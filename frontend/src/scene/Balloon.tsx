import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';
import { scene } from '../theme/tokens';
import { PIVOT_Y } from './shared';

const RADIUS = 2.6;
const CENTER_Y = PIVOT_Y + 0.25 + RADIUS * 1.1;

const vertex = /* glsl */ `
  varying vec3 vN;
  varying vec3 vV;
  varying vec3 vP;
  void main() {
    vP = position;
    vN = normalize(normalMatrix * normal);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vV = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;

// Translucent latex: soft fresnel rim, faint gores, a little key-light shading.
const fragment = /* glsl */ `
  uniform vec3 uColor;
  uniform vec3 uRim;
  varying vec3 vN;
  varying vec3 vV;
  varying vec3 vP;
  void main() {
    // clamp: float error can push |dot| past 1, and pow() of a negative base is NaN,
    // which the bloom blur would spread into a fully black frame.
    float fres = pow(clamp(1.0 - abs(dot(vN, vV)), 0.0, 1.0), 2.4);
    float ang = atan(vP.z, vP.x + 1e-5); // offset avoids atan(0, 0) at the poles
    float gore = smoothstep(0.985, 1.0, abs(cos(ang * 6.0)));
    float lit = 0.55 + 0.45 * max(dot(vN, normalize(vec3(0.5, 0.8, 0.4))), 0.0);
    vec3 col = mix(uColor * lit, uRim, fres);
    float a = 0.07 + 0.55 * fres + 0.06 * gore;
    gl_FragColor = vec4(col, a);
  }
`;

/** Latex balloon above the stack. Gently "breathes"; does not swing with the gondola. */
export function Balloon() {
  const mesh = useRef<THREE.Mesh>(null);
  const reduced = useMemo(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches, []);
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: vertex,
        fragmentShader: fragment,
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
        uniforms: {
          uColor: { value: new THREE.Color(scene.balloon) },
          uRim: { value: new THREE.Color(scene.balloonRim) },
        },
      }),
    [],
  );

  useFrame(({ clock }) => {
    if (!mesh.current || reduced) return;
    const s = 1 + 0.012 * Math.sin(clock.elapsedTime * 0.9);
    mesh.current.scale.set(s, s * 1.1, s);
  });

  return (
    <group>
      <mesh ref={mesh} position={[0, CENTER_Y, 0]} scale={[1, 1.1, 1]} material={material} renderOrder={2}>
        <sphereGeometry args={[RADIUS, 64, 48]} />
      </mesh>
      {/* Neck down to the suspension point */}
      <mesh position={[0, PIVOT_Y + 0.17, 0]}>
        <cylinderGeometry args={[0.16, 0.05, 0.34, 20, 1, true]} />
        <meshStandardMaterial color={scene.balloon} transparent opacity={0.55} roughness={0.6} side={THREE.DoubleSide} />
      </mesh>
    </group>
  );
}
