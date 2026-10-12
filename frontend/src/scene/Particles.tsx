import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';

/** Spawn parameters written by an emitter for one particle. */
export interface Spawn {
  pos: THREE.Vector3;
  vel: THREE.Vector3;
  /** Lifetime in seconds. */
  life: number;
}

/** A constant, or a getter read every frame (so values can animate without React renders). */
type Num = number | (() => number);
const read = (n: Num) => (typeof n === 'function' ? n() : n);

export interface ParticleFieldProps {
  count: number;
  /** Particles per second to spawn this frame (0 = off). Called every frame. */
  rate: () => number;
  /** Fill `s` for a new particle. */
  emit: (s: Spawn) => void;
  colorA: THREE.ColorRepresentation;
  colorB: THREE.ColorRepresentation;
  /** Colour multiplier; > 1 pushes the particles past the bloom threshold. */
  intensity?: Num;
  size?: Num;
  opacity?: Num;
  /** Velocity multiplier per second (1 = no drag). */
  drag?: number;
  gravity?: number;
}

const vertex = /* glsl */ `
  attribute float aAge;
  uniform float uSize;
  uniform float uPixelRatio;
  varying float vAge;
  void main() {
    vAge = aAge;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    float grow = mix(0.55, 1.9, aAge);
    gl_PointSize = aAge >= 1.0 ? 0.0 : uSize * grow * uPixelRatio * (220.0 / -mv.z);
  }
`;

const fragment = /* glsl */ `
  uniform vec3 uColorA;
  uniform vec3 uColorB;
  uniform float uIntensity;
  uniform float uOpacity;
  varying float vAge;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float soft = 1.0 - smoothstep(0.0, 0.5, d);
    vec3 col = mix(uColorA, uColorB, vAge) * uIntensity;
    gl_FragColor = vec4(col, soft * (1.0 - vAge) * uOpacity);
  }
`;

/**
 * A pool of point particles simulated on the CPU (positions + age), drawn in
 * one draw call with additive blending. Dead particles have age 1 and size 0.
 */
export function ParticleField({
  count,
  rate,
  emit,
  colorA,
  colorB,
  intensity = 1,
  size = 0.12,
  opacity = 1,
  drag = 0.6,
  gravity = 0,
}: ParticleFieldProps) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aAge', new THREE.BufferAttribute(new Float32Array(count).fill(1), 1).setUsage(THREE.DynamicDrawUsage));
    return g;
  }, [count]);

  const sim = useMemo(
    () => ({ vel: new Float32Array(count * 3), life: new Float32Array(count).fill(1), cursor: 0, carry: 0 }),
    [count],
  );

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: vertex,
        fragmentShader: fragment,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: {
          uSize: { value: read(size) },
          uPixelRatio: { value: 1 },
          uColorA: { value: new THREE.Color(colorA) },
          uColorB: { value: new THREE.Color(colorB) },
          uIntensity: { value: read(intensity) },
          uOpacity: { value: read(opacity) },
        },
      }),
    // Uniform values are refreshed every frame below; build the material once.
    [],
  );

  const spawn = useRef<Spawn>({ pos: new THREE.Vector3(), vel: new THREE.Vector3(), life: 1 }).current;

  useFrame((state, rawDt) => {
    const dt = Math.min(rawDt, 1 / 20);
    const pos = geom.attributes.position as THREE.BufferAttribute;
    const age = geom.attributes.aAge as THREE.BufferAttribute;
    const p = pos.array as Float32Array;
    const a = age.array as Float32Array;
    const v = sim.vel;
    const dragK = Math.pow(drag, dt);

    material.uniforms.uPixelRatio.value = state.gl.getPixelRatio();
    material.uniforms.uSize.value = read(size);
    material.uniforms.uIntensity.value = read(intensity);
    material.uniforms.uOpacity.value = read(opacity);

    let alive = 0;
    for (let i = 0; i < count; i++) {
      if (a[i] >= 1) continue;
      alive++;
      const j = i * 3;
      a[i] = Math.min(1, a[i] + dt / sim.life[i]);
      v[j + 1] -= gravity * dt;
      v[j] *= dragK;
      v[j + 1] *= dragK;
      v[j + 2] *= dragK;
      p[j] += v[j] * dt;
      p[j + 1] += v[j + 1] * dt;
      p[j + 2] += v[j + 2] * dt;
    }

    // Spawn: recycle slots round-robin.
    sim.carry += Math.max(0, rate()) * dt;
    let n = Math.floor(sim.carry);
    sim.carry -= n;
    while (n-- > 0) {
      const i = sim.cursor;
      sim.cursor = (sim.cursor + 1) % count;
      emit(spawn);
      const j = i * 3;
      p[j] = spawn.pos.x;
      p[j + 1] = spawn.pos.y;
      p[j + 2] = spawn.pos.z;
      v[j] = spawn.vel.x;
      v[j + 1] = spawn.vel.y;
      v[j + 2] = spawn.vel.z;
      sim.life[i] = spawn.life;
      a[i] = 0;
      alive++;
    }

    if (alive > 0 || sim.carry > 0) {
      pos.needsUpdate = true;
      age.needsUpdate = true;
    }
  });

  return <points geometry={geom} material={material} frustumCulled={false} />;
}
