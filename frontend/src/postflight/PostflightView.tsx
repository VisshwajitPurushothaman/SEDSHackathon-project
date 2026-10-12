import { CircleCheck, CircleDashed, CircleHelp, CircleX, Download, Eye, EyeOff, FileCheck2, HardDriveDownload, ShieldCheck } from 'lucide-react';
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { missionsApi, type MissionSummary } from '../flight/api';
import { useFlight } from '../flight/store';
import { fmtT } from '../flight/ui';
import { ui } from '../theme/tokens';
import { postflightApi, type FaultRow, type Postflight, type RecoveryStatus, type Requirement } from './api';

const PostflightMap = lazy(() => import('./PostflightMap'));

const REQ_STYLE: Record<Requirement['status'], { cls: string; icon: typeof CircleCheck }> = {
  PASS: { cls: 'border-pass/40 bg-pass/10 text-pass', icon: CircleCheck },
  FAIL: { cls: 'border-fail/60 bg-fail/10 text-fail', icon: CircleX },
  'NOT TESTED': { cls: 'border-line bg-panel2 text-muted', icon: CircleDashed },
  INCONCLUSIVE: { cls: 'border-hold/50 bg-hold/10 text-hold', icon: CircleHelp },
};

function Card({ title, icon: Icon, children, right }: { title: string; icon: typeof CircleCheck; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="panel flex min-h-0 flex-col" aria-label={title}>
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <Icon size={15} className="text-muted" aria-hidden />
        <h2 className="panel-title flex-1">{title}</h2>
        {right}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">{children}</div>
    </section>
  );
}

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return '--';
  if (typeof v === 'number') return Math.abs(v) >= 1e5 || (Math.abs(v) < 1e-3 && v !== 0) ? v.toExponential(2) : Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'object') return JSON.stringify(v).slice(0, 90);
  return String(v);
}

function RecoveryCard({ status, onRecover, busy, finished }: { status: RecoveryStatus | null; onRecover: () => void; busy: boolean; finished: boolean }) {
  const s = status ?? { state: 'idle' as const };
  const pct = s.expected ? (100 * (s.have ?? 0)) / s.expected : 0;
  const stages = s.progress ?? [];
  const rough = stages.filter((p) => p.stage.startsWith('rough')).pop();
  return (
    <Card
      title="Recover"
      icon={HardDriveDownload}
      right={
        <button type="button" className="btn btn-primary px-2 py-1 text-xs" onClick={onRecover} disabled={busy || !finished || s.state === 'running' || s.state === 'done'}>
          {s.state === 'done' ? 'Recovered' : s.state === 'running' ? 'Recovering…' : 'Run recovery'}
        </button>
      }
    >
      {!finished && <p className="text-[12px] text-hold">Recovery is available after landing (payload recovered).</p>}
      {s.state === 'idle' && finished && <p className="text-[12px] text-muted">Downloads every record that did not arrive live: every 10th missing record first (rough picture), then the rest in batches of 200, each HMAC-verified.</p>}
      {s.state !== 'idle' && (
        <div className="flex flex-col gap-2 text-[12px]">
          <div>
            <div className="mb-1 flex justify-between">
              <span className="text-muted">{s.stage}</span>
              <span className="num text-ink">
                {s.have?.toLocaleString()} / {s.expected?.toLocaleString()} records
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full bg-info transition-[width] duration-300" style={{ width: `${pct}%` }} />
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            <dt className="text-muted">received live</dt>
            <dd className="num text-right">{s.received_live?.toLocaleString()}</dd>
            <dt className="text-muted">missing at start</dt>
            <dd className="num text-right">{s.missing_at_start?.toLocaleString()}</dd>
            <dt className="text-muted">rough picture</dt>
            <dd className="num text-right">{rough ? `${rough.recovered.toLocaleString()} records` : '--'}</dd>
            <dt className="text-muted">recovered</dt>
            <dd className="num text-right">{s.recovered?.toLocaleString()}</dd>
            <dt className="text-muted">retries / failed batches</dt>
            <dd className="num text-right">
              {s.retries} / {s.failed_batches}
            </dd>
            <dt className="text-muted">lost records</dt>
            <dd className={`num text-right ${s.lost_count ? 'text-fail' : 'text-pass'}`}>{s.lost_count ?? 0}</dd>
            <dt className="text-muted">hash chain</dt>
            <dd className={`num text-right ${s.chain_ok ? 'text-pass' : s.chain_ok === false ? 'text-fail' : 'text-muted'}`}>
              {s.chain_ok === true ? 'verified end to end' : s.chain_ok === false ? `BROKEN at seq ${s.chain_first_bad ?? (s.hmac_failures ?? [])[0]}` : s.state === 'done' ? 'incomplete' : '…'}
            </dd>
          </dl>
          {!!s.lost?.length && <p className="num text-[11px] text-fail">lost seq: {s.lost.slice(0, 20).join(', ')}{s.lost.length > 20 ? '…' : ''}</p>}
        </div>
      )}
    </Card>
  );
}

function AltitudeChart({ report, reveal }: { report: Postflight; reveal: boolean }) {
  const data = useMemo(() => {
    const rows = new Map<number, Record<string, number>>();
    const put = (t: number, k: string, v: number) => {
      const key = Math.round(t / 5) * 5;
      rows.set(key, { ...(rows.get(key) ?? { t: key }), [k]: v / 1000 });
    };
    (report.operator_track ?? []).forEach((p) => p.alt !== undefined && put(p.t, p.status === 'PREDICTED' ? 'predicted' : 'live', p.alt));
    (report.reconstruction ?? []).forEach((p) => p.u !== undefined && put(p.t, 'recon', p.u));
    if (reveal) (report.truth_track ?? []).forEach((p) => p.z !== undefined && put(p.t, 'truth', p.z));
    return [...rows.values()].sort((a, b) => a.t - b.t);
  }, [report, reveal]);
  return (
    <div className="h-full min-h-[180px]">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -10 }}>
          <CartesianGrid stroke={ui.border} strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} tickFormatter={(t: number) => fmtT(t).replace('T+', '')} stroke={ui.border} tick={{ fill: ui.muted, fontSize: 10 }} />
          <YAxis stroke={ui.border} tick={{ fill: ui.muted, fontSize: 10 }} unit=" km" domain={[0, 'auto']} allowDataOverflow />
          <Tooltip contentStyle={{ background: ui.panel, border: `1px solid ${ui.border}`, fontSize: 11 }} labelFormatter={(t: number) => fmtT(t)} formatter={(v: number) => `${v.toFixed(2)} km`} />
          <Line dataKey="live" name="operator saw (live)" stroke={ui.pass} dot={false} strokeWidth={1.8} isAnimationActive={false} connectNulls={false} />
          <Line dataKey="predicted" name="operator saw (predicted)" stroke={ui.hold} dot={false} strokeWidth={1.8} strokeDasharray="4 3" isAnimationActive={false} connectNulls={false} />
          <Line dataKey="recon" name="reconstructed" stroke={ui.info} dot={false} strokeWidth={1.2} isAnimationActive={false} />
          {reveal && <Line dataKey="truth" name="truth" stroke={ui.text} dot={false} strokeWidth={1} strokeDasharray="2 3" isAnimationActive={false} />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function RequirementsTable({ reqs }: { reqs: Requirement[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <ul className="flex flex-col gap-1.5">
      {reqs.map((r) => {
        const st = REQ_STYLE[r.status];
        const Icon = st.icon;
        return (
          <li key={r.id} className="rounded-lg border border-line/70 bg-panel2/40 px-2.5 py-1.5">
            <button type="button" className="flex w-full items-center gap-2 text-left" onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id}>
              <span className="num shrink-0 text-[10.5px] text-faint">{r.id}</span>
              <span className="min-w-0 flex-1 text-[12px] text-ink">{r.text}</span>
              <span className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${st.cls}`}>
                <Icon size={11} aria-hidden /> {r.status}
              </span>
            </button>
            {open === r.id && (
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-[10.5px]">
                {Object.entries(r.evidence).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-faint">{k}</dt>
                    <dd className="num truncate text-ink/80" title={JSON.stringify(v)}>
                      {fmtVal(v)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function FaultsTable({ faults, reveal, onAction }: { faults: FaultRow[]; reveal: boolean; onAction: (id: string, text: string) => void }) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  faults = reveal ? faults : faults.filter((f) => f.detected_at !== null);
  if (!faults.length) return <p className="text-[12px] text-muted">No faults detected{reveal ? ' or injected' : ''}.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {faults.map((f) => (
        <li key={f.id} className="rounded-lg border border-line/70 bg-panel2/40 px-2.5 py-2 text-[11.5px]">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-ink">{reveal ? f.kind : f.detected_by ?? f.kind}</span>
            <span className="num text-muted">{f.detected_at !== null ? `detected ${fmtT(f.detected_at)}` : 'not detected'}</span>
          </div>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px]">
            {reveal && f.injected && (
              <>
                <dt className="text-faint">injected (truth)</dt>
                <dd className="num text-ink/80">{fmtVal(f.injected)}</dd>
                <dt className="text-faint">detection delay</dt>
                <dd className="num text-ink/80">{f.detection_delay_s !== null ? `${f.detection_delay_s.toFixed(0)} s` : '--'}</dd>
              </>
            )}
            {f.detected_by && (
              <>
                <dt className="text-faint">detected by</dt>
                <dd className="text-ink/80">{f.detected_by}</dd>
              </>
            )}
            {f.estimated_diameter_um !== undefined && (
              <>
                <dt className="text-faint">estimated size</dt>
                <dd className="num text-ink/80">
                  {f.estimated_diameter_um ? `${f.estimated_diameter_um.toFixed(0)} µm` : '--'}
                  {reveal && f.injected && typeof f.injected.diameter_um === 'number' && ` (true ${f.injected.diameter_um} µm)`}
                </dd>
              </>
            )}
            {f.hindsight_onset_t !== undefined && (
              <>
                <dt className="text-faint">hindsight onset</dt>
                <dd className="num text-ink/80">{f.hindsight_onset_t !== null ? fmtT(f.hindsight_onset_t) : '--'}</dd>
              </>
            )}
          </dl>
          <div className="mt-1.5">
            {f.corrective_action ? (
              <p className="text-[11px] text-ink/90">
                <span className="text-muted">Corrective action ({f.corrective_action.by}):</span> {f.corrective_action.text}
              </p>
            ) : (
              <form
                className="flex gap-1.5"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (drafts[f.id]?.trim()) onAction(f.id, drafts[f.id]);
                }}
              >
                <label className="sr-only" htmlFor={`ca-${f.id}`}>
                  Corrective action for {f.kind}
                </label>
                <input
                  id={`ca-${f.id}`}
                  className="min-w-0 flex-1 rounded-md border border-line bg-panel px-2 py-1 text-[11px] text-ink placeholder:text-faint"
                  placeholder="Corrective action…"
                  value={drafts[f.id] ?? ''}
                  onChange={(e) => setDrafts({ ...drafts, [f.id]: e.target.value })}
                />
                <button type="submit" className="btn px-2 py-0.5 text-[11px]">
                  Save
                </button>
              </form>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

function ScorecardCard({ report }: { report: Postflight }) {
  const s = report.scorecard;
  if (!s) return null;
  return (
    <div className="flex flex-col gap-2 text-[11.5px]">
      <p className="text-[10.5px] text-hold">Grading mode: compared with simulator truth. Not available to the operator in flight.</p>
      <div>
        <h3 className="mb-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Region coverage vs claimed</h3>
        <table className="num w-full text-[11px]">
          <thead>
            <tr className="text-faint">
              <th className="text-left font-normal">level</th>
              <th className="text-right font-normal">claimed</th>
              <th className="text-right font-normal">achieved</th>
              <th className="text-right font-normal">snapshots</th>
            </tr>
          </thead>
          <tbody>
            {(['50', '90', '99'] as const).map((k) => {
              const c = s.region_coverage[k];
              return (
                <tr key={k}>
                  <td>{k} %</td>
                  <td className="text-right">{(c.claimed * 100).toFixed(0)} %</td>
                  <td className="text-right">{c.achieved === null ? '--' : `${(c.achieved * 100).toFixed(0)} %`}</td>
                  <td className="text-right">{c.snapshots}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
        <dt className="text-muted">position error at reconnect</dt>
        <dd className="num text-right">{s.reconnect_errors.length ? s.reconnect_errors.map((r) => `${r.error_vs_truth_m.toFixed(0)} m after ${r.blackout_s.toFixed(0)} s`).join('; ') : 'no blackout'}</dd>
        <dt className="text-muted">false alarms</dt>
        <dd className={`num text-right ${s.false_alarms.length ? 'text-fail' : 'text-pass'}`}>{s.false_alarms.length ? s.false_alarms.map((f) => f.key).join(', ') : 'none'}</dd>
        <dt className="text-muted">landing error</dt>
        <dd className="num text-right">{s.landing ? `${s.landing.error_m.toFixed(0)} m (${s.landing.inside_90 ? 'inside' : 'outside'} 90 %)` : '--'}</dd>
        {s.leak && (
          <>
            <dt className="text-muted">leak size</dt>
            <dd className="num text-right">
              est. {s.leak.estimated_um?.toFixed(0) ?? '--'} µm vs true {s.leak.injected_um} µm
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

export default function PostflightView() {
  const attached = useFlight((s) => s.missionId);
  const operator = useFlight((s) => s.operator);
  const setOperator = useFlight((s) => s.setOperator);
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const [id, setId] = useState<string | null>(attached);
  const [report, setReport] = useState<Postflight | null>(null);
  const [recovery, setRecovery] = useState<RecoveryStatus | null>(null);
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hash, setHash] = useState<string | null>(null);
  const currentId = useRef(id);
  currentId.current = id;

  useEffect(() => {
    let alive = true;
    const refresh = () => missionsApi.list().then((ms) => {
      if (!alive) return;
      setMissions(ms);
      if (!id) {
        const fin = ms.filter((m) => m.state === 'finished');
        if (fin.length) setId(fin[fin.length - 1].id);
      }
    }).catch((e) => { if (alive) setError((e as Error).message); });
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => { alive = false; clearInterval(timer); };
  }, [id]);

  const selected = missions.find((m) => m.id === id) ?? null;
  const finished = selected?.state === 'finished';

  const load = useCallback(async () => {
    if (!id || !finished) return;
    try {
      const [nextReport, nextRecovery] = await Promise.all([postflightApi.report(id), postflightApi.recoverStatus(id)]);
      if (currentId.current !== id) return;
      setReport(nextReport);
      setRecovery(nextRecovery);
      setError(null);
    } catch (e) {
      if (currentId.current === id) setError((e as Error).message);
    }
  }, [id, finished]);

  useEffect(() => {
    setReport(null);
    setHash(null);
    setRecovery(null);
    setReveal(false);
    setError(null);
    void load();
  }, [load]);

  // Poll recovery progress while it runs, then reload the report (requirements depend on it).
  useEffect(() => {
    if (!id || recovery?.state !== 'running') return;
    const timer = setInterval(async () => {
      try {
        const st = await postflightApi.recoverStatus(id);
        if (currentId.current !== id) return;
        setRecovery(st);
        if (st.state === 'done') {
          clearInterval(timer);
          void load();
        }
      } catch (e) {
        if (currentId.current === id) setError((e as Error).message);
      }
    }, 300);
    return () => clearInterval(timer);
  }, [id, recovery?.state, load]);

  const run = async () => {
    if (!id) return;
    setBusy(true);
    try {
      setRecovery(await postflightApi.recover(id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const needOp = () => {
    if (!operator.trim()) {
      setError('Enter the operator name first');
      return false;
    }
    return true;
  };

  const exportJson = async () => {
    if (!id) return;
    setBusy(true);
    try {
      const res = await fetch(postflightApi.exportUrl(id, 'json'));
      if (!res.ok) throw new Error(`Export failed: HTTP ${res.status}`);
      const raw = await res.text();
      const body = JSON.parse(raw);
      setHash(body.report_sha256);
      // Preserve numeric representations used by the server's canonical report hash.
      const blob = new Blob([raw], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${id}-mission.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2 text-[12px]">
        <label htmlFor="pf-mission" className="text-muted">
          Mission
        </label>
        <select id="pf-mission" className="rounded-md border border-line bg-panel2 px-2 py-1 text-xs text-ink" value={id ?? ''} onChange={(e) => setId(e.target.value || null)}>
          <option value="">select…</option>
          {missions.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} · {m.state}
              {m.end_reason ? ` (${m.end_reason})` : ''}
            </option>
          ))}
        </select>
        <label htmlFor="pf-operator" className="sr-only">
          Operator name
        </label>
        <input id="pf-operator" className="w-36 rounded-md border border-line bg-panel2 px-2 py-1 text-xs text-ink placeholder:text-faint" placeholder="Operator name" value={operator} onChange={(e) => setOperator(e.target.value)} />
        {report?.grading && (
          <button type="button" className={`btn px-2 py-1 text-xs ${reveal ? 'border-hold/60 text-hold' : ''}`} onClick={() => setReveal(!reveal)} aria-pressed={reveal}>
            {reveal ? <EyeOff size={13} aria-hidden /> : <Eye size={13} aria-hidden />}
            {reveal ? 'Hide truth' : 'Reveal truth / grade'}
          </button>
        )}
        {selected && !finished && <span className="text-hold">Mission still {selected.state}: post-flight opens after landing.</span>}
        {error && (
          <span className="text-hold" role="alert">
            {error}
          </span>
        )}
      </div>

      {!report ? (
        <div className="grid flex-1 place-items-center p-6 text-sm text-muted">
          {id ? (finished ? 'Loading post-flight analysis…' : 'Waiting for the mission to finish.') : 'Fly a mission in the Flight tab, then come back here.'}
        </div>
      ) : (
        <main className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[360px_minmax(0,1fr)_360px] lg:overflow-hidden">
          <div className="flex min-h-0 flex-col gap-3">
            <div className="shrink-0">
              <RecoveryCard status={recovery} onRecover={() => void run()} busy={busy} finished={finished} />
            </div>
            <div className="flex min-h-0 flex-1 flex-col">
              <Card title="Requirement verification" icon={ShieldCheck}>
                <RequirementsTable reqs={report.requirements} />
              </Card>
            </div>
          </div>

          <div className="flex min-h-[520px] flex-col gap-3 lg:min-h-0">
            <section className="relative min-h-[260px] flex-[3]" aria-label="Map of the three histories">
              <Suspense fallback={<div className="grid h-full place-items-center text-xs text-faint">Loading map…</div>}>
                <PostflightMap report={report} reveal={reveal} />
              </Suspense>
            </section>
            <section className="panel min-h-[200px] flex-[2] p-2" aria-label="Altitude versus time">
              <div className="px-1 text-[11px] text-muted">Altitude vs time: what the operator saw, the reconstruction{reveal ? ', and truth' : ''}</div>
              <AltitudeChart report={report} reveal={reveal} />
            </section>
          </div>

          <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
            <Card title="Fault analysis" icon={CircleHelp}>
              <FaultsTable
                faults={report.faults}
                reveal={reveal}
                onAction={async (fid, text) => {
                  if (!id || !needOp()) return;
                  await postflightApi.action(id, fid, operator, text).catch((e) => setError((e as Error).message));
                  void load();
                }}
              />
            </Card>
            {reveal && report.scorecard && (
              <Card title="Scorecard (grading)" icon={FileCheck2}>
                <ScorecardCard report={report} />
              </Card>
            )}
            <Card title="Export & closure" icon={Download}>
              <div className="flex flex-col gap-2 text-[12px]">
                <div className="flex flex-wrap gap-2">
                  <a className="btn px-2 py-1 text-xs" href={id ? postflightApi.exportUrl(id, 'csv') : '#'} download>
                    <Download size={13} aria-hidden /> Telemetry CSV
                  </a>
                  <button type="button" className="btn px-2 py-1 text-xs" onClick={() => void exportJson()} disabled={busy}>
                    <Download size={13} aria-hidden /> Report + records JSON
                  </button>
                </div>
                {hash && (
                  <p className="num break-all text-[10.5px] text-muted">
                    report SHA-256: <span className="text-ink">{hash}</span>
                  </p>
                )}
                <ul className="flex flex-col gap-1">
                  {Object.entries(report.closure).map(([k, c]) => (
                    <li key={k} className="flex items-center gap-2">
                      {c.done ? <CircleCheck size={14} className="text-pass" aria-hidden /> : <CircleDashed size={14} className="text-muted" aria-hidden />}
                      <span className="flex-1">{c.title}</span>
                      {c.done ? (
                        <span className="text-[10.5px] text-muted">{c.done.by}</span>
                      ) : (
                        <button
                          type="button"
                          className="btn px-2 py-0.5 text-[11px]"
                          onClick={async () => {
                            if (!id || !needOp()) return;
                            await postflightApi.closure(id, k, operator).catch((e) => setError((e as Error).message));
                            void load();
                          }}
                        >
                          Mark done
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            </Card>
          </div>
        </main>
      )}
    </div>
  );
}
