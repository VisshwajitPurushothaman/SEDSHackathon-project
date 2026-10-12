import { Line } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';
import { swingOf, TILT_LIMIT_DEG } from '../lib/checks';
import { getCurrentFrame } from '../store/useMission';
import { ui } from '../theme/tokens';
import { Balloon } from './Balloon';
import { Gondola } from './Gondola';
import { Highlight, hoverProps } from './interaction';
import { Rocket } from './Rocket';
import { anchorRef, PIVOT_Y, shake } from './shared';

const DEG = Math.PI / 180;
const WEDGE_LEN = 6.3;

const cPass = new THREE.Color(ui.pass);
const cHold = new THREE.Color(ui.hold);
const cIdle = new THREE.Color(ui.faint);

/**
 * Translucent ±1.5° window under the suspension point (fixed in the world).
 * Green while the swinging assembly's axis is inside it.
 */
function TiltWedge() {
  const mat = useRef<THREE.MeshBasicMaterial>(null);
  const geom = useMemo(
    () => new THREE.CircleGeometry(WEDGE_LEN, 12, -Math.PI / 2 - TILT_LIMIT_DEG * DEG, 2 * TILT_LIMIT_DEG * DEG),
    [],
  );
  useFrame(() => {
    const f = getCurrentFrame();
    if (!mat.current) return;
    // Coloured by the swing AMPLITUDE (the gated quantity), not by the instantaneous angle.
    mat.current.color.copy(!f ? cIdle : swingOf(f) <= TILT_LIMIT_DEG ? cPass : cHold);
  });
  const edge = (sign: number): [number, number, number][] => [
    [0, 0, 0],
    [Math.sin(sign * TILT_LIMIT_DEG * DEG) * WEDGE_LEN, -Math.cos(TILT_LIMIT_DEG * DEG) * WEDGE_LEN, 0],
  ];
  return (
    <group position={[0, PIVOT_Y, 0.02]}>
      <mesh geometry={geom} {...hoverProps('tilt')}>
        <meshBasicMaterial ref={mat} transparent opacity={0.16} depthWrite={false} side={THREE.DoubleSide} />
        <Highlight id="tilt" thickness={0.01} />
      </mesh>
      <Line points={edge(-1)} color={ui.muted} lineWidth={0.8} transparent opacity={0.45} />
      <Line points={edge(1)} color={ui.muted} lineWidth={0.8} transparent opacity={0.45} />
      <object3D ref={anchorRef('tilt')} position={[0, -WEDGE_LEN, 0]} />
    </group>
  );
}

/**
 * Balloon + the swinging assembly (load line, gondola, rocket). The assembly
 * rotates by tilt_deg about the suspension point. A one-shot shake offsets
 * the whole vehicle (used when a leak is detected).
 */
export function Vehicle() {
  const swing = useRef<THREE.Group>(null);
  const shaker = useRef<THREE.Group>(null);

  useFrame(() => {
    const f = getCurrentFrame();
    if (swing.current) swing.current.rotation.z = (f?.tilt_deg ?? 0) * DEG;
    if (shaker.current) {
      const t = shake.start < 0 ? 1 : (performance.now() - shake.start) / 1000;
      if (t < 0.5) {
        const amp = 0.05 * (1 - t / 0.5);
        shaker.current.position.set((Math.random() - 0.5) * amp, (Math.random() - 0.5) * amp, 0);
      } else {
        shaker.current.position.set(0, 0, 0);
      }
    }
  });

  return (
    <group ref={shaker}>
      <Balloon />
      <TiltWedge />
      <group position={[0, PIVOT_Y, 0]}>
        <group ref={swing}>
          {/* Children are authored in vehicle coordinates; shift them back below the pivot. */}
          <group position={[0, -PIVOT_Y, 0]}>
            <Gondola />
            <Rocket />
          </group>
          {/* The assembly's axis, compared against the wedge. */}
          <Line points={[[0, 0, 0.03], [0, -WEDGE_LEN, 0.03]]} color={ui.text} lineWidth={1} dashed dashSize={0.12} gapSize={0.08} transparent opacity={0.5} />
        </group>
      </group>
    </group>
  );
}
