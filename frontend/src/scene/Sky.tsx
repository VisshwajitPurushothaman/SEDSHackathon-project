import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';
import { getCurrentFrame, useMission } from '../store/useMission';
import { scene } from '../theme/tokens';
import { anchorRef, smoothstep } from './shared';

const SKY_R = 90;

const skyVertex = /* glsl */ `
  varying vec3 vDir;
  void main() {
    vDir = normalize(position);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const skyFragment = /* glsl */ `
  uniform vec3 uLowTop;
  uniform vec3 uLowHorizon;
  uniform vec3 uHighTop;
  uniform vec3 uHighHorizon;
  uniform float uK;
  varying vec3 vDir;
  void main() {
    vec3 top = mix(uLowTop, uHighTop, uK);
    vec3 horizon = mix(uLowHorizon, uHighHorizon, uK);
    float h = smoothstep(-0.25, 0.65, vDir.y);
    vec3 col = mix(horizon, top, h);
    // Darker below the horizon.
    col *= mix(0.55, 1.0, smoothstep(-0.6, 0.0, vDir.y));
    gl_FragColor = vec4(col, 1.0);
  }
`;

/** Soft horizontal haze band texture, generated on a canvas (no image files). */
function makeHazeTexture(seed: number): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = 512;
  c.height = 64;
  const g = c.getContext('2d')!;
  let s = seed;
  const rand = () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
  for (let i = 0; i < 26; i++) {
    const x = rand() * 512;
    const y = 32 + (rand() - 0.5) * 18;
    const r = 30 + rand() * 70;
    const grad = g.createRadialGradient(x, y, 0, x, y, r);
    grad.addColorStop(0, 'rgba(255,255,255,0.32)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad;
    g.beginPath();
    g.ellipse(x, y, r, r * 0.28, 0, 0, Math.PI * 2);
    g.fill();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const HAZE_COUNT = 7;
const HAZE_SPAN = 30; // vertical wrap range in scene units
const HAZE_SCROLL = 1.7; // scene units per km climbed

/**
 * The atmosphere check lives in the sky itself: the gradient darkens with
 * altitude, stars fade in above ~15 km, haze layers pass downward while
 * climbing and thin out above the tropopause.
 */
export function Sky() {
  const hovered = useMission((s) => s.hovered === 'atmosphere');
  const skyMat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: skyVertex,
        fragmentShader: skyFragment,
        side: THREE.BackSide,
        depthWrite: false,
        uniforms: {
          uLowTop: { value: new THREE.Color(scene.skyLowTop) },
          uLowHorizon: { value: new THREE.Color(scene.skyLowHorizon) },
          uHighTop: { value: new THREE.Color(scene.skyHighTop) },
          uHighHorizon: { value: new THREE.Color(scene.skyHighHorizon) },
          uK: { value: 0 },
        },
      }),
    [],
  );

  const starGeom = useMemo(() => {
    const n = 900;
    const p = new Float32Array(n * 3);
    let s = 7;
    const rand = () => {
      s = (s * 16807) % 2147483647;
      return s / 2147483647;
    };
    for (let i = 0; i < n; i++) {
      const u = rand() * Math.PI * 2;
      const y = rand() * 1.1 - 0.15;
      const r = Math.sqrt(1 - Math.min(y * y, 1));
      p.set([Math.cos(u) * r * 80, y * 80, Math.sin(u) * r * 80], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(p, 3));
    return g;
  }, []);
  const starMat = useRef<THREE.PointsMaterial>(null);

  const haze = useMemo(
    () =>
      Array.from({ length: HAZE_COUNT }, (_, i) => ({
        base: (i / HAZE_COUNT) * HAZE_SPAN,
        z: -11 - (i % 3) * 3,
        x: ((i * 37) % 9) - 4,
        texture: makeHazeTexture(1000 + i * 97),
      })),
    [],
  );
  const hazeRefs = useRef<(THREE.Mesh | null)[]>([]);

  useFrame(() => {
    const alt = getCurrentFrame()?.altitude_m ?? 0;
    skyMat.uniforms.uK.value = smoothstep(0, 24000, alt);
    if (starMat.current) starMat.current.opacity = 0.9 * smoothstep(12000, 19000, alt);

    const hazeK = 1 - smoothstep(9000, 17000, alt);
    const km = alt / 1000;
    hazeRefs.current.forEach((m, i) => {
      if (!m) return;
      const h = haze[i];
      // Wrap into [-12, 18) so layers stream downward as the vehicle climbs.
      const y = ((((h.base - km * HAZE_SCROLL) % HAZE_SPAN) + HAZE_SPAN) % HAZE_SPAN) - 12;
      m.position.set(h.x, y, h.z);
      const edge = smoothstep(-12, -8, y) * (1 - smoothstep(13, 18, y));
      (m.material as THREE.MeshBasicMaterial).opacity = (hovered ? 0.7 : 0.34) * hazeK * edge;
    });
  });

  return (
    <group>
      <mesh material={skyMat} renderOrder={-10}>
        <sphereGeometry args={[SKY_R, 32, 24]} />
      </mesh>
      <points geometry={starGeom} renderOrder={-9}>
        <pointsMaterial ref={starMat} color={scene.star} size={1.6} sizeAttenuation={false} transparent opacity={0} depthWrite={false} />
      </points>
      {haze.map((h, i) => (
        <mesh key={i} ref={(m) => (hazeRefs.current[i] = m)} renderOrder={-8}>
          <planeGeometry args={[46, 3.2]} />
          <meshBasicMaterial map={h.texture} color={scene.haze} transparent opacity={0} depthWrite={false} />
        </mesh>
      ))}
      <object3D ref={anchorRef('atmosphere')} position={[-3.2, 4.6, -4]} />
    </group>
  );
}
