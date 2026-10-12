import { Pause, Play, RotateCcw, Send } from 'lucide-react';
import { useId, useMemo } from 'react';
import { fixtureFor } from '../data/fixtures';
import { deriveMissionEvents, type MissionEventKind } from '../lib/checks';
import { endTimeOf, formatMissionClock, SPEEDS } from '../lib/playback';
import { useMission } from '../store/useMission';

// --- Logarithmic puncture slider: position 0 = sealed, 1..100 → 10^(3p/100) µm (≈1 µm … 1000 µm).
const toUm = (p: number) => (p <= 0 ? 0 : Math.round(Math.pow(10, (3 * p) / 100)));
const toPos = (um: number) => (um <= 0 ? 0 : (100 * Math.log10(Math.max(um, 1))) / 3);

const PRESETS = [
  { um: 0, label: 'Sealed (0)' },
  { um: 100, label: '100 µm' },
  { um: 300, label: '300 µm' },
  { um: 1000, label: '1000 µm' },
];

const MARKER: Record<MissionEventKind, string> = {
  leak: 'bg-fail',
  abort: 'bg-fail',
  release: 'bg-pass',
  armed: 'bg-armed',
  terminated: 'bg-muted',
};

function Transport() {
  const playing = useMission((s) => s.playing);
  const speed = useMission((s) => s.speed);
  const hasFrames = useMission((s) => s.frames.length > 0);
  const { togglePlay, restart, setSpeed } = useMission.getState();
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        className="btn btn-primary h-9 w-9 p-0"
        onClick={togglePlay}
        disabled={!hasFrames}
        aria-label={playing ? 'Pause (Space)' : 'Play (Space)'}
        title={playing ? 'Pause (Space)' : 'Play (Space)'}
      >
        {playing ? <Pause size={16} aria-hidden /> : <Play size={16} aria-hidden />}
      </button>
      <button type="button" className="btn h-9 w-9 p-0" onClick={restart} disabled={!hasFrames} aria-label="Restart (R)" title="Restart (R)">
        <RotateCcw size={15} aria-hidden />
      </button>
      <div className="seg" role="group" aria-label="Playback speed">
        {SPEEDS.map((s) => (
          <button key={s} type="button" aria-pressed={speed === s} onClick={() => setSpeed(s)} className="num">
            {s}×
          </button>
        ))}
      </div>
    </div>
  );
}

function Scrubber() {
  const frames = useMission((s) => s.frames);
  const complete = useMission((s) => s.complete);
  const playhead = useMission((s) => s.uiPlayhead);
  const seek = useMission((s) => s.seek);
  const end = endTimeOf(frames);
  const events = useMemo(() => deriveMissionEvents(frames, complete), [frames, complete]);
  const pct = (t: number) => `${end > 0 ? (t / end) * 100 : 0}%`;

  return (
    <div className="min-w-0 flex-1">
      <div className="mb-0.5 flex items-center justify-between text-[11px]">
        <span className="num text-ink">{frames.length ? formatMissionClock(playhead) : 'T+ ---:--'}</span>
        <span className="num text-muted">{frames.length ? `${formatMissionClock(end)}${complete ? '' : ' · buffering'}` : ''}</span>
      </div>
      <div className="relative">
        {/* Event markers */}
        <div className="pointer-events-none absolute inset-x-[8px] -top-1 h-2">
          {events.map((e) => (
            <span
              key={e.kind}
              className="pointer-events-auto absolute -translate-x-1/2"
              style={{ left: pct(e.t) }}
              title={`${e.label} · T+${e.t.toFixed(1)} s${e.derived ? ' (interpolated)' : ''}`}
            >
              <span className={`block h-2.5 w-[3px] rounded-sm ${MARKER[e.kind]}`} />
            </span>
          ))}
        </div>
        <input
          type="range"
          className="scrubber"
          min={0}
          max={Math.max(end, 0.1)}
          step={0.1}
          value={playhead}
          disabled={!frames.length}
          onChange={(e) => seek(Number(e.target.value))}
          onKeyDown={(e) => {
            // Same steps as the global ←/→ shortcuts instead of the native 0.1 s.
            if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
              e.preventDefault();
              seek(playhead + (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 25 : 5));
            }
          }}
          aria-label="Mission time scrubber"
          aria-valuetext={formatMissionClock(playhead)}
          style={{ ['--p' as string]: pct(playhead) }}
        />
        {/* Backend frame ticks: shows how sparse the real data is */}
        <div className="pointer-events-none absolute inset-x-[8px] bottom-0 h-1.5" aria-hidden>
          {frames.map((f) => (
            <span key={f.time_s} className="absolute bottom-0 h-1.5 w-px bg-faint" style={{ left: pct(f.time_s) }} />
          ))}
        </div>
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 text-[10.5px] text-faint">
        {events.map((e) => (
          <span key={e.kind} className="inline-flex items-center gap-1">
            <span className={`inline-block h-2 w-[3px] rounded-sm ${MARKER[e.kind]}`} aria-hidden />
            {e.label} <span className="num">T+{Math.round(e.t)} s</span>
          </span>
        ))}
        {frames.length > 0 && <span className="ml-auto">ticks = {frames.length} backend frames</span>}
      </div>
    </div>
  );
}

function Scenario() {
  const um = useMission((s) => s.scenarioUm);
  const online = useMission((s) => s.connection === 'online');
  const mode = useMission((s) => s.mode);
  const runStatus = useMission((s) => s.runStatus);
  const { setScenario, run } = useMission.getState();
  const sliderId = useId();
  const canRun = online || fixtureFor(um) !== null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <div className="flex min-w-[200px] flex-col">
        <label htmlFor={sliderId} className="flex items-baseline justify-between text-[11px] text-muted">
          <span>Puncture diameter</span>
          <span className="num text-[13px] text-ink">
            {um} <span className="unit text-[11px]">µm</span>
          </span>
        </label>
        <input
          id={sliderId}
          type="range"
          className="scrubber"
          min={0}
          max={100}
          step={0.5}
          value={toPos(um)}
          disabled={!online}
          onChange={(e) => setScenario(toUm(Number(e.target.value)))}
          aria-valuetext={`${um} micrometres`}
          style={{ ['--p' as string]: `${toPos(um)}%` }}
          title={online ? 'Logarithmic scale, 0–1000 µm' : 'Backend offline: demo fixtures only (Sealed, 100 µm)'}
        />
      </div>
      <div className="seg" role="group" aria-label="Scenario presets">
        {PRESETS.map((p) => {
          const disabled = !online && fixtureFor(p.um) === null;
          return (
            <button
              key={p.um}
              type="button"
              aria-pressed={um === p.um}
              disabled={disabled}
              title={disabled ? 'Needs the backend (no demo fixture)' : undefined}
              onClick={() => setScenario(p.um)}
              className="num"
            >
              {p.label}
            </button>
          );
        })}
      </div>
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => void run()}
        disabled={!canRun || runStatus === 'loading'}
        title={canRun ? undefined : 'Offline: demo data exists only for Sealed and 100 µm'}
      >
        <Send size={14} aria-hidden /> Run <span className="text-[11px] text-muted">{online ? (mode === 'live' ? 'live' : 'replay') : 'demo'}</span>
      </button>
    </div>
  );
}

/** Bottom dock: transport, scrubber with event markers, scenario control. */
export function TimelineDock() {
  return (
    <footer className="border-t border-line bg-panel/85 px-4 py-2.5 backdrop-blur-panel" aria-label="Mission timeline">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <Transport />
        <Scrubber />
        <div className="hidden h-10 w-px bg-line xl:block" aria-hidden />
        <Scenario />
      </div>
    </footer>
  );
}
