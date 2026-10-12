import { Html, Text } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';
import { START_TANK_PRES_BAR } from '../lib/checks';
import { getCurrentFrame, useMission } from '../store/useMission';
import { scene, ui } from '../theme/tokens';
import { Highlight, Optional, hoverProps } from './interaction';
import { Plume } from './Plume';
import { anchorRef, driftAt, IGNITION, ignitionElapsed, pitchAt, riseAt, ROCKET_R, smoothstep, TANK_Y } from './shared';
import { VentJet } from './VentJet';

const R = ROCKET_R;

// Section layout along the body (rocket-local Y, nozzle exit at -1.5, nose tip at 1.72).
const NOZZLE = { y0: -1.5, y1: -1.28 };
const MOTOR = { y0: -1.28, y1: -0.48 };
const INTERSTAGE = { y0: -0.48, y1: -0.36 };
const TANK = { y0: -0.36, y1: 0.5 };
const PAYLOAD = { y0: 0.5, y1: 1.08 };
const NOSE = { y0: 1.08, y1: 1.72 };
const mid = (s: { y0: number; y1: number }) => (s.y0 + s.y1) / 2;
const len = (s: { y0: number; y1: number }) => s.y1 - s.y0;

const GAUGE_R = 0.3;
/** The gauge group is rotated so a full arc ends front-right, facing the default camera. */
const GAUGE_Y = 0.32;

const cWarm = new THREE.Color(scene.motorWarm);
const cCold = new THREE.Color(scene.motorCold);
const cHot = new THREE.Color(scene.plumeEdge);
const cPass = new THREE.Color(ui.pass);
const cFail = new THREE.Color(ui.fail);
const cHold = new THREE.Color(ui.hold);
const cArmed = new THREE.Color(ui.armed);
const cIdle = new THREE.Color(ui.faint);

/** Motor casing colour map: 20 °C warm amber → 0 °C and below icy blue. */
export function motorColor(tempC: number, out: THREE.Color) {
  return out.copy(cCold).lerp(cWarm, smoothstep(0, 20, tempC));
}

function Fins() {
  const geom = useMemo(() => {
    const shape = new THREE.Shape();
    shape.moveTo(0, 0);
    shape.lineTo(0.2, -0.12);
    shape.lineTo(0.2, -0.3);
    shape.lineTo(0, -0.26);
    shape.closePath();
    const g = new THREE.ExtrudeGeometry(shape, { depth: 0.014, bevelEnabled: false });
    g.translate(0, 0, -0.007);
    return g;
  }, []);
  return (
    <group position={[0, MOTOR.y0 + 0.24, 0]}>
      {[0, 1, 2, 3].map((k) => (
        <group key={k} rotation={[0, (k * Math.PI) / 2 + Math.PI / 4, 0]}>
          <mesh geometry={geom} position={[R - 0.01, 0, 0]} castShadow>
            <meshStandardMaterial color={scene.graphite} metalness={0.5} roughness={0.45} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

function Stripe({ y }: { y: number }) {
  return (
    <mesh position={[0, y, 0]}>
      <cylinderGeometry args={[R + 0.003, R + 0.003, 0.018, 48, 1, true]} />
      <meshStandardMaterial color={scene.accentStripe} metalness={0.2} roughness={0.5} />
    </mesh>
  );
}

/** Ring around the tank: slow green pulse while sealed, red on leak. */
function IntegrityRing() {
  const mat = useRef<THREE.MeshStandardMaterial>(null);
  const reduced = useRef(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useFrame(({ clock }) => {
    const f = getCurrentFrame();
    const m = mat.current;
    if (!m) return;
    if (!f) {
      m.emissive.copy(cIdle);
      m.emissiveIntensity = 0.3;
    } else if (f.leak_detected) {
      m.emissive.copy(cFail);
      m.emissiveIntensity = 2.2;
    } else {
      m.emissive.copy(cPass);
      const pulse = reduced.current ? 0.5 : 0.5 + 0.5 * Math.sin(clock.elapsedTime * 2.2);
      m.emissiveIntensity = 0.6 + 0.9 * pulse;
    }
    m.color.copy(m.emissive);
  });
  return (
    <mesh position={[0, TANK_Y, 0]} rotation={[Math.PI / 2, 0, 0]} {...hoverProps('tank')}>
      <torusGeometry args={[R + 0.03, 0.012, 12, 72]} />
      <meshStandardMaterial ref={mat} toneMapped={false} metalness={0} roughness={0.6} />
      <Highlight id="tank" thickness={0.01} />
    </mesh>
  );
}

/** Circular gauge around the tank: arc length = tank_pres_bar / 200; a label rides the arc end. */
function PressureGauge() {
  const arc = useRef<THREE.Mesh>(null);
  const labelPivot = useRef<THREE.Group>(null);
  const label = useRef<HTMLSpanElement>(null);
  const lastArc = useRef(-1);

  useFrame(() => {
    const f = getCurrentFrame();
    const frac = f ? Math.min(Math.max(f.tank_pres_bar / START_TANK_PRES_BAR, 0), 1) : 0;
    const theta = Math.max(frac * Math.PI * 2, 0.0001);
    // Rebuild the arc only when it visibly changes (pressure moves slowly).
    if (arc.current && Math.abs(theta - lastArc.current) > 0.002) {
      arc.current.geometry.dispose();
      arc.current.geometry = new THREE.TorusGeometry(GAUGE_R, 0.011, 8, 120, theta);
      lastArc.current = theta;
    }
    if (labelPivot.current) labelPivot.current.position.set(Math.cos(theta) * (GAUGE_R + 0.2), Math.sin(theta) * (GAUGE_R + 0.2), 0);
    if (label.current) label.current.textContent = f ? `${f.tank_pres_bar.toFixed(1)} bar` : '-- bar';
  });

  // Rotate the XY-plane torus to lie horizontally, starting at the back and sweeping round the front.
  return (
    <group position={[0, GAUGE_Y, 0]} rotation={[Math.PI / 2, 0, Math.PI * 0.3]} {...hoverProps('pressure')}>
      <mesh>
        <torusGeometry args={[GAUGE_R, 0.004, 6, 120]} />
        <meshBasicMaterial color={ui.border} transparent opacity={0.9} />
      </mesh>
      <mesh ref={arc}>
        <torusGeometry args={[GAUGE_R, 0.011, 8, 120, Math.PI * 2]} />
        <meshStandardMaterial color={ui.info} emissive={ui.info} emissiveIntensity={0.9} toneMapped={false} />
        <Highlight id="pressure" thickness={0.008} />
      </mesh>
      <group ref={labelPivot}>
        <object3D ref={anchorRef('pressure')} />
        <Html center zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
          <span ref={label} className="scene-label text-info" />
        </Html>
      </group>
    </group>
  );
}

/** Nose cone status light: amber NO-GO, green ARMED, red flashing ABORT. */
function NoseLight() {
  const mat = useRef<THREE.MeshStandardMaterial>(null);
  const abortAt = useRef(-1);
  useFrame(({ clock }) => {
    const m = mat.current;
    if (!m) return;
    const f = getCurrentFrame();
    const committed = useMission.getState().commit.status === 'committed';
    if (!f) {
      m.emissive.copy(cIdle);
      m.emissiveIntensity = 0.4;
      abortAt.current = -1;
    } else if (f.is_abort) {
      if (abortAt.current < 0) abortAt.current = clock.elapsedTime;
      const t = clock.elapsedTime - abortAt.current;
      // Three hard flashes, then steady red.
      const flash = t < 1.8 ? (Math.sin(t * Math.PI * 3.33) > 0 ? 1 : 0.15) : 1;
      m.emissive.copy(cFail);
      m.emissiveIntensity = 4 * flash;
    } else if (f.gate_status === 'ARMED' || committed) {
      abortAt.current = -1;
      m.emissive.copy(cArmed);
      m.emissiveIntensity = 5;
    } else {
      abortAt.current = -1;
      m.emissive.copy(cHold);
      m.emissiveIntensity = 1.6;
    }
    m.color.copy(m.emissive);
  });
  return (
    <mesh position={[0, NOSE.y1 + 0.01, 0]} {...hoverProps('gate')}>
      <sphereGeometry args={[0.035, 20, 16]} />
      <meshStandardMaterial ref={mat} toneMapped={false} />
      <object3D ref={anchorRef('gate')} />
      <Highlight id="gate" thickness={0.01} />
    </mesh>
  );
}

/** The launch vehicle on its rail. Rises out of the gondola after the operator commit. */
export function Rocket() {
  const root = useRef<THREE.Group>(null);
  const motorMat = useRef<THREE.MeshStandardMaterial>(null);
  const tmp = useMemo(() => new THREE.Color(), []);

  useFrame(() => {
    const f = getCurrentFrame();
    const e = ignitionElapsed(useMission.getState().commit);

    if (motorMat.current) {
      const m = motorMat.current;
      motorColor(f?.motor_temp_c ?? 20, tmp);
      if (e >= 0) {
        // Ignition: casing heats toward the plume colour.
        const k = smoothstep(0, IGNITION.glowEnd, e);
        tmp.lerp(cHot, k);
        m.emissiveIntensity = 0.35 + 2.4 * k;
      } else {
        m.emissiveIntensity = f ? 0.35 : 0.12;
      }
      m.emissive.copy(tmp);
    }

    if (root.current) {
      const rise = e >= 0 ? riseAt(e) : 0;
      root.current.position.set(driftAt(rise), rise, 0);
      root.current.rotation.z = -pitchAt(rise);
      root.current.visible = rise < 80;
    }
  });

  return (
    <group ref={root}>
      {/* Nozzle */}
      <mesh position={[0, mid(NOZZLE), 0]}>
        <cylinderGeometry args={[0.09, 0.14, len(NOZZLE), 32, 1, true]} />
        <meshStandardMaterial color={scene.darkMetal} metalness={0.85} roughness={0.3} side={THREE.DoubleSide} />
      </mesh>

      {/* Motor section (colour-mapped by motor_temp_c) */}
      <mesh position={[0, mid(MOTOR), 0]} {...hoverProps('motor')}>
        <cylinderGeometry args={[R, R, len(MOTOR), 48]} />
        <meshStandardMaterial ref={motorMat} color={scene.graphite} metalness={0.6} roughness={0.38} emissiveIntensity={0.35} />
        <Highlight id="motor" />
      </mesh>
      <object3D ref={anchorRef('motor')} position={[0, mid(MOTOR), R]} />
      <Fins />

      {/* Interstage */}
      <mesh position={[0, mid(INTERSTAGE), 0]}>
        <cylinderGeometry args={[R, R, len(INTERSTAGE), 48]} />
        <meshStandardMaterial color={scene.graphite} metalness={0.5} roughness={0.5} />
      </mesh>
      <Stripe y={INTERSTAGE.y1} />

      {/* Pressurant tank band */}
      <mesh position={[0, mid(TANK), 0]} {...hoverProps('tank')}>
        <cylinderGeometry args={[R, R, len(TANK), 48]} />
        <meshStandardMaterial color={scene.liveryWhite} metalness={0.35} roughness={0.32} />
        <Highlight id="tank" />
      </mesh>
      <object3D ref={anchorRef('tank')} position={[Math.cos(0.55) * R, TANK_Y, Math.sin(0.55) * R]} />
      <IntegrityRing />
      <PressureGauge />
      <VentJet />
      <Stripe y={TANK.y1} />

      {/* Payload bay with the decal */}
      <mesh position={[0, mid(PAYLOAD), 0]}>
        <cylinderGeometry args={[R, R, len(PAYLOAD), 48]} />
        <meshStandardMaterial color={scene.liveryWhite} metalness={0.3} roughness={0.35} />
      </mesh>
      <Optional>
        <Text
          position={[0, mid(PAYLOAD), R + 0.002]}
          rotation={[0, 0, Math.PI / 2]}
          fontSize={0.062}
          letterSpacing={0.08}
          color={scene.graphite}
          anchorX="center"
          anchorY="middle"
        >
          AERO-FIRE
        </Text>
      </Optional>

      {/* Nose cone */}
      <mesh position={[0, mid(NOSE), 0]}>
        <coneGeometry args={[R, len(NOSE), 48]} />
        <meshStandardMaterial color={scene.liveryWhite} metalness={0.3} roughness={0.3} />
      </mesh>
      <NoseLight />

      <Plume />
    </group>
  );
}
