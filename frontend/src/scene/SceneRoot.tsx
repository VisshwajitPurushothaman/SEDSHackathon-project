import { Environment, Lightformer } from '@react-three/drei';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Bloom, EffectComposer, ToneMapping } from '@react-three/postprocessing';
import { ToneMappingMode } from 'postprocessing';
import { Suspense, useEffect, useMemo, useState } from 'react';
import * as THREE from 'three';
import { usePageVisible, useReducedMotionPref } from '../hooks/useMissionRuntime';
import { selectFrame, useMission } from '../store/useMission';
import { scene } from '../theme/tokens';
import { AltitudeRail } from './AltitudeRail';
import { CameraDirector, POSES } from './CameraDirector';
import { Boundary } from './interaction';
import { anchors, screenAnchor } from './shared';
import { Sky } from './Sky';
import { Vehicle } from './Vehicle';

/** Re-render on any store change while in demand mode (hover, scrub, state flips). */
function Invalidator() {
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => useMission.subscribe(() => invalidate()), [invalidate]);
  return null;
}

/**
 * Compiles every material in the scene with KHR_parallel_shader_compile
 * (three's compileAsync) before the first visible frame. Without this the
 * first render compiles ~20 programs synchronously and blocks the main thread.
 *
 * Two details make the warmed programs the ones actually used:
 *  - it mounts next to <Environment>, so it runs once the HDR is loaded
 *    (an environment change would recompile every standard material);
 *  - it compiles while a render target is bound, because the EffectComposer
 *    renders the scene into one (no tone mapping, linear output), and three
 *    picks those shader variants from the current render target.
 */
function Warmup({ onReady }: { onReady: () => void }) {
  const { gl, scene, camera } = useThree();
  useEffect(() => {
    let alive = true;
    const rt = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType });
    const prev = gl.getRenderTarget();
    gl.setRenderTarget(rt);
    // Without the extension compileAsync gains nothing (and three logs a warning): compile directly.
    const parallel = gl.getContext().getExtension('KHR_parallel_shader_compile') !== null;
    const compiling = parallel ? gl.compileAsync(scene, camera) : Promise.resolve(gl.compile(scene, camera));
    gl.setRenderTarget(prev);
    compiling
      .catch(() => undefined)
      .finally(() => {
        rt.dispose();
        if (alive) onReady();
      });
    return () => {
      alive = false;
    };
  }, [gl, scene, camera, onReady]);
  return null;
}

/** Night HDR environment; falls back to procedural light formers if the HDR cannot load. */
function SceneEnvironment({ onReady }: { onReady: () => void }) {
  return (
    <Boundary
      fallback={
        <>
          <FallbackEnvironment />
          <Warmup onReady={onReady} />
        </>
      }
    >
      <Suspense fallback={null}>
        <Environment preset="night" environmentIntensity={0.55} />
        <Warmup onReady={onReady} />
      </Suspense>
    </Boundary>
  );
}

/** Projects the hovered part's anchor to client pixels for the DOM leader line / tooltip. */
function AnchorProjector() {
  const v = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ camera, gl }) => {
    const id = useMission.getState().hovered;
    const obj = id ? anchors.get(id) : undefined;
    if (!obj) {
      screenAnchor.visible = false;
      return;
    }
    obj.getWorldPosition(v).project(camera);
    const r = gl.domElement.getBoundingClientRect();
    screenAnchor.x = r.left + ((v.x + 1) / 2) * r.width;
    screenAnchor.y = r.top + ((1 - v.y) / 2) * r.height;
    screenAnchor.visible = v.z < 1 && Math.abs(v.x) <= 1.05 && Math.abs(v.y) <= 1.05;
  });
  return null;
}

/** Procedural night environment, used until (or instead of) the HDR preset. */
function FallbackEnvironment() {
  return (
    <Environment resolution={64} environmentIntensity={0.6}>
      <Lightformer intensity={1.2} color={scene.rimLight} position={[-5, 3, -6]} scale={[10, 4, 1]} />
      <Lightformer intensity={0.8} color={scene.keyLight} position={[6, 5, 6]} scale={[6, 6, 1]} />
      <Lightformer intensity={0.25} color={scene.skyLowHorizon} position={[0, -6, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[20, 20, 1]} />
    </Environment>
  );
}

export default function SceneRoot() {
  const reduced = useReducedMotionPref();
  const visible = usePageVisible();
  // Render continuously only while something is moving; otherwise on demand.
  const animating = useMission((s) => {
    const f = selectFrame(s);
    return s.playing || s.commit.status === 'committed' || !!f?.is_abort || !!f?.leak_detected;
  });
  const [ready, setReady] = useState(false);
  const onReady = useMemo(() => () => setReady(true), []);
  // Never wait forever on a slow HDR download: start rendering anyway after 6 s.
  useEffect(() => {
    const id = setTimeout(onReady, 6000);
    return () => clearTimeout(id);
  }, [onReady]);
  const frameloop = !visible || !ready ? 'never' : animating ? 'always' : 'demand';

  return (
    <div className="relative h-full w-full">
    <Canvas
      frameloop={frameloop}
      dpr={[1, 1.75]}
      gl={{ antialias: false, powerPreference: 'high-performance', stencil: false }}
      camera={{ fov: 38, near: 0.1, far: 240, position: POSES.overview.pos }}
      onCreated={({ gl }) => {
        // Shader error checks call getProgramInfoLog / LINK_STATUS, which block the main
        // thread until the GPU finishes compiling. Keep them in dev, drop them in production.
        gl.debug.checkShaderErrors = import.meta.env.DEV;
      }}
      onPointerMissed={() => useMission.getState().setHovered(null)}
      aria-label="3D view of the balloon, gondola and rocket"
    >
      <Invalidator />
      <AnchorProjector />

      <Sky />
      <ambientLight intensity={0.18} />
      <directionalLight position={[6, 9, 7]} intensity={2.2} color={scene.keyLight} />
      <directionalLight position={[-7, 3, -6]} intensity={1.4} color={scene.rimLight} />
      <SceneEnvironment onReady={onReady} />

      <Vehicle />
      <AltitudeRail />
      <CameraDirector reduced={reduced} />

      <EffectComposer multisampling={4}>
        <Bloom mipmapBlur luminanceThreshold={1} luminanceSmoothing={0.2} intensity={0.75} radius={0.65} />
        <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
      </EffectComposer>
    </Canvas>
      {!ready && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-xs uppercase tracking-[0.18em] text-faint">
          Preparing 3D view…
        </div>
      )}
    </div>
  );
}
