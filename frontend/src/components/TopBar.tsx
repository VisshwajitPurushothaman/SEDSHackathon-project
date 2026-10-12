import { FlaskConical, Radio, Wifi, WifiOff } from 'lucide-react';
import { API_URL } from '../api/client';
import { formatMissionClock } from '../lib/playback';
import { useMission, type Mode } from '../store/useMission';

function MissionClock() {
  const playhead = useMission((s) => s.uiPlayhead);
  const hasData = useMission((s) => s.frames.length > 0);
  return (
    <div className="flex items-baseline gap-2" aria-live="off">
      <span className="text-[11px] uppercase tracking-[0.16em] text-muted">Mission</span>
      <span className="num text-xl font-medium text-ink" title="Mission elapsed time (playhead)">
        {hasData ? formatMissionClock(playhead) : 'T+ ---:--'}
      </span>
    </div>
  );
}

function ConnectionBadge() {
  const connection = useMission((s) => s.connection);
  if (connection === 'checking') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-xs text-muted">
        <Wifi size={13} aria-hidden /> Connecting…
      </span>
    );
  }
  if (connection === 'online') {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border border-pass/40 bg-pass/10 px-2.5 py-1 text-xs text-pass"
        title={`GET ${API_URL}/api/health · polled every 5 s`}
      >
        <span className="h-1.5 w-1.5 animate-status-pulse rounded-full bg-pass" aria-hidden />
        <Wifi size={13} aria-hidden /> Live
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-hold/50 bg-hold/10 px-2.5 py-1 text-xs text-hold"
      title={`No answer from ${API_URL}/api/health. Playing recorded backend output.`}
    >
      <WifiOff size={13} aria-hidden /> Backend offline · demo data
    </span>
  );
}

function ModeSwitch() {
  const mode = useMission((s) => s.mode);
  const online = useMission((s) => s.connection === 'online');
  const setMode = useMission((s) => s.setMode);
  const opt = (m: Mode, label: string, hint: string, disabled = false) => (
    <button
      type="button"
      role="radio"
      aria-checked={mode === m}
      disabled={disabled}
      title={disabled ? 'Live stream needs the backend' : hint}
      onClick={() => setMode(m)}
    >
      {label}
    </button>
  );
  return (
    <div className="seg" role="radiogroup" aria-label="Data mode">
      {opt('replay', 'Replay', 'POST /api/simulate, then play back')}
      {opt('live', 'Live stream', 'GET /api/stream (SSE): frames appended as they arrive', !online)}
    </div>
  );
}

export type View = 'preflight' | 'flight' | 'postflight';

const TABS: { id: View; label: string; href: string }[] = [
  { id: 'preflight', label: 'Pre-flight', href: '#/' },
  { id: 'flight', label: 'Flight', href: '#/flight' },
  { id: 'postflight', label: 'Post-flight', href: '#/postflight' },
];

function Tabs({ view }: { view: View }) {
  return (
    <nav aria-label="Mission windows">
      <ul className="seg">
        {TABS.map((t) => (
          <li key={t.id}>
            <a
              href={t.href}
              aria-current={view === t.id ? 'page' : undefined}
              className={`block rounded-md px-3 py-1 text-xs font-medium transition-colors duration-150 ${view === t.id ? 'bg-line text-ink' : 'text-muted hover:text-ink'}`}
            >
              {t.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function TopBar({ view = 'preflight' }: { view?: View }) {
  return (
    <header className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-line bg-panel/70 px-4 py-2.5 backdrop-blur-panel">
      <div className="flex items-center gap-2.5">
        <div className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-panel2">
          <Radio size={16} className="text-info" aria-hidden />
        </div>
        <h1 className="text-[17px] font-semibold tracking-tight text-ink">
          AERO-FIRE <span className="font-medium text-muted">Flight Director</span>
        </h1>
      </div>
      <Tabs view={view} />
      {view === 'preflight' && <MissionClock />}
      <div className="ml-auto flex flex-wrap items-center gap-3">
        {view === 'preflight' && <ModeSwitch />}
        <ConnectionBadge />
        <span
          className="inline-flex items-center gap-1.5 rounded-full border border-info/40 bg-info/10 px-2.5 py-1 text-xs font-semibold tracking-wider text-info"
          title="All telemetry is simulated by the AERO-FIRE backend. No real vehicle is commanded."
        >
          <FlaskConical size={13} aria-hidden /> SIMULATION
        </span>
      </div>
    </header>
  );
}
