import { Pause, Play, Radio, SatelliteDish, UserRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import { missionsApi, type GroundFrame, type MissionSummary } from './api';
import { useFlight } from './store';
import { fmtT } from './ui';

/** LIVE (fresh fused fixes) vs PREDICTED (blackout) badge with the last fix time. Never a frozen "live" value. */
export function LinkBadge({ frame }: { frame: GroundFrame | null }) {
  if (!frame) return null;
  if (frame.predicting) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-hold/60 bg-hold/15 px-2.5 py-1 text-xs font-semibold text-hold" role="status">
        <SatelliteDish size={13} aria-hidden /> PREDICTED · last fix {fmtT(frame.last_fix_t)}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-pass/40 bg-pass/10 px-2.5 py-1 text-xs font-semibold text-pass" role="status">
      <span className="h-1.5 w-1.5 animate-status-pulse rounded-full bg-pass" aria-hidden /> LIVE · fix {fmtT(frame.last_fix_t)}
    </span>
  );
}

export function FlightDock() {
  const s = useFlight();
  const frame = s.frames[s.cursor] ?? null;
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const refresh = () => void missionsApi.list().then(setMissions).catch(() => setMissions([]));
  useEffect(refresh, []);

  // Replay clock.
  useEffect(() => {
    if (!s.playing) return;
    const id = setInterval(() => useFlight.getState().tick(), 120);
    return () => clearInterval(id);
  }, [s.playing]);

  return (
    <footer className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line bg-panel/85 px-4 py-2.5 backdrop-blur-panel" aria-label="Mission control">
      <div className="flex items-center gap-2">
        <label className="text-[11px] text-muted" htmlFor="mission">
          Mission
        </label>
        <select
          id="mission"
          className="rounded-md border border-line bg-panel2 px-2 py-1 text-xs text-ink"
          value={s.missionId ?? ''}
          onFocus={refresh}
          onChange={(e) => e.target.value && s.attach(e.target.value)}
        >
          <option value="">Select a received mission…</option>
          {missions.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} · {m.state}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-1.5">
        <UserRound size={14} className="text-muted" aria-hidden />
        <label htmlFor="operator" className="sr-only">
          Operator name
        </label>
        <input
          id="operator"
          className="w-36 rounded-md border border-line bg-panel2 px-2 py-1 text-xs text-ink placeholder:text-faint"
          placeholder="Operator name"
          value={s.operator}
          maxLength={64}
          onChange={(e) => s.setOperator(e.target.value)}
        />
      </div>

      <div className="flex min-w-[260px] flex-1 items-center gap-2">
        <button type="button" className="btn h-8 w-8 p-0" onClick={s.togglePlay} disabled={!s.frames.length} aria-label={s.playing ? 'Pause replay' : 'Replay from cursor'}>
          {s.playing ? <Pause size={14} aria-hidden /> : <Play size={14} aria-hidden />}
        </button>
        <input
          type="range"
          className="scrubber flex-1"
          min={0}
          max={Math.max(0, s.frames.length - 1)}
          value={s.cursor}
          onChange={(e) => s.seek(Number(e.target.value))}
          disabled={!s.frames.length}
          aria-label="Scrub through received frames"
          aria-valuetext={frame ? fmtT(frame.t) : 'no frames'}
          style={{ ['--p' as string]: `${s.frames.length > 1 ? (100 * s.cursor) / (s.frames.length - 1) : 0}%` }}
        />
        <span className="num w-[86px] text-right text-[11px] text-ink">{frame ? fmtT(frame.t) : '--'}</span>
        <div className="seg" role="group" aria-label="Replay speed">
          {[1, 4, 16].map((x) => (
            <button key={x} type="button" aria-pressed={s.speed === x} onClick={() => s.setSpeed(x)} className="num">
              {x}×
            </button>
          ))}
        </div>
        <button type="button" className={`btn px-2 py-1 text-xs ${s.live ? 'border-pass/50 text-pass' : ''}`} onClick={s.goLive} aria-pressed={s.live}>
          <Radio size={13} aria-hidden /> Live
        </button>
      </div>

      <LinkBadge frame={frame} />
      {(s.error || s.streamNote) && (
        <span className="w-full text-[11px] text-hold" role="alert">
          {s.error ?? s.streamNote}
        </span>
      )}
    </footer>
  );
}
