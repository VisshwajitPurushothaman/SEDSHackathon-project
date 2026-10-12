import { Html } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useRef } from 'react';
import * as THREE from 'three';
import { CEILING_ALTITUDE_M, RELEASE_ALTITUDE_M } from '../lib/checks';
import { getCurrentFrame } from '../store/useMission';
import { ui } from '../theme/tokens';
import { Highlight, hoverProps } from './interaction';
import { altToRailY, anchorRef, RAIL_X, RAIL_Y0, RAIL_Y1 } from './shared';

const cIdle = new THREE.Color(ui.faint);
const cPass = new THREE.Color(ui.pass);

/** Vertical altitude rail beside the vehicle with the 25 km release band. */
export function AltitudeRail() {
  const marker = useRef<THREE.Group>(null);
  const bandMat = useRef<THREE.MeshStandardMaterial>(null);
  const label = useRef<HTMLSpanElement>(null);

  const bandY0 = altToRailY(RELEASE_ALTITUDE_M);
  const bandY1 = altToRailY(CEILING_ALTITUDE_M);

  useFrame(() => {
    const f = getCurrentFrame();
    const alt = f?.altitude_m ?? 0;
    if (marker.current) marker.current.position.y = altToRailY(alt);
    if (label.current) label.current.textContent = f ? `${(alt / 1000).toFixed(2)} km` : '-- km';
    if (bandMat.current) {
      const reached = alt >= RELEASE_ALTITUDE_M;
      bandMat.current.emissive.copy(reached ? cPass : cIdle);
      bandMat.current.color.copy(bandMat.current.emissive);
      bandMat.current.emissiveIntensity = reached ? 1.6 : 0.5;
      bandMat.current.opacity = reached ? 0.55 : 0.22;
    }
  });

  const ticks = [0, 5000, 10000, 15000, 20000, 25000, 30000];

  return (
    <group position={[RAIL_X, 0, 0]}>
      {/* Rail */}
      <mesh position={[0, (RAIL_Y0 + RAIL_Y1) / 2, 0]} {...hoverProps('altitude')}>
        <boxGeometry args={[0.03, RAIL_Y1 - RAIL_Y0, 0.03]} />
        <meshStandardMaterial color={ui.border} metalness={0.3} roughness={0.6} />
        <Highlight id="altitude" thickness={0.01} />
      </mesh>

      {/* Release band (25–30 km) */}
      <mesh position={[0, (bandY0 + bandY1) / 2, 0]} {...hoverProps('altitude')}>
        <boxGeometry args={[0.1, bandY1 - bandY0, 0.1]} />
        <meshStandardMaterial ref={bandMat} transparent opacity={0.22} toneMapped={false} depthWrite={false} />
      </mesh>
      <Html position={[0.12, bandY1 - 0.22, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
        <span className="scene-label scene-label--muted whitespace-nowrap">release band</span>
      </Html>

      {/* Ticks every 5 km */}
      {ticks.map((m) => (
        <group key={m} position={[0, altToRailY(m), 0]}>
          <mesh>
            <boxGeometry args={[0.12, 0.012, 0.012]} />
            <meshBasicMaterial color={ui.muted} />
          </mesh>
          <Html position={[-0.14, 0, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
            <span className="scene-label scene-label--muted -translate-x-full -translate-y-1/2 block">{m / 1000}</span>
          </Html>
        </group>
      ))}

      {/* Climbing marker */}
      <group ref={marker} position={[0, RAIL_Y0, 0]}>
        <mesh rotation={[0, 0, -Math.PI / 2]} position={[-0.09, 0, 0]}>
          <coneGeometry args={[0.05, 0.1, 3]} />
          <meshStandardMaterial color={ui.info} emissive={ui.info} emissiveIntensity={1.2} toneMapped={false} />
        </mesh>
        <object3D ref={anchorRef('altitude')} />
        <Html position={[0.1, 0, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
          <span ref={label} className="scene-label -translate-y-1/2 block text-info" />
        </Html>
      </group>
    </group>
  );
}
