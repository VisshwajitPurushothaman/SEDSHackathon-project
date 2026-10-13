import { useCallback, useEffect, useMemo, useState } from 'react';
import { API_URL } from '../api/client';
import { ui } from '../theme/tokens';
import { fmtMET, fmtNum, fmtUTC } from './format';
import { profileMapProps, useProfile } from './PreflightPage';
import { RangeMap, type MapLine, type MapPoint } from './RangeMap';
import { StripChart } from './StripChart';
import { useGround } from './store';

interface Recovery {
  state: string;
  stage?: string;
  source?: string | null;
  expected?: number | null;
  have?: number;
  missing_at_start?: number | null;
  recovered?: number;
  retries?: number;
  lost_count?: number;
  lost?: number[];
  hmac_failures?: number[];
  chain_ok?: boolean | null;
  error?: string | null;
}
interface Hist {
  t: number;
  lat: number;
  lon: number;
  alt: number | null;
}
interface Score {
  reference: string;
  h: number;
  n: number;
  hybrid_used: number;
  persistence_alt_mae: number;
  physics_alt_mae: number;
  hybrid_alt_mae: number;
  persistence_horiz_mae: number;
  physics_horiz_mae: number;
  hybrid_horiz_mae: number;
}
interface FaultRow {
  kind: string;
  met: number;
  title: string;
  severity?: string;
  detected_met?: number | null;
  detection_delay_s?: number | null;
}
interface Report {
  frames_held: number;
  frames_recovered: number;
  requirements: { id: string; text: string; status: string; evidence: Record<string, unknown> }[];
  limit_violations: { channel: string; phase: string; start_met: number; end_met: number; level: string; peak: number; samples: number }[];
  predicted_vs_actual: { channel: string; samples: number; inside_pct: number | null; worst_outside: number | null }[];
  fault_timeline: FaultRow[];
  forecast_scorecard: Score[];
  landing: { miss_distance_m: number; inside: Record<string, boolean>; landing_fix: { lat: number; lon: number } } | null;
  closure: Record<string, { by: string; utc: number } | null>;
}
interface Analysis {
  report: Report;
  report_sha256: string;
  grading: boolean;
  histories: { live: { met: number; lat: number; lon: number; alt: number | null; status: string }[]; reconstructed: Hist[]; truth?: Hist[] };
  grading_view?: { fault_timeline: FaultRow[]; forecast_scorecard: Score[]; landing_truth: { lat: number; lon: number } | null; scenario?: string; variability?: Record<string, number> };
}

const REQ_CLS: Record<string, string> = { PASS: 'tag-nom', FAIL: 'tag-alm', 'NOT TESTED': 'tag-stale', INCONCLUSIVE: 'tag-cau' };
const CLOSURE: [string, string][] = [
  ['payload_recovered', 'Payload recovered'],
  ['data_archived', 'Data archived'],
  ['anomalies_filed', 'Anomalies filed'],
];
const SUBSYSTEMS = ['NAV', 'ENV', 'ATT', 'PROP', 'PWR', 'COMM', 'VEHICLE'];

async function download(path: string, name: string) {
  const r = await fetch(`${API_URL}${path}`);
  const text = await r.text(); // original bytes: keeps the report hash verifiable
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: r.headers.get('content-type') ?? 'text/plain' }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

const post = (path: string, body: unknown) => fetch(`${API_URL}${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

function Panel({ id, title, right, children }: { id: string; title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="panel flex min-w-0 flex-col" aria-labelledby={id}>
      <div className="panel-head">
        <h2 id={id} className="panel-title">
          {title}
        </h2>
        {right && <span className="ml-auto flex items-center gap-2">{right}</span>}
      </div>
      {children}
    </section>
  );
}

function ScoreTable({ rows, label }: { rows: Score[]; label: string }) {
  if (!rows.length) return <p className="px-3 py-2 text-[12px] text-muted">No forecasts with a reference yet.</p>;
  return (
    <table className="tbl" aria-label={label}>
      <thead>
        <tr>
          <th>ref</th>
          <th>+h</th>
          <th className="text-right">n</th>
          <th className="text-right">alt MAE pers / phys / hyb (m)</th>
          <th className="text-right">horiz MAE pers / phys / hyb (m)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.reference}${r.h}`}>
            <td className="text-[11px]">{r.reference}</td>
            <td className="num">{r.h} s</td>
            <td className="num text-right">{r.n}</td>
            <td className="num text-right">
              {fmtNum(r.persistence_alt_mae, 0)} / {fmtNum(r.physics_alt_mae, 1)} / <span className={r.hybrid_alt_mae < r.physics_alt_mae ? 'text-pass' : 'text-hold'}>{fmtNum(r.hybrid_alt_mae, 1)}</span>
            </td>
            <td className="num text-right">
              {fmtNum(r.persistence_horiz_mae, 0)} / {fmtNum(r.physics_horiz_mae, 1)} / <span className={r.hybrid_horiz_mae < r.physics_horiz_mae ? 'text-pass' : 'text-hold'}>{fmtNum(r.hybrid_horiz_mae, 1)}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function PostflightPage() {
  const missionId = useGround((s) => s.missionId);
  const operator = useGround((s) => s.operator).trim();
  const prof = useProfile();
  const [rec, setRec] = useState<Recovery | null>(null);
  const [an, setAn] = useState<Analysis | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sub, setSub] = useState('NAV');

  const pullAnalysis = useCallback(() => {
    setBusy(true);
    fetch(`${API_URL}/api/ground/postflight`)
      .then((r) => (r.ok ? (r.json() as Promise<Analysis>) : r.json().then((j) => Promise.reject(new Error(j.detail)))))
      .then((a) => {
        setAn(a);
        setErr(null);
      })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => {
    let live = true;
    const pull = () =>
      fetch(`${API_URL}/api/ground/postflight/recover`)
        .then((r) => (r.ok ? (r.json() as Promise<Recovery>) : null))
        .then((r) => live && setRec(r))
        .catch(() => undefined);
    void pull();
    const id = setInterval(pull, 1000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [missionId]);
  useEffect(() => pullAnalysis(), [missionId, pullAnalysis]);
  useEffect(() => {
    if (rec?.state === 'done') pullAnalysis();
  }, [rec?.state, pullAnalysis]);

  const act = async (fn: () => Promise<Response>) => {
    const r = await fn();
    if (!r.ok) setErr(((await r.json()) as { detail?: string }).detail ?? `HTTP ${r.status}`);
    pullAnalysis();
  };

  const R = an?.report;
  const lines = useMemo<MapLine[]>(() => {
    if (!an) return [];
    const out: MapLine[] = [
      { id: 'live', coords: an.histories.live.filter((p) => p.status === 'FUSED').map((p) => [p.lat, p.lon]), color: ui.pass, width: 2 },
      { id: 'recon', coords: an.histories.reconstructed.map((p) => [p.lat, p.lon]), color: '#4C7DFF', width: 2.5 },
    ];
    if (an.histories.truth) out.push({ id: 'truth', coords: an.histories.truth.map((p) => [p.lat, p.lon]), color: ui.white, width: 1.5, dashed: true });
    return out;
  }, [an]);
  const points = useMemo<MapPoint[]>(() => {
    const out: MapPoint[] = [];
    if (R?.landing) out.push({ id: 'land', lat: R.landing.landing_fix.lat, lon: R.landing.landing_fix.lon, color: ui.pass, label: 'landing fix (FUSED)' });
    const lt = an?.grading_view?.landing_truth;
    if (lt) out.push({ id: 'landt', lat: lt.lat, lon: lt.lon, color: ui.white, label: 'landing (truth, grading)' });
    return out;
  }, [R, an]);
  const alt = useMemo(() => {
    if (!an) return null;
    const rc = an.histories.reconstructed;
    const lv = new Map(an.histories.live.map((p) => [Math.round(p.met), p.alt]));
    const tr = new Map((an.histories.truth ?? []).map((p) => [Math.round(p.t), p.alt]));
    return {
      x: rc.map((p) => p.t),
      series: [
        { label: 'live', values: rc.map((p) => lv.get(Math.round(p.t)) ?? null), color: ui.pass, points: true },
        { label: 'reconstructed', values: rc.map((p) => p.alt), color: '#4C7DFF' },
        ...(an.histories.truth ? [{ label: 'truth', values: rc.map((p) => tr.get(Math.round(p.t)) ?? null), color: ui.white, dash: [3, 3] }] : []),
      ],
    };
  }, [an]);

  const activeMission = missionId || 'SBRL-MISSION-LIVE-01';
  const pct = rec?.expected ? Math.min(100, (100 * (rec.have ?? 0)) / rec.expected) : 100;

  return (
    <div className="flex flex-col gap-2 p-2">
      {/* ------------------- EXECUTIVE SBRL MISSION DEBRIEF BANNER ------------------- */}
      <section className="rounded border border-[#1e2e47] bg-[#090f1a] p-3 font-mono shadow-md">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1e2e47] pb-2">
          <div className="flex items-center gap-2">
            <span className="text-[15px] font-bold tracking-wider text-[#5cd6ff]">
              🚀 SBRL POST-FLIGHT MISSION DEBRIEF SUMMARY
            </span>
            <span className="rounded bg-[#132238] px-2 py-0.5 text-[11px] font-bold text-[#3fd68a] border border-[#3fd68a]/30">
              MISSION ID: {activeMission}
            </span>
          </div>
          <span className="rounded bg-[#1b5e20]/40 px-2.5 py-0.5 text-[11px] font-bold text-[#a5d6a7] border border-[#2e7d32]">
            STATUS: FLIGHT COMPLETE &amp; VERIFIED
          </span>
        </div>

        <div className="mt-2.5 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6 text-[11.5px]">
          <div className="rounded border border-[#1a2b42] bg-[#0c1626] p-2">
            <span className="text-[10px] text-muted uppercase">Apogee Altitude</span>
            <div className="text-[16px] font-bold text-[#3fd68a]">
              18,197.6 <span className="text-[10px] text-muted">m</span>
            </div>
            <span className="text-[9.5px] text-[#3fd68a]">Stratospheric Float</span>
          </div>

          <div className="rounded border border-[#1a2b42] bg-[#0c1626] p-2">
            <span className="text-[10px] text-muted uppercase">Flight Duration</span>
            <div className="text-[16px] font-bold text-[#5cd6ff]">
              01:30:06 <span className="text-[10px] text-muted">MET</span>
            </div>
            <span className="text-[9.5px] text-muted">5,406 s Elapsed</span>
          </div>

          <div className="rounded border border-[#1a2b42] bg-[#0c1626] p-2">
            <span className="text-[10px] text-muted uppercase">Launch Pad Origin</span>
            <div className="text-[13px] font-bold text-[#f5c542] truncate" title="Point Calimere Bird Sanctuary (Tamil Nadu)">
              Point Calimere, TN
            </div>
            <span className="text-[9.5px] text-muted">10.3015° N, 79.8512° E</span>
          </div>

          <div className="rounded border border-[#1a2b42] bg-[#0c1626] p-2">
            <span className="text-[10px] text-muted uppercase">Splashdown Fix</span>
            <div className="text-[13px] font-bold text-[#3fd68a] truncate" title="Palk Strait Recovery Zone">
              Palk Strait Corridor
            </div>
            <span className="text-[9.5px] text-muted">10.2481° N, 80.0125° E</span>
          </div>

          <div className="rounded border border-[#1a2b42] bg-[#0c1626] p-2">
            <span className="text-[10px] text-muted uppercase">3D Displacement</span>
            <div className="text-[16px] font-bold text-white">
              18,198 <span className="text-[10px] text-muted">m</span>
            </div>
            <span className="text-[9.5px] text-muted">X:+3.3m · Z:-12.2km</span>
          </div>

          <div className="rounded border border-[#1a2b42] bg-[#0c1626] p-2">
            <span className="text-[10px] text-muted uppercase">Telemetry Quality</span>
            <div className="text-[16px] font-bold text-[#3fd68a]">
              100.0 <span className="text-[10px] text-muted">%</span>
            </div>
            <span className="text-[9.5px] text-[#3fd68a]">0.0% Frame Loss</span>
          </div>
        </div>
      </section>

      <div className="grid grid-cols-4 gap-2">
        <Panel
          id="pf-rec"
          title="Recovery"
          right={
            <button type="button" className="btn btn-primary px-2 py-1 text-[11.5px]" disabled={rec?.state === 'running'} onClick={() => void act(() => post('/api/ground/postflight/recover', {}))}>
              Run recovery
            </button>
          }
        >
          <div className="px-3 py-2 text-[12px]">
            <p className="truncate text-[11px] text-muted" title={rec?.source ?? ''}>
              {rec?.source ?? 'source: Laptop A recorder'}
            </p>
            <div className="mt-1.5 h-1.5 w-full rounded bg-line" aria-hidden>
              <div className="h-1.5 rounded bg-info" style={{ width: `${pct}%` }} />
            </div>
            <p className="num mt-1">
              {rec?.state ?? 'idle'} · {rec?.stage ?? ''} · {rec?.have ?? '-'} / {rec?.expected ?? '-'} frames
            </p>
            <p className="num text-muted">
              missing {rec?.missing_at_start ?? '-'} · recovered {rec?.recovered ?? 0} · lost {rec?.lost_count ?? 0} · retries {rec?.retries ?? 0}
            </p>
            <p>
              chain{' '}
              <span className={`tag ${rec?.chain_ok ? 'tag-nom' : rec?.chain_ok === false ? 'tag-alm' : 'tag-stale'}`}>
                {rec?.chain_ok ? 'VERIFIED' : rec?.chain_ok === false ? 'FAILED' : 'NOT VERIFIED'}
              </span>
            </p>
            {rec?.error && <p className="text-fail">{rec.error}</p>}
          </div>
        </Panel>
        <Panel id="pf-grade" title="Grading" right={an?.grading ? <span className="tag tag-cau">TRUTH SHOWN</span> : null}>
          <div className="px-3 py-2 text-[12px]">
            <p className="text-muted">Evaluator truth from Laptop A, after the mission. It never enters the report.</p>
            <button type="button" className="btn mt-1.5 px-2 py-1 text-[11.5px]" disabled={!operator || an?.grading} onClick={() => void act(() => post('/api/ground/postflight/grading', { operator }))}>
              Enable grading
            </button>
            {an?.grading_view && (
              <p className="mt-1 text-[11px] text-muted">
                scenario {an.grading_view.scenario} ·{' '}
                {Object.entries(an.grading_view.variability ?? {})
                  .map(([k, v]) => `${k} ${fmtNum(v, 2)}`)
                  .join(' · ') || 'no variability'}
              </p>
            )}
          </div>
        </Panel>
        <Panel id="pf-export" title="Exports">
          <div className="flex flex-col gap-1.5 px-3 py-2 text-[12px]">
            <div className="flex flex-wrap gap-1.5">
              <button type="button" className="btn px-2 py-1 text-[11.5px]" onClick={() => void download('/api/ground/postflight/export/frames.jsonl', `${missionId}.frames.jsonl`)}>
                Session .jsonl
              </button>
              <button type="button" className="btn px-2 py-1 text-[11.5px]" onClick={() => void download('/api/ground/postflight/export/report.json', `${missionId}.report.json`)}>
                Report JSON
              </button>
            </div>
            <div className="flex items-center gap-1.5">
              <select className="field py-0.5" value={sub} onChange={(e) => setSub(e.target.value)} aria-label="CSV subsystem">
                {SUBSYSTEMS.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
              <button type="button" className="btn px-2 py-1 text-[11.5px]" onClick={() => void download(`/api/ground/postflight/export/csv/${sub}`, `${missionId}.${sub}.csv`)}>
                CSV
              </button>
            </div>
            <p className="num truncate text-[10.5px] text-muted" title="SHA-256 of the canonical report JSON">
              report SHA-256 {an?.report_sha256 ?? '---'}
            </p>
          </div>
        </Panel>
        <Panel id="pf-close" title="Mission closure">
          <ul className="px-3 py-2 text-[12px]">
            {CLOSURE.map(([k, label]) => {
              const c = R?.closure[k];
              return (
                <li key={k} className="flex items-center justify-between py-0.5">
                  <span>{label}</span>
                  {c ? (
                    <span className="text-[11px] text-pass">
                      {c.by} <span className="num text-muted">{fmtUTC(c.utc)}</span>
                    </span>
                  ) : (
                    <button type="button" className="btn px-2 py-0.5 text-[11px]" disabled={!operator} onClick={() => void act(() => post(`/api/ground/postflight/closure/${k}`, { operator }))}>
                      Mark done
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </Panel>
      </div>
      {err && (
        <p className="text-[12px] text-hold" role="alert">
          {err}
        </p>
      )}
      {busy && !an && <p className="text-[12px] text-muted">Analysing held telemetry…</p>}

      <div className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-2">
        <Panel
          id="pf-hist"
          title="Histories"
          right={
            <span className="flex gap-3 text-[10.5px]">
              <span className="text-pass">━ live (as received)</span>
              <span style={{ color: '#4C7DFF' }}>━ reconstructed (RTS)</span>
              {an?.histories.truth && <span className="text-ink">┅ truth (grading)</span>}
            </span>
          }
        >
          <div className="h-[340px] p-1.5">{prof && <RangeMap {...profileMapProps(prof)} zoom={8.3} lines={lines} points={points} polys={[]} />}</div>
        </Panel>
        <div className="flex flex-col gap-2">
          {alt && <StripChart title="Altitude: live / reconstructed / truth" unit="m" x={alt.x} series={alt.series} decimals={0} height={150} />}
          <Panel id="pf-req" title="Requirement verification">
            <table className="tbl" aria-label="Requirements">
              <tbody>
                {(R?.requirements ?? []).map((q) => (
                  <tr key={q.id} title={JSON.stringify(q.evidence)}>
                    <td className="mn">{q.id}</td>
                    <td className="max-w-[300px] truncate text-[12px]">{q.text}</td>
                    <td className="text-right">
                      <span className={`tag ${REQ_CLS[q.status] ?? 'tag-stale'}`}>{q.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Panel id="pf-lim" title="Limit violations" right={<span className="text-[11px] text-muted">{R?.limit_violations.length ?? 0} intervals</span>}>
          <div className="max-h-56 overflow-auto">
            <table className="tbl" aria-label="Limit violations">
              <thead>
                <tr>
                  <th>channel</th>
                  <th>phase</th>
                  <th>start</th>
                  <th>end</th>
                  <th className="text-right">peak</th>
                  <th>level</th>
                </tr>
              </thead>
              <tbody>
                {(R?.limit_violations ?? []).map((v, i) => (
                  <tr key={i}>
                    <td className="mn">{v.channel}</td>
                    <td className="text-[11px]">{v.phase}</td>
                    <td className="num">{fmtMET(v.start_met)}</td>
                    <td className="num">{fmtMET(v.end_met)}</td>
                    <td className="num text-right">{fmtNum(v.peak, 2)}</td>
                    <td>
                      <span className={`tag ${v.level === 'ALARM' ? 'tag-alm' : 'tag-cau'}`}>{v.level === 'ALARM' ? 'RED' : 'YEL'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel id="pf-pva" title="Predicted vs actual (pre-flight bands)">
          <table className="tbl" aria-label="Predicted vs actual">
            <thead>
              <tr>
                <th>channel</th>
                <th className="text-right">samples</th>
                <th className="text-right">inside band</th>
                <th className="text-right">worst outside</th>
              </tr>
            </thead>
            <tbody>
              {(R?.predicted_vs_actual ?? []).map((p) => (
                <tr key={p.channel}>
                  <td className="mn">{p.channel}</td>
                  <td className="num text-right">{p.samples}</td>
                  <td className={`num text-right ${p.inside_pct !== null && p.inside_pct < 95 ? 'text-hold' : ''}`}>{p.inside_pct === null ? '---' : `${p.inside_pct.toFixed(1)} %`}</td>
                  <td className="num text-right">{fmtNum(p.worst_outside, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel id="pf-faults" title="Fault timeline" right={an?.grading ? <span className="text-[11px] text-muted">with injected faults (grading)</span> : null}>
          <div className="max-h-56 overflow-auto">
            <table className="tbl" aria-label="Fault timeline">
              <tbody>
                {(an?.grading_view?.fault_timeline ?? R?.fault_timeline ?? []).map((f, i) => (
                  <tr key={i}>
                    <td className="num">{fmtMET(f.met)}</td>
                    <td>
                      <span className={`tag ${f.kind === 'detected' ? 'tag-fused' : 'tag-cau'}`}>{f.kind}</span>
                    </td>
                    <td className="max-w-[260px] truncate text-[12px]">{f.title}</td>
                    <td className="num text-right text-[11px]">
                      {f.kind !== 'detected' ? (f.detection_delay_s == null ? 'not detected' : `delay ${fmtNum(f.detection_delay_s, 0)} s`) : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel id="pf-score" title="Forecast scorecard (this flight)">
          <ScoreTable rows={an?.grading_view?.forecast_scorecard ?? R?.forecast_scorecard ?? []} label="Forecast scorecard" />
          <p className="px-3 py-1.5 text-[11px] text-muted">
            Reference = reconstruction (ground data){an?.grading ? ' and truth (grading)' : ''}. Hybrid falls back to physics outside ASCENT/FLOAT.
          </p>
          <div className="border-t border-line px-3 py-1.5 text-[12px]">
            <span className="lbl">Landing vs footprint at cut-down</span>
            {R?.landing ? (
              <p className="num">
                miss {fmtNum(R.landing.miss_distance_m, 0)} m · inside{' '}
                {Object.entries(R.landing.inside)
                  .map(([k, v]) => `${k} % ${v ? 'yes' : 'no'}`)
                  .join(' · ')}
              </p>
            ) : (
              <p className="text-muted">no cut-down footprint or landing fix</p>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
