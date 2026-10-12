import { CircleCheck, CircleDashed, CircleX, Hand, ShieldCheck } from 'lucide-react';
import type { Gate, GroundFrame } from './api';
import { useFlight } from './store';

const ORDER = ['float', 'nav', 'motor', 'swing', 'impact'] as const;

function gateValue(g: Gate): string {
  const v = g.value as any;
  switch (g.id) {
    case 'float':
      return v ? `${(v.z_m / 1000).toFixed(2)} km · v̄z ${v.vz_m_s.toFixed(2)} m/s` : '--';
    case 'nav':
      return v ? `χ² h ${v.chi2_h.toFixed(1)} / ${(g.limit as any).chi2_h} · v ${v.chi2_v.toFixed(1)} / ${(g.limit as any).chi2_v}` : '--';
    case 'motor':
      return typeof v === 'number' ? `${v.toFixed(1)} °C ≥ ${g.limit} °C` : '--';
    case 'swing':
      return typeof v === 'number' ? `${v.toFixed(2)}° ≤ ${g.limit}°` : '--';
    case 'impact':
      return v !== null && v !== undefined ? `${v}/${g.limit} cases inside` : '--';
    case 'tank':
      return g.passed === false ? `LEAK ${typeof v === 'number' ? `est. ${v.toFixed(0)} µm` : ''}` : 'no leak';
    default:
      return String(v ?? '--');
  }
}

function GateRow({ g }: { g: Gate }) {
  const Icon = g.passed === null ? CircleDashed : g.passed ? CircleCheck : CircleX;
  const tone = g.passed === null ? 'text-muted' : g.passed ? 'text-pass' : 'text-fail';
  const label = g.passed === null ? 'NOT EVALUATED' : g.passed ? 'PASS' : 'FAIL';
  return (
    <li className="rounded-lg border border-line/70 bg-panel2/40 px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <Icon size={14} className={tone} aria-hidden />
        <span className="flex-1 truncate text-[12.5px] font-medium text-ink">{g.name}</span>
        <span className={`text-[10.5px] font-semibold ${tone}`}>{label}</span>
      </div>
      <div className="num mt-0.5 truncate text-[11.5px] text-ink/90" title={g.detail}>
        {gateValue(g)}
        {g.margin !== null && g.passed !== null && <span className="ml-1.5 text-muted">margin {g.margin.toFixed(g.unit === 'm' ? 0 : 2)} {g.unit}</span>}
      </div>
      <div className="truncate text-[10.5px] text-muted" title={g.detail}>
        {g.detail}
      </div>
    </li>
  );
}

/** The five launch-commit gates, the leak abort criterion, the recommendation and the operator controls. */
export function GatesPanel({ frame }: { frame: GroundFrame | null }) {
  const live = useFlight((s) => s.live);
  const operator = useFlight((s) => s.operator);
  const busy = useFlight((s) => s.busy);
  const command = useFlight((s) => s.command);
  if (!frame) {
    return (
      <section className="panel p-3 text-[12.5px] text-muted" aria-label="Launch-commit gates">
        Start or attach a mission to see the launch-commit gates.
      </section>
    );
  }
  const rec = frame.recommendation;
  const op = frame.operator;
  const canCommit = live && rec === 'GO_RECOMMENDED' && op.state === 'none' && operator.trim().length > 0;
  const recCls = rec === 'GO_RECOMMENDED' ? 'border-armed/50 bg-armed/10 text-armed' : rec === 'ABORT_RECOMMENDED' ? 'border-fail/60 bg-fail/10 text-fail' : 'border-hold/40 bg-hold/10 text-hold';

  return (
    <section className="panel flex min-h-0 flex-1 flex-col" aria-labelledby="gates-title">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <ShieldCheck size={15} className="text-muted" aria-hidden />
        <h2 id="gates-title" className="panel-title">
          Launch-commit gates
        </h2>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
        {op.state === 'committed' || op.state === 'terminated' ? (
          <div className="rounded-lg border border-line px-3 py-2 text-muted" role="status">
            <div className="font-display text-[15px] font-semibold tracking-wide text-ink">GATES CLOSED</div>
            <div className="text-[11px]">
              Operator {op.state} at T+{Math.round(op.at_t ?? 0)} s; the gates below are shown for the record only.
            </div>
          </div>
        ) : (
        <div className={`rounded-lg border px-3 py-2 ${recCls}`} role="status" aria-live="polite">
          <div className="font-display text-[15px] font-semibold tracking-wide">{rec.replace('_', ' ')}</div>
          <div className="text-[11px] text-ink/80">
            {rec === 'GO_RECOMMENDED'
              ? 'Software recommends; the operator commits.'
              : rec === 'ABORT_RECOMMENDED'
                ? 'Do not launch. Terminate (cut-down without launch) is the operator’s call.'
                : 'All five gates must pass for 10 s.'}
          </div>
        </div>
        )}
        <div className="rounded-lg border border-line/70 bg-panel2/40 p-2.5">
          <div className="mb-1.5 text-[11px] text-muted">
            Operator state: <span className="num text-ink">{op.state}</span>
            {op.by && <span> · {op.by}</span>}
          </div>
          {op.state === 'committed' ? (
            <div className="text-[12.5px] font-semibold text-armed">COMMITTED (simulation) by {op.by}</div>
          ) : op.state === 'terminated' ? (
            <div className="text-[12.5px] font-semibold text-hold">TERMINATED by {op.by}: cut-down without launch</div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {rec === 'GO_RECOMMENDED' && op.state === 'none' && (
                <button type="button" className="btn border-pass/50 text-pass" onClick={() => void command('commit')} disabled={!canCommit || busy}>
                  Record GO (operator)
                </button>
              )}
              {op.state === 'held' ? (
                <button type="button" className="btn" onClick={() => void command('release')} disabled={!live || busy}>
                  Release hold
                </button>
              ) : (
                <button type="button" className="btn border-hold/50 text-hold" onClick={() => void command('hold')} disabled={!live || busy}>
                  <Hand size={14} aria-hidden /> Hold
                </button>
              )}
              {['ASCENT', 'FLOAT'].includes(frame.vehicle_phase) && (
                <button type="button" className="btn border-fail/50 text-fail" onClick={() => void command('terminate')} disabled={!live || busy || !operator.trim()}>
                  Terminate (cut down)
                </button>
              )}
            </div>
          )}
          {!operator.trim() && <div className="mt-1.5 text-[11px] text-hold">Enter the operator name (bottom bar) to enable commands.</div>}
          {!live && <div className="mt-1.5 text-[11px] text-hold">Replaying the past: go live to act.</div>}
        </div>
        <ul className="flex flex-col gap-1.5">
          {ORDER.map((k) => (
            <GateRow key={k} g={frame.gates[k]} />
          ))}
          <GateRow g={{ ...frame.tank_criterion, id: 'tank' }} />
        </ul>

      </div>
    </section>
  );
}
