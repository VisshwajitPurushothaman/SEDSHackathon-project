/**
 * Flight 3D view. 1 scene unit = 100 m; x = east, y = up, z = -north (launch site at the origin).
 * The vehicle is drawn x20 (labelled "not to scale"); its balloon radius follows the MEASURED volume.
 * Every visual effect is driven by a backend field:
 *   balloon_venting (ground: duct flow > 3 sigma)  -> gas plume at the balloon's base duct
 *   tank_leak_detected (ground leak detector)        -> jet from the rocket's pressurant tank (+ est. diameter)
 *   balloon envelope leak                            -> no operator-side field exists, so no jet is drawn
 * No effect is shown without its driving field.
 */
import { Html, Line } from '@react-three/drei';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';
import { ParticleField, type Spawn } from '../scene/Particles';
import { scene as sc, ui } from '../theme/tokens';
import type { GroundFrame, Region } from './api';
import { useFlight, type CameraMode } from './store';

const U = 100; // metres per scene unit
const EXAG = 20; // vehicle drawn x20
const M_PER_DEG_LAT = 110_640;

function enuFromLatLon(lat: number, lon: number, lat0: number, lon0: number): [number, number] {
  const east = (lon - lon0) * M_PER_DEG_LAT * Math.cos((lat0 * Math.PI) / 180);
  const north = (lat - lat0) * M_PER_DEG_LAT;
  return [east, north];
}
const toScene = (east: number, north: number, up: number) => new THREE.Vector3(east / U, up / U, -north / U);

const C_LIVE = new THREE.Color(ui.pass);
const C_PRED = new THREE.Color(ui.hold);

/**
 * Render interpolation: frames arrive at up to ~8 Hz while the vehicle may move tens of scene units per
 * second at high time scales. Position = lerp(previous frame, latest frame) over the measured
 * inter-frame interval, shared by the vehicle and the camera so neither lags.
 */
const shown = { pos: new THREE.Vector3(), from: new THREE.Vector3(), to: new THREE.Vector3(), t0: 0, dt: 0.125, frameId: -1, ok: false };

function PositionInterpolator({ frame }: { frame: GroundFrame | null }) {
  useFrame(() => {
    const now = performance.now() / 1000;
    const target = frame ? posOf(frame) : null;
    if (!target) return;
    if (frame!.id !== shown.frameId) {
      const gap = shown.frameId >= 0 ? now - shown.t0 : 0.125;
      shown.from.copy(shown.ok ? shown.pos : target);
      shown.to.copy(target);
      shown.dt = Math.min(Math.max(gap, 0.03), 1.0);
      shown.t0 = now;
      shown.frameId = frame!.id;
      shown.ok = true;
    }
    const k = Math.min(1, (now - shown.t0) / shown.dt);
    shown.pos.lerpVectors(shown.from, shown.to, k);
  }, -1);
  return null;
}

function posOf(f: GroundFrame): THREE.Vector3 | null {
  const E = f.estimates;
  if (!E.east || typeof E.east.value !== 'number') return null;
  return toScene(E.east.value as number, E.north.value as number, E.altitude.value as number);
}

function Ground({ lat0, lon0, area }: { lat0: number; lon0: number; area: [number, number][] }) {
  const lines = useMemo(() => {
    const out: { pts: [number, number, number][]; label: string; at: [number, number, number] }[] = [];
    const span = 1.0; // degrees each way
    for (let d = -span; d <= span + 1e-9; d += 0.1) {
      const lat = Math.round((lat0 + d) * 10) / 10;
      const lon = Math.round((lon0 + d) * 10) / 10;
      const [, n] = enuFromLatLon(lat, lon0, lat0, lon0);
      const [e] = enuFromLatLon(lat0, lon, lat0, lon0);
      const L = (span * M_PER_DEG_LAT) / U;
      out.push({ pts: [[-L, 0, -n / U], [L, 0, -n / U]], label: `${lat.toFixed(1)}°N`, at: [-L, 0, -n / U] });
      out.push({ pts: [[e / U, 0, -L], [e / U, 0, L]], label: `${lon.toFixed(1)}°E`, at: [e / U, 0, L] });
    }
    return out;
  }, [lat0, lon0]);
  const areaPts = useMemo(() => {
    const pts = area.map(([la, lo]) => {
      const [e, n] = enuFromLatLon(la, lo, lat0, lon0);
      return [e / U, 0.5, -n / U] as [number, number, number];
    });
    return [...pts, pts[0]];
  }, [area, lat0, lon0]);
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.35, 0]}>
        <planeGeometry args={[4000, 4000]} />
        <meshStandardMaterial color={sc.graphite} roughness={1} />
      </mesh>
      {lines.map((l, i) => (
        <group key={i}>
          <Line points={l.pts} color={ui.border} lineWidth={1} />
          {i % 4 === 0 && (
            <Html position={l.at} zIndexRange={[5, 0]} style={{ pointerEvents: 'none' }}>
              <span className="scene-label scene-label--muted">{l.label}</span>
            </Html>
          )}
        </group>
      ))}
      <Line points={areaPts} color={ui.pass} lineWidth={1.5} dashed dashSize={20} gapSize={12} />
      <mesh position={[0, 3, 0]}>
        <cylinderGeometry args={[1.5, 1.5, 6, 16]} />
        <meshStandardMaterial color={ui.text} emissive={ui.text} emissiveIntensity={0.4} />
      </mesh>
      <Html position={[0, 9, 0]} center zIndexRange={[6, 0]} style={{ pointerEvents: 'none' }}>
        <span className="scene-label">Launch site</span>
      </Html>
    </group>
  );
}

function Trail({ frames, cursor }: { frames: GroundFrame[]; cursor: number }) {
  const data = useMemo(() => {
    const pts: THREE.Vector3[] = [];
    const cols: THREE.Color[] = [];
    for (let i = 0; i <= cursor && i < frames.length; i++) {
      const p = posOf(frames[i]);
      if (!p) continue;
      pts.push(p);
      cols.push(frames[i].estimates.altitude.status === 'PREDICTED' ? C_PRED : C_LIVE);
    }
    return { pts, cols };
  }, [frames, cursor]);
  if (data.pts.length < 2) return null;
  return <Line points={data.pts} vertexColors={data.cols.map((c) => [c.r, c.g, c.b] as [number, number, number])} lineWidth={2} />;
}

function RegionShapes({ regions, y, color, lat0, lon0 }: { regions: Region[]; y: number; color: string; lat0: number; lon0: number }) {
  const shapes = useMemo(
    () =>
      regions.map((r) => {
        const s = new THREE.Shape();
        r.polygon.forEach(([la, lo], i) => {
          const [e, n] = enuFromLatLon(la, lo, lat0, lon0);
          if (i === 0) s.moveTo(e / U, n / U);
          else s.lineTo(e / U, n / U);
        });
        return { level: r.level, geom: new THREE.ShapeGeometry(s) };
      }),
    [regions, lat0, lon0],
  );
  return (
    <group position={[0, y, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      {shapes.map((s) => (
        <mesh key={s.level} geometry={s.geom} renderOrder={2}>
          <meshBasicMaterial color={color} transparent opacity={s.level === 50 ? 0.32 : s.level === 90 ? 0.18 : 0.09} depthWrite={false} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </group>
  );
}

/**
 * Balloon + gondola (x20), duct vent plume, tank leak jet, parachute after cut-down.
 * Anchored at the GONDOLA: the position estimate is the gondola's (GNSS antenna); the balloon hangs above it.
 */
function Vehicle({ frame }: { frame: GroundFrame }) {
  const group = useRef<THREE.Group>(null);
  const balloon = useRef<THREE.Mesh>(null);
  const E = frame.estimates;
  const target = posOf(frame);
  const vol = typeof E.balloon_volume?.value === 'number' ? (E.balloon_volume.value as number) : null;
  const rPhys = vol ? Math.cbrt((3 * vol) / (4 * Math.PI)) : 3;
  const rVis = (rPhys * EXAG) / U;
  const cut = ['CUTDOWN', 'DESCENT', 'LANDED'].includes(frame.vehicle_phase);
  const venting = E.balloon_venting?.value === true && !cut;
  const ventRate = typeof E.balloon_vent_rate?.value === 'number' ? (E.balloon_vent_rate.value as number) : 0;
  const leak = E.tank_leak_detected?.value === true && frame.rocket_phase === 'ABOARD';
  const leakDia = typeof E.tank_leak_diameter?.value === 'number' ? (E.tank_leak_diameter.value as number) : null;
  const predicted = E.altitude?.status === 'PREDICTED';
  const lineLen = 15 * (EXAG / U); // flight train, x20
  const balloonY = lineLen + rVis * 1.1; // balloon centre above the gondola

  useFrame((_, dt) => {
    if (!group.current || !target || !shown.ok) return;
    group.current.position.copy(shown.pos);
    if (balloon.current) {
      const s = balloon.current.scale;
      s.lerp(new THREE.Vector3(rVis, rVis * 1.1, rVis), Math.min(1, dt * 4));
    }
  });

  const ventEmit = (s: Spawn) => {
    s.pos.set((Math.random() - 0.5) * 0.15 * rVis, balloonY - rVis * 1.1, (Math.random() - 0.5) * 0.15 * rVis);
    s.vel.set((Math.random() - 0.5) * 0.4 * rVis, -0.6 * rVis - Math.random() * 0.4 * rVis, (Math.random() - 0.5) * 0.4 * rVis);
    s.life = 1.2 + Math.random();
  };
  const leakEmit = (s: Spawn) => {
    s.pos.set(0.25, 0.2, 0.1);
    s.vel.set(1.5 + Math.random(), (Math.random() - 0.3) * 0.6, (Math.random() - 0.5) * 0.6);
    s.life = 0.6 + Math.random() * 0.5;
  };

  return (
    <group ref={group}>
      {!cut && (
        <mesh ref={balloon} position={[0, balloonY, 0]} scale={[rVis, rVis * 1.1, rVis]}>
          <sphereGeometry args={[1, 32, 24]} />
          <meshStandardMaterial color={sc.balloon} transparent opacity={predicted ? 0.35 : 0.6} roughness={0.5} />
        </mesh>
      )}
      {cut && (
        <mesh position={[0, 2.2, 0]}>
          <sphereGeometry args={[1.6, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2]} />
          <meshStandardMaterial color={ui.hold} side={THREE.DoubleSide} />
        </mesh>
      )}
      <Line points={[[0, cut ? 2.2 : lineLen, 0], [0, 0, 0]]} color={sc.loadLine} lineWidth={1} />
      <mesh position={[0, 0, 0]}>
        <boxGeometry args={[0.5, 0.6, 0.5]} />
        <meshStandardMaterial color={sc.gondolaFrame} metalness={0.6} roughness={0.4} />
      </mesh>
      {venting && (
        <ParticleField count={300} rate={() => 60 + Math.min(400, ventRate * 20000)} emit={ventEmit} colorA={sc.vent} colorB={sc.vent} intensity={0.6} size={() => 0.25 * rVis} drag={0.4} />
      )}
      {leak && (
        <>
          <ParticleField count={200} rate={() => 150} emit={leakEmit} colorA={sc.vent} colorB={sc.vent} intensity={0.8} size={0.3} drag={0.3} />
          <Html position={[1.2, 0.6, 0]} zIndexRange={[8, 0]} style={{ pointerEvents: 'none' }}>
            <span className="scene-label text-fail">tank leak{leakDia ? ` · est. ${leakDia.toFixed(0)} µm` : ''}</span>
          </Html>
        </>
      )}
      <Html position={[rVis * 1.2, cut ? 2.2 : balloonY, 0]} zIndexRange={[8, 0]} style={{ pointerEvents: 'none' }}>
        <span className={`scene-label ${predicted ? 'text-hold' : 'scene-label--muted'}`}>
          {predicted ? 'PREDICTED · ' : ''}
          {(((E.altitude?.value as number) ?? 0) / 1000).toFixed(2)} km · ×{EXAG} not to scale
        </span>
      </Html>
    </group>
  );
}

/** Rocket after commit (illustrative model): position = release point + downrange along the rail azimuth. */
function RocketTrack({ frames, cursor, azimuth }: { frames: GroundFrame[]; cursor: number; azimuth: number }) {
  const { pts, head, burning } = useMemo(() => {
    let release: THREE.Vector3 | null = null;
    const pts: THREE.Vector3[] = [];
    let burning = false;
    const az = (azimuth * Math.PI) / 180;
    for (let i = 0; i <= cursor && i < frames.length; i++) {
      const f = frames[i];
      if (f.rocket_phase === 'ABOARD') continue;
      if (!release) {
        for (let j = i; j >= 0; j--) {
          const p = posOf(frames[j]);
          if (p && frames[j].rocket_phase === 'ABOARD') {
            release = p;
            break;
          }
        }
      }
      const E = f.estimates;
      if (!release || typeof E.rocket_downrange?.value !== 'number') continue;
      const x = E.rocket_downrange.value as number;
      pts.push(new THREE.Vector3(release.x + (x * Math.sin(az)) / U, (E.rocket_altitude.value as number) / U, release.z - (x * Math.cos(az)) / U));
      burning = f.rocket_phase === 'BURN';
    }
    return { pts, head: pts[pts.length - 1] ?? null, burning };
  }, [frames, cursor, azimuth]);
  if (!head) return null;
  return (
    <group>
      {pts.length > 1 && <Line points={pts} color={ui.armed} lineWidth={1.5} dashed dashSize={4} gapSize={3} />}
      <mesh position={head}>
        <coneGeometry args={[0.6, 2.2, 12]} />
        <meshStandardMaterial color={sc.liveryWhite} emissive={burning ? sc.plumeEdge : sc.graphite} emissiveIntensity={burning ? 2 : 0} toneMapped={!burning} />
      </mesh>
      <Html position={head.clone().add(new THREE.Vector3(1.5, 0, 0))} zIndexRange={[8, 0]} style={{ pointerEvents: 'none' }}>
        <span className="scene-label text-armed">rocket · illustrative model, not validated</span>
      </Html>
    </group>
  );
}

function CameraRig({ frame, mode }: { frame: GroundFrame | null; mode: CameraMode }) {
  const { camera } = useThree();
  const look = useRef(new THREE.Vector3());
  const offset = useRef(new THREE.Vector3(-14, 4, 18));
  useFrame((_, dt) => {
    const target = frame && shown.ok ? shown.pos : new THREE.Vector3(0, 0, 0);
    // The offset (mode change) eases; the target itself is followed without lag.
    const wantOffset = mode === 'chase' ? new THREE.Vector3(-14, 4, 18) : mode === 'top' ? new THREE.Vector3(0.01, 160, 0.01) : null;
    const k = Math.min(1, dt * 3);
    if (wantOffset) {
      offset.current.lerp(wantOffset, k);
      camera.position.copy(target).add(offset.current);
    } else {
      camera.position.lerp(new THREE.Vector3(-2, 1, 2), k); // ground station at the launch site
    }
    look.current.copy(target);
    camera.lookAt(look.current);
    const cam = camera as THREE.PerspectiveCamera;
    const fov = mode === 'ground' ? 18 : 45;
    if (Math.abs(cam.fov - fov) > 0.1) {
      cam.fov += (fov - cam.fov) * k;
      cam.updateProjectionMatrix();
    }
  });
  return null;
}

function SkyTint({ frame }: { frame: GroundFrame | null }) {
  const { scene } = useThree();
  const low = useMemo(() => new THREE.Color(sc.skyLowHorizon), []);
  const high = useMemo(() => new THREE.Color(sc.skyHighTop), []);
  useFrame(() => {
    const alt = (frame?.estimates.altitude?.value as number) ?? 0;
    const k = Math.min(1, Math.max(0, alt / 25000));
    scene.background = low.clone().lerp(high, k);
  });
  return null;
}

export default function FlightScene({ frame }: { frame: GroundFrame | null }) {
  const frames = useFlight((s) => s.frames);
  const cursor = useFlight((s) => s.cursor);
  const mode = useFlight((s) => s.camera);
  const config = useFlight((s) => s.config);
  const lat0 = config?.site.lat_deg ?? 13.72;
  const lon0 = config?.site.lon_deg ?? 80.23;
  const E = frame?.estimates;
  return (
    <Canvas dpr={[1, 1.75]} camera={{ fov: 45, near: 0.1, far: 20000, position: [-30, 10, 40] }} gl={{ antialias: true, powerPreference: 'high-performance' }}>
      <SkyTint frame={frame} />
      <ambientLight intensity={0.5} />
      <directionalLight position={[300, 500, 200]} intensity={1.6} color={sc.keyLight} />
      <Ground lat0={lat0} lon0={lon0} area={config?.gates.approved_area ?? []} />
      <PositionInterpolator frame={frame} />
      <Trail frames={frames} cursor={cursor} />
      {frame && <Vehicle frame={frame} />}
      {E?.prediction && <RegionShapes regions={E.prediction.regions} y={E.prediction.mean.up / U} color={ui.hold} lat0={lat0} lon0={lon0} />}
      {frame?.landing_footprint && <RegionShapes regions={frame.landing_footprint.regions} y={0.6} color={ui.info} lat0={lat0} lon0={lon0} />}
      {frame?.impact_points?.map((p) => {
        const [e, n] = enuFromLatLon(p.lat, p.lon, lat0, lon0);
        return (
          <mesh key={p.case} position={[e / U, 1.5, -n / U]}>
            <sphereGeometry args={[1.5, 12, 8]} />
            <meshBasicMaterial color={p.inside ? ui.pass : ui.fail} />
          </mesh>
        );
      })}
      <RocketTrack frames={frames} cursor={cursor} azimuth={config?.rocket.rail_azimuth_deg ?? 90} />
      <CameraRig frame={frame} mode={mode} />
    </Canvas>
  );
}
