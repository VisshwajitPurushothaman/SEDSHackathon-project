import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useMission } from '../store/useMission';
import { scene } from '../theme/tokens';
import { ParticleField, type Spawn } from './Particles';
import { IGNITION, ignitionElapsed, smoothstep } from './shared';

const NOZZLE_EXIT_Y = -1.52;

/** 0..1 plume strength at the current moment of the ignition sequence. */
function plumeLevel(): number {
  const e = ignitionElapsed(useMission.getState().commit);
  if (e < 0) return 0;
  return smoothstep(IGNITION.plumeStart, IGNITION.plumeStart + 0.5, e);
}

/**
 * Exhaust plume shown only after the operator commits (frontend-only
 * simulation): an emissive flame core plus ~700 additive particles, both
 * bright enough to bloom. Lives in rocket-local space.
 */
export function Plume() {
  const core = useRef<THREE.Mesh>(null);
  const light = useRef<THREE.PointLight>(null);
  const coreMat = useRef<THREE.MeshBasicMaterial>(null);
  // Tip-down cone with its base at the origin, so scaling Y stretches it away from the nozzle.
  const coneGeom = useMemo(() => new THREE.ConeGeometry(0.12, 1.1, 24, 1, true).rotateX(Math.PI).translate(0, -0.55, 0), []);
  const coreColor = useMemo(() => new THREE.Color(scene.plumeCore).multiplyScalar(4), []);

  useFrame(({ clock }) => {
    const k = plumeLevel();
    const flicker = 0.9 + 0.1 * Math.sin(clock.elapsedTime * 47) * Math.sin(clock.elapsedTime * 13);
    if (core.current && coreMat.current) {
      core.current.visible = k > 0.01;
      core.current.scale.set(1, 0.3 + 0.9 * k * flicker, 1);
      coreMat.current.opacity = 0.9 * k;
    }
    if (light.current) light.current.intensity = 14 * k * flicker;
  });

  const emit = (s: Spawn) => {
    const r = Math.random() * 0.07;
    const a = Math.random() * Math.PI * 2;
    s.pos.set(Math.cos(a) * r, NOZZLE_EXIT_Y, Math.sin(a) * r);
    s.vel.set((Math.random() - 0.5) * 0.9, -6 - Math.random() * 4, (Math.random() - 0.5) * 0.9);
    s.life = 0.35 + Math.random() * 0.35;
  };

  return (
    <group>
      {/* Flame core: a cone hanging from the nozzle exit, scaled by plume strength. */}
      <mesh ref={core} position={[0, NOZZLE_EXIT_Y, 0]} geometry={coneGeom} visible={false}>
        <meshBasicMaterial
          ref={coreMat}
          color={coreColor}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
          side={THREE.DoubleSide}
        />
      </mesh>
      <pointLight ref={light} position={[0, NOZZLE_EXIT_Y - 0.4, 0]} color={scene.plumeEdge} intensity={0} distance={6} decay={2} />
      <ParticleField
        count={700}
        rate={() => 1400 * plumeLevel()}
        emit={emit}
        colorA={scene.plumeCore}
        colorB={scene.plumeEdge}
        intensity={() => 2.4 * plumeLevel() + 0.01}
        size={0.16}
        drag={0.35}
      />
    </group>
  );
}
