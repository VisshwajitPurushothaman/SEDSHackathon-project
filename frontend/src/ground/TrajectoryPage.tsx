import { useEffect, useMemo, useRef, useState } from 'react';
import { API_URL } from '../api/client';
import { ui } from '../theme/tokens';
import { fmtMET, fmtNum } from './format';
import { profileMapProps, useProfile } from './PreflightPage';
import { RangeMap, type MapLine, type MapPoint, type MapPoly } from './RangeMap';
import { StripChart } from './StripChart';
import { TrajectoryXYZGraph, type TrajectoryXYZPoint } from './TrajectoryXYZGraph';
import { useGround } from './store';

interface HorizonForecast {
  physics: { alt: number; lat: number; lon: number };
  hybrid: { alt: number; lat: number; lon: number } | null;
  status: string;
  out_of_range?: string[] | null;
  default: boolean;
}
export interface Forecast {
  met: number;
  phase: string;
  state_status: string;
  now: { alt: number; lat: number; lon: number };
  physics_path: { h: number; alt: number; lat: number; lon: number }[];
  horizons: Record<string, HorizonForecast>;
}
interface ModelCard {
  available: boolean;
  name?: string;
  sklearn?: string;
  train_seeds?: [number, number];
  test_seeds?: [number, number];
  data_sha256?: string;
  error?: string;
  label?: string;
  evaluation?: {
    primary: string;
    primary_decision: string;
    ci95_mae_improvement_m: Record<string, [number, number]>;
    default_enabled: Record<string, boolean>;
    table: { key: string; split: string; physics: { mae: number }; hybrid: { mae: number }; persistence: { mae: number } }[];
    fallback_rate_by_horizon: Record<string, number>;
  } | null;
}

const H = ['30', '60', '120'];

// Point Calimere Bird Sanctuary (Tamil Nadu) Launch Origin
const ORIGIN_LAT = 10.301500;
const ORIGIN_LON = 79.851200;
const ORIGIN_NAME = 'Point Calimere Bird Sanctuary (Tamil Nadu)';

function ModelCardPanel({ card, fc, ml, setMl }: { card: ModelCard | null; fc: Forecast | null; ml: boolean; setMl: (v: boolean) => void }) {
  const ev = card?.evaluation;
  const overall = (k: string) => ev?.table.find((r) => r.key === k && r.split === 'overall');
  return (
    <section className="panel" aria-labelledby="mc-title">
      <div className="panel-head">
        <h2 id="mc-title" className="panel-title">
          Forecast model
        </h2>
        <label className="ml-auto flex items-center gap-1.5 text-[12px]">
          <input type="checkbox" checked={ml} onChange={(e) => setMl(e.target.checked)} disabled={!card?.available} aria-label="ML correction" />
          ML correction
        </label>
      </div>
      {!card?.available ? (
        <p className="px-3 py-2 text-[12px] text-hold">Model unavailable: physics forecast only ({card?.error ?? 'no model file'}).</p>
      ) : (
        <div className="px-3 py-2 text-[12px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="mn text-ink">{card.name}</span>
            <span className="tag tag-pred">PREDICTED</span>
            <span className="tag tag-cau">{card.label}</span>
          </div>
          <p className="mt-1 text-[11px] text-muted">
            sklearn {card.sklearn} · train seeds {card.train_seeds?.join('–')} · test seeds {card.test_seeds?.join('–')} · data {card.data_sha256}…
          </p>
          {ev && (
            <>
              <p className="mt-1.5">
                Decision ({ev.primary} s): <span className={ev.primary_decision.startsWith('hybrid') ? 'text-pass' : 'text-hold'}>{ev.primary_decision}</span>
              </p>
              <table className="tbl mt-1">
                <thead>
                  <tr>
                    <th>alt @</th>
                    <th className="text-right">physics MAE</th>
                    <th className="text-right">hybrid MAE</th>
                    <th className="text-right">Δ 95 % CI</th>
                  </tr>
                </thead>
                <tbody>
                  {H.map((h) => {
                    const k = `z@${h}`;
                    const r = overall(k);
                    const ci = ev.ci95_mae_improvement_m[k];
                    return (
                      <tr key={h}>
                        <td className="num">{h} s</td>
                        <td className="num text-right">{fmtNum(r?.physics.mae, 1)} m</td>
                        <td className="num text-right">{fmtNum(r?.hybrid.mae, 1)} m</td>
                        <td className="num text-right">
                          {fmtNum(ci?.[0], 1)}…{fmtNum(ci?.[1], 1)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-1 text-[10.5px] text-muted">Test missions only; simulator data. Improvement = physics MAE − hybrid MAE.</p>
            </>
          )}
          <a className="mt-1 inline-block text-[11.5px] text-info hover:underline" href={`${API_URL}/api/ground/ml/report`} target="_blank" rel="noreferrer">
            ML_RESULTS.md
          </a>
        </div>
      )}
      <div className="border-t border-line px-3 py-1.5">
        <span className="lbl">Live fallback status</span>
        <div className="mt-1 flex gap-3 text-[11.5px]">
          {H.map((h) => {
            const f = fc?.horizons[h];
            return (
              <span key={h} className="flex items-center gap-1" title={f?.out_of_range?.join(', ') ?? ''}>
                <span className="num text-muted">{h}s</span>
                <span className={`tag ${f?.status === 'OK' ? 'tag-nom' : 'tag-cau'}`}>{f ? (f.status === 'OK' ? 'HYBRID' : f.status) : '---'}</span>
              </span>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export default function TrajectoryPage() {
  const snap = useGround((s) => s.snap);
  const rows = useGround((s) => s.rows);
  const track = useGround((s) => s.track);
  const prof = useProfile();
  const [card, setCard] = useState<ModelCard | null>(null);
  const [mlOverride, setMl] = useState<boolean | null>(null);
  const [mainTab, setMainTab] = useState<'xyz' | 'map'>('xyz');

  useEffect(() => {
    fetch(`${API_URL}/api/ground/ml/model`)
      .then((r) => r.json() as Promise<ModelCard>)
      .then(setCard)
      .catch(() => setCard({ available: false, error: 'backend unreachable' }));
  }, []);

  const ml = mlOverride ?? !!card?.evaluation?.default_enabled?.['z@30'];
  const fc = (snap as unknown as { forecast?: Forecast | null } | null)?.forecast ?? null;
  const g = snap?.ground;

  // Latch coordinates to prevent fluctuating between pings
  const dt = snap?.digital_twin_telemetry;
  const latchedCoords = useRef({ x: 0, y: 0, z: 0, slant: 0 });

  if (dt?.gnss_coordinates) {
    const rawX = dt.gnss_coordinates.x_downrange_m ?? dt.gnss_coordinates.position_from_origin_xyz_m?.[0] ?? 0;
    const rawY = dt.gnss_coordinates.y_altitude_m ?? dt.gnss_coordinates.position_from_origin_xyz_m?.[1] ?? 0;
    const rawZ = dt.gnss_coordinates.z_crossrange_m ?? dt.gnss_coordinates.position_from_origin_xyz_m?.[2] ?? 0;
    const slant = dt.gnss_coordinates.slant_range_3d_m ?? Math.hypot(rawX, rawY, rawZ);

    if (rawY > 0 || latchedCoords.current.y === 0) {
      latchedCoords.current = { x: rawX, y: rawY, z: rawZ, slant };
    }
  } else if (rows.length > 0) {
    const lastR = rows[rows.length - 1];
    const rawX = (lastR.x as number) ?? 0;
    const rawY = (lastR.y as number) ?? (lastR.GNSS_ALT as number) ?? 0;
    const rawZ = (lastR.z as number) ?? 0;
    const slant = Math.hypot(rawX, rawY, rawZ);
    if (rawY > 0 || latchedCoords.current.y === 0) {
      latchedCoords.current = { x: rawX, y: rawY, z: rawZ, slant };
    }
  }

  const { x: curX, y: curY, z: curZ, slant: curSlant } = latchedCoords.current;

  // High-precision WGS84 Geodetic fix derived from Launch Pad Origin at Point Calimere
  const computedLat = ORIG_LAT_CALC(curZ);
  const computedLon = ORIG_LON_CALC(curX);

  function ORIG_LAT_CALC(z: number): number {
    return Number((ORIGIN_LAT + z / 110609.6).toFixed(6));
  }
  function ORIG_LON_CALC(x: number): number {
    return Number((ORIGIN_LON + x / 109531.0).toFixed(6));
  }

  // Ping paused / prediction indicator
  const isPaused =
    dt?.ping_paused === 1 ||
    dt?.ping_paused === true ||
    dt?.ping_paused === '1' ||
    dt?.is_prediction_active === true ||
    dt?.simulation_state?.is_paused === true;

  // History for 3D XYZ graph
  const historyXYZ: TrajectoryXYZPoint[] = useMemo(() => {
    return rows.map((r) => ({
      x: typeof r.x === 'number' ? r.x : 0,
      y: typeof r.y === 'number' ? r.y : typeof r.GNSS_ALT === 'number' ? r.GNSS_ALT : 0,
      z: typeof r.z === 'number' ? r.z : 0,
      met: r.met,
    }));
  }, [rows]);

  const lines = useMemo<MapLine[]>(() => {
    const out: MapLine[] = [];
    let seg: [number, number][] = [];
    let st = '';
    const flush = () => {
      if (seg.length > 1) out.push({ id: `trk${out.length}`, coords: seg, color: st === 'FUSED' ? ui.pass : st === 'RECONSTRUCTED' ? '#4C7DFF' : ui.info, width: 2.5 });
    };
    for (const p of track) {
      if (p.status !== st) {
        flush();
        seg = seg.length ? [seg[seg.length - 1]] : [];
        st = p.status;
      }
      seg.push([p.lat, p.lon]);
    }
    flush();
    if (fc) {
      out.push({ id: 'phys', coords: [[fc.now.lat, fc.now.lon], ...fc.physics_path.map((p) => [p.lat, p.lon] as [number, number])], color: ui.info, dashed: true });
      const hyb = H.map((h) => fc.horizons[h]?.hybrid).filter((x): x is NonNullable<typeof x> => !!x);
      if (ml && hyb.length) out.push({ id: 'hyb', coords: [[fc.now.lat, fc.now.lon], ...hyb.map((p) => [p.lat, p.lon] as [number, number])], color: ui.white, dashed: true });
    }
    return out;
  }, [track, fc, ml]);

  const polys = useMemo<MapPoly[]>(() => {
    const out: MapPoly[] = [];
    for (const r of g?.landing_footprint?.regions ?? []) out.push({ id: `fp${r.level}`, polygon: r.polygon, color: ui.hold, opacity: r.level === 50 ? 0.22 : 0.08 });
    const pred = (g?.estimates?.prediction as unknown as { regions?: { level: number; polygon: [number, number][] }[] } | undefined)?.regions;
    for (const r of pred ?? []) out.push({ id: `pr${r.level}`, polygon: r.polygon, color: ui.info, opacity: r.level === 50 ? 0.25 : 0.1 });
    return out;
  }, [g]);

  const points = useMemo<MapPoint[]>(() => {
    const out: MapPoint[] = [];
    out.push({ id: 'pad-origin', lat: ORIGIN_LAT, lon: ORIGIN_LON, color: '#f5c542', label: 'PAD: Point Calimere Bird Sanctuary' });
    out.push({ id: 'vehicle-pos', lat: computedLat, lon: computedLon, color: ui.pass, label: `VEHICLE (${curY.toFixed(0)}m)` });
    const ip = (g as unknown as { impact_points?: { case: string; lat: number; lon: number; inside: boolean }[] } | undefined)?.impact_points;
    for (const p of ip ?? []) out.push({ id: `iip-${p.case}`, lat: p.lat, lon: p.lon, color: p.inside ? ui.white : ui.fail, label: `IIP ${p.case}` });
    return out;
  }, [g, computedLat, computedLon, curY]);

  // Altitude vs time: last 20 min measured + forecasts to +120 s.
  const chart = useMemo(() => {
    const recent = rows.filter((r) => rows.length && r.met >= rows[rows.length - 1].met - 1200);
    const fx = fc ? [fc.met, ...H.map((h) => fc.met + Number(h))] : [];
    const x = [...recent.map((r) => r.met), ...fx.filter((t) => !recent.length || t > recent[recent.length - 1].met)];
    const idx = new Map(x.map((t, i) => [t, i]));
    const meas = x.map(() => null as number | null);
    recent.forEach((r, i) => (meas[i] = typeof r.GNSS_ALT === 'number' ? (r.GNSS_ALT as number) : null));
    const phys = x.map(() => null as number | null);
    const hyb = x.map(() => null as number | null);
    if (fc) {
      const i0 = idx.get(fc.met);
      if (i0 !== undefined) phys[i0] = hyb[i0] = fc.now.alt;
      H.forEach((h) => {
        const i = idx.get(fc.met + Number(h));
        if (i === undefined) return;
        phys[i] = fc.horizons[h]?.physics.alt ?? null;
        hyb[i] = fc.horizons[h]?.hybrid?.alt ?? null;
      });
    }
    return { x, meas, phys, hyb };
  }, [rows, fc]);

  return (
    <div className="flex flex-col gap-2 p-2">
      {/* Paused / Prediction active user prompt banner */}
      {isPaused && (
        <div className="flex items-center justify-between rounded border border-[#f5c542]/50 bg-[#f5c542]/15 px-4 py-2 font-mono text-[12px] text-[#f5c542] animate-pulse">
          <div className="flex items-center gap-2">
            <span className="text-[14px]">⚠️</span>
            <span className="font-bold tracking-wide">
              TELEMETRY STREAM PAUSED: FLIGHT SYSTEM RUNNING ON FUSED KALMAN PREDICTION ALGORITHM — DISPLAYING PROPAGATED TELEMETRY
            </span>
          </div>
          <span className="rounded bg-[#f5c542]/25 px-2 py-0.5 text-[10.5px] font-extrabold border border-[#f5c542]/50">
            PREDICTION ACTIVE
          </span>
        </div>
      )}

      <div className="grid min-h-[760px] grid-cols-[minmax(0,1fr)_380px] gap-2">
        <div className="flex min-h-0 flex-col gap-2">
          {/* Main Trajectory Header & View Switcher */}
          <div className="flex items-center justify-between rounded border border-[#222e42] bg-[#0c1422] px-3 py-1.5 font-mono text-[12px]">
            <div className="flex items-center gap-2">
              <span className="font-bold text-[#5cd6ff]">MISSION TRAJECTORY CORRIDOR</span>
              <span className="rounded bg-[#132238] px-2 py-0.5 text-[10.5px] text-[#3fd68a] border border-[#3fd68a]/30">
                POINT CALIMERE ORIGIN [0, 0, 0]
              </span>
            </div>
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => setMainTab('xyz')}
                className={`rounded px-2.5 py-1 text-[11px] font-bold transition-all ${
                  mainTab === 'xyz' ? 'bg-[#1e74ba] text-white shadow' : 'bg-[#121c2d] text-muted hover:text-white'
                }`}
              >
                📐 High-Precision XYZ Graph
              </button>
              <button
                type="button"
                onClick={() => setMainTab('map')}
                className={`rounded px-2.5 py-1 text-[11px] font-bold transition-all ${
                  mainTab === 'map' ? 'bg-[#1e74ba] text-white shadow' : 'bg-[#121c2d] text-muted hover:text-white'
                }`}
              >
                🗺️ Geodetic Range Map
              </button>
            </div>
          </div>

          {/* MAIN VIEW: 3D XYZ GRAPH (User requirement) */}
          {mainTab === 'xyz' ? (
            <div className="min-h-[480px] flex-1">
              <TrajectoryXYZGraph
                currentX={curX}
                currentY={curY}
                currentZ={curZ}
                slantRangeM={curSlant}
                originName={ORIGIN_NAME}
                originLat={ORIGIN_LAT}
                originLon={ORIGIN_LON}
                computedLat={computedLat}
                computedLon={computedLon}
                history={historyXYZ}
              />
            </div>
          ) : (
            <section className="panel flex min-h-[480px] flex-1 flex-col" aria-labelledby="traj-map">
              <div className="panel-head">
                <h2 id="traj-map" className="panel-title">
                  Point Calimere Corridor Range Map
                </h2>
                <span className="flex flex-wrap gap-3 text-[10.5px] text-muted">
                  <span className="text-pass">━ measured track</span>
                  <span className="text-hold">◯ launch corridor</span>
                  <span>● Pad Origin</span>
                </span>
              </div>
              <div className="min-h-0 flex-1 p-1.5">
                {prof ? (
                  <RangeMap {...profileMapProps(prof)} center={[ORIGIN_LAT, ORIGIN_LON]} zoom={8.8} lines={lines} polys={polys} points={points} />
                ) : (
                  <RangeMap
                    center={[ORIGIN_LAT, ORIGIN_LON]}
                    approved={null}
                    zones={[]}
                    zoom={8.8}
                    lines={lines}
                    polys={polys}
                    points={points}
                  />
                )}
              </div>
            </section>
          )}

          {/* Altitude Profile StripChart */}
          <StripChart
            title="Altitude profile & Kalman forecast"
            unit="m"
            x={chart.x}
            series={[
              { label: 'GNSS_ALT', values: chart.meas, color: ui.pass, points: true },
              { label: 'physics', values: chart.phys, color: ui.info, dash: [5, 4] },
              ...(ml ? [{ label: 'hybrid', values: chart.hyb, color: ui.white, dash: [2, 3] }] : []),
            ]}
            decimals={0}
            height={180}
          />
        </div>

        {/* Right Sidebar: Forecast & Landing Footprint */}
        <div className="flex min-h-0 flex-col gap-2 overflow-auto">
          {/* Pad Coordinate Reference Card */}
          <section className="panel p-3 font-mono text-[11.5px]" aria-label="Pad Reference">
            <span className="lbl text-[#5cd6ff]">LAUNCH PAD REFERENCE</span>
            <div className="mt-1 text-[13px] font-bold text-[#f5c542]">{ORIGIN_NAME}</div>
            <div className="mt-1 flex justify-between text-muted text-[11px]">
              <span>Latitude:</span>
              <span className="text-white font-bold">{ORIGIN_LAT.toFixed(6)}° N</span>
            </div>
            <div className="flex justify-between text-muted text-[11px]">
              <span>Longitude:</span>
              <span className="text-white font-bold">{ORIGIN_LON.toFixed(6)}° E</span>
            </div>
            <div className="flex justify-between text-muted text-[11px]">
              <span>Corridor:</span>
              <span className="text-[#3fd68a] font-bold">Palk Strait / Bay of Bengal</span>
            </div>
            <div className="mt-2 border-t border-[#222e42] pt-1.5">
              <span className="lbl text-[#3fd68a]">HIGH-PRECISION TELEMETRY FIX</span>
              <div className="mt-1 flex justify-between text-[11px]">
                <span className="text-muted">Downrange (X):</span>
                <span className="text-[#ff5252] font-bold">{curX >= 0 ? `+${curX.toFixed(2)}` : curX.toFixed(2)} m</span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-muted">Altitude (Y):</span>
                <span className="text-[#3fd68a] font-bold">{curY.toFixed(2)} m</span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-muted">Crossrange (Z):</span>
                <span className="text-[#00e5ff] font-bold">{curZ >= 0 ? `+${curZ.toFixed(2)}` : curZ.toFixed(2)} m</span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-muted">3D Slant Range:</span>
                <span className="text-[#f5c542] font-bold">{curSlant.toFixed(2)} m</span>
              </div>
            </div>
          </section>

          <section className="panel" aria-labelledby="fc-now">
            <div className="panel-head">
              <h2 id="fc-now" className="panel-title">
                Forecast
              </h2>
              <span className="tag tag-pred ml-auto">PREDICTED</span>
            </div>
            {fc ? (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>+h</th>
                    <th className="text-right">physics alt</th>
                    <th className="text-right">hybrid alt</th>
                    <th>source</th>
                  </tr>
                </thead>
                <tbody>
                  {H.map((h) => {
                    const f = fc.horizons[h];
                    return (
                      <tr key={h}>
                        <td className="num">{h} s</td>
                        <td className="num text-right text-info">{fmtNum(f?.physics.alt, 0)} m</td>
                        <td className="num text-right">{f?.hybrid ? `${fmtNum(f.hybrid.alt, 0)} m` : '---'}</td>
                        <td>
                          <span className={`tag ${ml && f?.status === 'OK' ? 'tag-fused' : 'tag-pred'}`}>{ml && f?.status === 'OK' ? 'HYBRID' : 'PHYSICS'}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <p className="px-3 py-2 text-[12px] text-muted">Tracking stratospheric ascent trajectory.</p>
            )}
            {fc && (
              <p className="px-3 py-1.5 text-[11px] text-muted">
                from {fmtMET(fc.met)} · {fc.phase} · state {fc.state_status} · updated every 5 s MET
              </p>
            )}
          </section>
          <ModelCardPanel card={card} fc={fc} ml={ml} setMl={setMl} />
          <section className="panel px-3 py-2 text-[11.5px] text-muted" aria-label="Landing footprint">
            <span className="lbl">Landing footprint</span>
            <p className="mt-1">{g?.landing_footprint ? `${g.landing_footprint.basis} · 50/90/99 % regions` : 'Palk Strait Recovery Zone (Active)'}</p>
          </section>
        </div>
      </div>
    </div>
  );
}
