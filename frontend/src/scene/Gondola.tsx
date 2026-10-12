import { Html, Line } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useRef } from 'react';
import * as THREE from 'three';
import { MASS_CAUTION_G } from '../lib/checks';
import { getCurrentFrame, useMission } from '../store/useMission';
import { scene, ui } from '../theme/tokens';
import { Highlight, hoverProps } from './interaction';
import { anchorRef, IGNITION, ignitionElapsed, PIVOT_Y, ROCKET_R, smoothstep, TANK_Y } from './shared';

const W = 0.75; // half-width of the gondola frame
const Y0 = -1.72;
const Y1 = 1.9;
const BRIDLE_Y = 2.75;
const BEAM = 0.032;

/** One straight frame member between two points. */
function Member({ a, b }: { a: [number, number, number]; b: [number, number, number] }) {
  const va = new THREE.Vector3(...a);
  const vb = new THREE.Vector3(...b);
  const length = va.distanceTo(vb);
  const midp = va.clone().add(vb).multiplyScalar(0.5);
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), vb.clone().sub(va).normalize());
  return (
    <mesh position={midp} quaternion={q}>
      <boxGeometry args={[BEAM, length, BEAM]} />
      <meshStandardMaterial color={scene.gondolaFrame} metalness={0.75} roughness={0.35} />
    </mesh>
  );
}

function Frame() {
  const corners: [number, number][] = [
    [-W, -W],
    [W, -W],
    [W, W],
    [-W, W],
  ];
  const levels = [Y0, -0.1, Y1];
  const members: { a: [number, number, number]; b: [number, number, number] }[] = [];
  for (const [x, z] of corners) members.push({ a: [x, Y0, z], b: [x, Y1, z] });
  for (const y of levels) {
    for (let i = 0; i < 4; i++) {
      const [x1, z1] = corners[i];
      const [x2, z2] = corners[(i + 1) % 4];
      members.push({ a: [x1, y, z1], b: [x2, y, z2] });
    }
  }
  // Diagonal bracing on the back and side faces (the front stays open to the camera).
  members.push({ a: [-W, Y0, -W], b: [W, -0.1, -W] }, { a: [-W, -0.1, -W], b: [W, Y1, -W] });
  members.push({ a: [-W, Y0, -W], b: [-W, -0.1, W] }, { a: [W, Y0, -W], b: [W, -0.1, W] });
  return (
    <group>
      {members.map((m, i) => (
        <Member key={i} a={m.a} b={m.b} />
      ))}
      {/* Deck */}
      <mesh position={[0, Y0 - 0.02, 0]} receiveShadow>
        <boxGeometry args={[W * 2, 0.03, W * 2]} />
        <meshStandardMaterial color={scene.graphite} metalness={0.4} roughness={0.7} />
      </mesh>
    </group>
  );
}

/** Launch rail behind the rocket and the two release clamps that open on commit. */
function RailAndClamps() {
  const clamps = useRef<THREE.Group[]>([]);
  useFrame(() => {
    const e = ignitionElapsed(useMission.getState().commit);
    const k = e >= 0 ? smoothstep(IGNITION.clampsOpen, IGNITION.clampsOpen + 0.3, e) : 0;
    clamps.current.forEach((g, i) => {
      if (!g) return;
      const side = i % 2 === 0 ? -1 : 1;
      g.position.x = side * (ROCKET_R + 0.035 + 0.14 * k);
      g.rotation.y = side * 0.6 * k;
    });
  });
  const ys = [-0.95, 0.72];
  return (
    <group>
      <mesh position={[0, 0.05, -0.33]}>
        <boxGeometry args={[0.07, 3.5, 0.07]} />
        <meshStandardMaterial color={scene.rail} metalness={0.7} roughness={0.4} />
      </mesh>
      {ys.flatMap((y, row) =>
        [0, 1].map((s) => (
          <group
            key={`${row}-${s}`}
            ref={(g) => {
              if (g) clamps.current[row * 2 + s] = g;
            }}
            position={[0, y, -0.15]}
          >
            <mesh>
              <boxGeometry args={[0.045, 0.09, 0.36]} />
              <meshStandardMaterial color={scene.darkMetal} metalness={0.8} roughness={0.35} />
            </mesh>
          </group>
        )),
      )}
    </group>
  );
}

/** Floating mass-balance readout tied to the tank by a leader line. */
function MassReadout() {
  const box = useRef<HTMLDivElement>(null);
  const value = useRef<HTMLSpanElement>(null);
  const group = useRef<THREE.Group>(null);
  const anchor: [number, number, number] = [1.12, 0.52, 0.42];
  const tankPoint: [number, number, number] = [Math.cos(0.9) * ROCKET_R, TANK_Y + 0.05, Math.sin(0.9) * ROCKET_R];

  useFrame(() => {
    const f = getCurrentFrame();
    const e = ignitionElapsed(useMission.getState().commit);
    if (group.current) group.current.visible = e < IGNITION.liftoff;
    if (!value.current || !box.current) return;
    value.current.textContent = f ? f.mass_loss_g.toFixed(2) : '--';
    const tone = !f ? 'idle' : f.leak_detected ? 'fail' : f.mass_loss_g > MASS_CAUTION_G ? 'hold' : 'ok';
    box.current.dataset.tone = tone;
  });

  return (
    <group ref={group}>
      <Line points={[tankPoint, anchor]} color={ui.muted} lineWidth={1} transparent opacity={0.7} />
      <mesh position={tankPoint}>
        <sphereGeometry args={[0.012, 8, 8]} />
        <meshBasicMaterial color={ui.muted} />
      </mesh>
      <group position={anchor}>
        <object3D ref={anchorRef('mass')} />
        <mesh {...hoverProps('mass')}>
          <sphereGeometry args={[0.09, 8, 8]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
          <Highlight id="mass" thickness={0.006} />
        </mesh>
        <Html zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
          <div ref={box} className="mass-readout" data-tone="idle" title="Ground estimate of helium lost (from noisy P and T). The leak decision is a slope test, not a mass threshold.">
            <span className="mass-readout__label">mass lost</span>
            <span>
              <span ref={value} className="mass-readout__value">
                --
              </span>
              <span className="mass-readout__unit"> g</span>
            </span>
          </div>
        </Html>
      </group>
    </group>
  );
}

/** Gondola frame, bridle and load line up to the suspension point, rail, clamps, readout. */
export function Gondola() {
  const top = PIVOT_Y;
  return (
    <group>
      <Frame />
      <RailAndClamps />
      <MassReadout />
      {/* Bridle from the four top corners to a ring, then the load line up to the balloon. */}
      {[
        [-W, -W],
        [W, -W],
        [W, W],
        [-W, W],
      ].map(([x, z], i) => (
        <Line key={i} points={[[x, Y1, z], [0, BRIDLE_Y, 0]]} color={scene.loadLine} lineWidth={1} transparent opacity={0.8} />
      ))}
      <mesh position={[0, BRIDLE_Y, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.05, 0.012, 8, 20]} />
        <meshStandardMaterial color={scene.gondolaFrame} metalness={0.8} roughness={0.3} />
      </mesh>
      <Line points={[[0, BRIDLE_Y, 0], [0, top, 0]]} color={scene.loadLine} lineWidth={1.4} />
    </group>
  );
}
