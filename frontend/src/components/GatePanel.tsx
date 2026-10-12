import { AnimatePresence, motion } from 'framer-motion';
import { Check, CircleCheck, CircleSlash, Hand, Loader2, Minus, OctagonX, Play, Rocket, TriangleAlert, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { gateConditions } from '../lib/checks';
import { IGNITION } from '../scene/shared';
import { HoldToCommit } from './HoldToCommit';
import { selectAtEnd, useCurrentFrame, useMission } from '../store/useMission';

const card = 'panel pointer-events-auto w-full max-w-[540px] px-4 py-3 shadow-2xl shadow-black/40';
const enter = { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: 8 }, transition: { duration: 0.22, ease: 'easeOut' } } as const;

function Cond({ ok, label }: { ok: boolean | null; label: string }) {
  const tone = ok === null ? 'text-muted' : ok ? 'text-pass' : 'text-hold';
  return (
    <li className={`inline-flex items-center gap-1 ${tone}`}>
      {ok ? <Check size={12} aria-hidden /> : ok === null ? <Minus size={12} aria-hidden /> : <X size={12} aria-hidden />}
      <span className="num">{label}</span>
      <span className="sr-only">{ok === null ? 'not evaluated' : ok ? 'met' : 'not met'}</span>
    </li>
  );
}

/** Seconds since commit, ticking at 10 Hz while the ignition sequence runs. */
function useSequenceTime(active: boolean, at: number) {
  const [e, setE] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setE((performance.now() - at) / 1000), 100);
    return () => clearInterval(id);
  }, [active, at]);
  return active ? e : 0;
}

/**
 * The launch gate. Software can only recommend GO (gate ARMED from the
 * backend); the ignition itself is a frontend-only operator commit.
 */
export function GatePanel() {
  const f = useCurrentFrame();
  const runStatus = useMission((s) => s.runStatus);
  const error = useMission((s) => s.error);
  const summary = useMission((s) => s.summary);
  const atEnd = useMission(selectAtEnd);
  const commit = useMission((s) => s.commit);
  const commitIgnition = useMission((s) => s.commitIgnition);
  const holdCommit = useMission((s) => s.holdCommit);
  const resumeCommit = useMission((s) => s.resumeCommit);
  const e = useSequenceTime(commit.status === 'committed', commit.at);

  let key: string;
  let body: React.ReactNode;

  if (!f) {
    key = `empty-${runStatus}`;
    body = (
      <div className={card} role="status">
        <div className="flex items-center gap-2 text-[13px] text-muted">
          {runStatus === 'loading' || runStatus === 'streaming' ? (
            <>
              <Loader2 size={15} className="animate-spin" aria-hidden /> {runStatus === 'streaming' ? 'Opening live stream…' : 'Running simulation…'}
            </>
          ) : runStatus === 'error' ? (
            <>
              <TriangleAlert size={15} className="text-hold" aria-hidden /> {error ?? 'Run failed'}
            </>
          ) : (
            <>Standby · pick a scenario and press Run</>
          )}
        </div>
      </div>
    );
  } else if (f.is_abort) {
    key = 'abort';
    body = (
      <div className={`${card} border-fail/70 bg-fail/[0.08]`} role="alert">
        <div className="flex items-center gap-2.5">
          <OctagonX size={22} className="animate-abort-flash text-fail" aria-hidden />
          <div className="flex-1">
            <div className="font-display text-[17px] font-semibold tracking-wide text-fail">MISSION ABORTED</div>
            <div className="num text-[12.5px] text-ink">{summary?.abort_reason ?? `Pressurant leak detected: ${f.leak_status}`}</div>
          </div>
        </div>
        <div className="mt-1.5 text-[11px] text-muted">
          Gate NO-GO · tank venting continues ·{' '}
          {summary?.fromBackend ? 'reason from POST /api/simulate summary' : 'reason derived from the abort frame (same rule as backend)'}
        </div>
      </div>
    );
  } else if (f.gate_status === 'ARMED' && commit.status === 'committed') {
    const settled = e >= IGNITION.cardAt;
    key = settled ? 'committed' : 'sequence';
    body = settled ? (
      <div className={`${card} border-armed/50`} role="status">
        <div className="flex items-center gap-2.5">
          <Rocket size={20} className="text-armed" aria-hidden />
          <div className="flex-1">
            <div className="font-display text-[17px] font-semibold tracking-wide text-armed">IGNITION COMMITTED (simulation)</div>
            <div className="text-[12px] text-muted">
              Operator commit at <span className="num text-ink">T+{Math.round(f.time_s)} s</span>, {(f.altitude_m / 1000).toFixed(2)} km. Frontend
              animation only; no backend call.
            </div>
          </div>
        </div>
      </div>
    ) : (
      <div className={`${card} border-armed/50`} role="status">
        <div className="flex items-center gap-2 text-[13px]">
          <Loader2 size={15} className="animate-spin text-armed" aria-hidden />
          <span className="font-semibold text-armed">Ignition sequence</span>
          <span className="num text-muted">
            {e < IGNITION.clampsOpen ? 'motor glow' : e < IGNITION.liftoff ? 'release clamps open' : 'liftoff'} · {e.toFixed(1)} s
          </span>
        </div>
      </div>
    );
  } else if (f.gate_status === 'ARMED' && commit.status === 'held') {
    key = 'held';
    body = (
      <div className={`${card} border-hold/60`} role="status">
        <div className="flex items-center gap-2.5">
          <Hand size={20} className="text-hold" aria-hidden />
          <div className="flex-1">
            <div className="font-display text-[16px] font-semibold tracking-wide text-hold">OPERATOR HOLD</div>
            <div className="text-[12px] text-muted">Gate is ARMED but the operator has not committed.</div>
          </div>
          <button type="button" className="btn" onClick={resumeCommit}>
            <Play size={14} aria-hidden /> Release hold
          </button>
        </div>
      </div>
    );
  } else if (f.gate_status === 'ARMED') {
    key = 'armed';
    body = (
      <div className={`${card} border-armed/50`} role="alert">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-[200px] flex-1">
            <div className="flex items-center gap-2">
              <CircleCheck size={18} className="text-armed" aria-hidden />
              <span className="font-display text-[17px] font-semibold tracking-wide text-armed">GO RECOMMENDED</span>
            </div>
            <div className="mt-0.5 text-[12px] text-ink">Operator must commit. Software recommends; a human commits.</div>
            <div className="num mt-0.5 text-[11px] text-muted">recommendation = GO_RECOMMENDED (ground station) · 5 gates + no leak</div>
          </div>
          <HoldToCommit onCommit={commitIgnition} />
          <button type="button" className="btn border-hold/50 text-hold hover:bg-hold/10" onClick={holdCommit}>
            <Hand size={14} aria-hidden /> Hold
          </button>
        </div>
      </div>
    );
  } else if (atEnd && summary?.verdict === 'NO_GO') {
    key = 'terminated';
    body = (
      <div className={card} role="status">
        <div className="flex items-center gap-2.5">
          <CircleSlash size={20} className="text-muted" aria-hidden />
          <div>
            <div className="font-display text-[16px] font-semibold tracking-wide text-ink">NO RECOMMENDATION</div>
            <div className="text-[12px] text-muted">Simulation horizon reached without GO or ABORT. No launch.</div>
          </div>
        </div>
      </div>
    );
  } else {
    const c = gateConditions(f);
    key = 'nogo';
    body = (
      <div className={`${card} border-hold/40`} role="status">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-hold" aria-hidden />
            <span className="font-display text-[15px] font-semibold tracking-wide text-hold">LAUNCH GATE · NO-GO</span>
          </div>
          <ul className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px]" aria-label="Commit gates (ground station)">
            {c.map((g) => (
              <Cond key={g.id} ok={g.ok} label={g.label} />
            ))}
          </ul>
          {runStatus === 'streaming' && (
            <span className="inline-flex items-center gap-1 text-[11px] text-info">
              <Loader2 size={12} className="animate-spin" aria-hidden /> receiving stream
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="pointer-events-none flex justify-center">
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={key} {...enter} className="flex w-full justify-center">
          {body}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
