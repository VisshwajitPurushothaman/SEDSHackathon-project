import { useEffect, useState, type ReactNode } from 'react';
import type { Alarm } from './api';
import { fmtAge, fmtMET, fmtUTC } from './format';
import { useGround } from './store';

export type PageId = 'overview' | 'preflight' | 'flight' | 'trajectory' | 'postflight' | 'events';

export const PAGES: { id: PageId; label: string; key: string }[] = [
  { id: 'overview', label: 'OVERVIEW', key: '1' },
  { id: 'preflight', label: 'PRE-FLIGHT', key: '2' },
  { id: 'flight', label: 'FLIGHT', key: '3' },
  { id: 'trajectory', label: 'TRAJECTORY', key: '4' },
  { id: 'postflight', label: 'POST-FLIGHT', key: '5' },
  { id: 'events', label: 'EVENTS', key: '6' },
];

const pageFromHash = (): PageId => {
  const h = location.hash.replace(/^#\/?/, '');
  return (PAGES.find((p) => p.id === h)?.id ?? 'overview') as PageId;
};

export function usePage(): PageId {
  const [page, setPage] = useState<PageId>(pageFromHash);
  useEffect(() => {
    const on = () => setPage(pageFromHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return page;
}

function useUtcClock(): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 250);
    return () => clearInterval(id);
  }, []);
  return now;
}

const SEV_TAG: Record<Alarm['severity'], string> = { INFO: 'tag-fused', CAUTION: 'tag-cau', WARNING: 'tag-alm' };
const SEV_TEXT: Record<Alarm['severity'], string> = { INFO: 'INFO', CAUTION: 'CAUTION', WARNING: 'ALARM' };

export function SevTag({ sev }: { sev: Alarm['severity'] }) {
  return <span className={`tag ${SEV_TAG[sev]}`}>{SEV_TEXT[sev]}</span>;
}

function Cell({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`flex flex-col justify-center border-r border-line px-3 ${wide ? 'min-w-[150px]' : ''}`}>
      <span className="lbl leading-3">{label}</span>
      <span className="num text-[14px] leading-5 text-ink">{children}</span>
    </div>
  );
}

function TopBar() {
  const snap = useGround((s) => s.snap);
  const ws = useGround((s) => s.ws);
  const utcNow = useUtcClock();
  const link = snap?.link;
  const aos = !!link?.aos;
  const hasData = !!snap?.mission_id;
  const source = ws !== 'open' ? 'NO BACKEND' : snap?.source ?? '---';
  const active = snap?.active_alarms ?? 0;
  const g = snap?.ground;
  return (
    <header className="flex h-[52px] shrink-0 items-stretch border-b border-line bg-panel" aria-label="Mission status">
      <div className="flex items-center gap-2 border-r border-line px-3">
        <span className="font-sans text-[13px] font-semibold tracking-[0.18em] text-ink">AERO-FIRE</span>
        <span className="lbl">ground station</span>
      </div>
      <Cell label="Mission">{snap?.mission_id ?? '---'}</Cell>
      <Cell label="MET" wide>
        <span className="flex items-center gap-1.5">
          {fmtMET(snap?.met)}
          {hasData && !aos && <span className="tag tag-pred">PRED</span>}
        </span>
      </Cell>
      <Cell label="UTC">{fmtUTC(utcNow)}</Cell>
      <Cell label="Phase" wide>
        <span className="flex items-center gap-1.5">
          {snap?.fc_phase ?? '---'}
          {g && <span className="text-[11px] text-muted">· {g.phase}</span>}
        </span>
      </Cell>
      <div className="flex items-center border-r border-line px-3" role="status" aria-label={`Signal ${aos ? 'acquired' : 'lost'}`}>
        {hasData ? (
          <span className={`tag ${aos ? 'tag-nom' : 'tag-alm'} text-[12px]`}>{aos ? 'AOS' : 'LOS'}</span>
        ) : (
          <span className="tag tag-stale text-[12px]">NO SIGNAL</span>
        )}
        <span className="num ml-2 text-[12px] text-muted">{fmtAge(link?.last_frame_age_s)}</span>
      </div>
      <Cell label="Rate">{link?.rate_hz !== undefined ? `${link.rate_hz.toFixed(1)} Hz` : '---'}</Cell>
      <Cell label="Loss">
        <span className={link?.loss_pct !== undefined && link.loss_pct > 5 ? 'text-hold' : ''}>{link?.loss_pct !== undefined ? `${link.loss_pct.toFixed(1)} %` : '---'}</span>
      </Cell>
      <Cell label="Latency">{link?.latency_s != null ? `${(link.latency_s * 1000).toFixed(0)} ms` : '---'}</Cell>
      <div className="flex items-center gap-2 border-r border-line px-3">
        <span className={`tag ${source === 'PLAYBACK' ? 'tag-pred' : source === 'LIVE UDP' ? 'tag-nom' : 'tag-stale'} text-[12px]`} title={snap?.receiver.playback_file ?? snap?.receiver.listen ?? ''}>
          {source}
          {source === 'PLAYBACK' && snap?.receiver.playback_done ? ' · END' : ''}
        </span>
        {snap?.digital_twin && (
          <span
            className={`tag ${snap.digital_twin.server_status === 'CONNECTED' ? 'tag-nom' : snap.digital_twin.server_status === 'LISTENING' ? 'tag-pred' : 'tag-stale'} text-[11px]`}
            title={`TCP ${snap.digital_twin.port} ${snap.digital_twin.server_status} · Packets: ${snap.digital_twin.packets_received}`}
          >
            TCP {snap.digital_twin.port}: {snap.digital_twin.server_status === 'CONNECTED' ? 'STREAMING' : snap.digital_twin.server_status}
          </span>
        )}
        {snap?.recording && (
          <span className="flex items-center gap-1 text-[11px] font-semibold text-fail" title={snap.session_file ?? ''}>
            <span className="h-2 w-2 rounded-full bg-fail" aria-hidden /> REC
          </span>
        )}
      </div>
      <div className="ml-auto flex items-center gap-2 px-3" aria-label={`${active} active alarms`}>
        <span className="lbl">Alarms</span>
        <span className={`tag ${active ? 'tag-alm' : 'tag-nom'} text-[13px]`}>{active}</span>
      </div>
    </header>
  );
}

/** Empty / error states for the whole console: never show frozen values as if live. */
function StateBanner() {
  const ws = useGround((s) => s.ws);
  const snap = useGround((s) => s.snap);
  const flt = (snap?.digital_twin as unknown as { filter?: { source: string | null; rejected: Record<string, number>; accepted: number } } | undefined)?.filter;
  let text: string | null = null;
  let cls = 'border-hold/60 bg-hold/10 text-hold';
  if (ws !== 'open') {
    text = ws === 'connecting' ? 'Connecting to the ground-station backend…' : 'Backend unreachable: the console is not receiving data (reconnecting). Values shown are not live.';
    cls = ws === 'connecting' ? 'border-line bg-panel2 text-muted' : 'border-fail/60 bg-fail/10 text-fail';
  } else if (!snap?.mission_id) {
    const rej = flt ? Object.values(flt.rejected).reduce((a, b) => a + b, 0) : 0;
    text = rej
      ? `No telemetry stream locked: ${rej} packets received from ${flt?.source ?? 'the sender'} but none advancing in MET (see OVERVIEW > Laptop A twin stream).`
      : 'No telemetry received yet: waiting for Laptop A (TCP 65431) or an ICD sender (UDP 5600).';
    cls = 'border-line bg-panel2 text-muted';
  } else if (snap.link && snap.link.aos === false) {
    text = 'LOS: no fresh frames. Values are STALE (with age) and positions PREDICTED.';
  }
  if (!text) return null;
  return (
    <div className={`border-b px-3 py-1.5 text-[12.5px] ${cls}`} role="status">
      {text}
    </div>
  );
}

function LeftNav({ page }: { page: PageId }) {
  return (
    <nav className="flex w-[148px] shrink-0 flex-col border-r border-line bg-panel py-2" aria-label="Pages">
      {PAGES.map((p) => (
        <a
          key={p.id}
          href={`#/${p.id}`}
          aria-current={page === p.id ? 'page' : undefined}
          className={`flex items-center justify-between border-l-2 px-3 py-2 text-[12px] font-semibold tracking-[0.1em] ${
            page === p.id ? 'border-info bg-line/60 text-ink' : 'border-transparent text-muted hover:text-ink'
          }`}
        >
          {p.label}
          <span className="num text-[10px] text-muted">{p.key}</span>
        </a>
      ))}
    </nav>
  );
}

export function topAlarm(alarms: Alarm[] | undefined): Alarm | null {
  if (!alarms) return null;
  for (let i = alarms.length - 1; i >= 0; i--) if (!alarms[i].acknowledged_by) return alarms[i];
  return null;
}

function AlarmLine() {
  const snap = useGround((s) => s.snap);
  const operator = useGround((s) => s.operator);
  const setOperator = useGround((s) => s.setOperator);
  const ack = useGround((s) => s.ack);
  const error = useGround((s) => s.error);
  const clearError = useGround((s) => s.clearError);
  const top = topAlarm(snap?.alarms);
  return (
    <footer className="flex h-[38px] shrink-0 items-center gap-3 border-t border-line bg-panel px-3" aria-label="Newest alarm">
      {top ? (
        <button
          type="button"
          onClick={() => void ack(top.id)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left hover:bg-line/40"
          title="Acknowledge (A)"
          aria-label={`Acknowledge alarm: ${top.title}`}
        >
          <SevTag sev={top.severity} />
          <span className="num text-[12px] text-muted">{fmtMET(top.met)}</span>
          <span className="truncate text-[13px] text-ink">{top.title}</span>
          <span className="ml-auto shrink-0 text-[11px] text-muted">click or A to acknowledge</span>
        </button>
      ) : (
        <span className="flex-1 text-[12px] text-muted">No unacknowledged alarms.</span>
      )}
      {error && (
        <button type="button" className="text-[12px] text-hold" onClick={clearError} role="alert">
          {error}
        </button>
      )}
      <label className="flex items-center gap-1.5">
        <span className="lbl">Operator</span>
        <input id="operator" className="field w-40" placeholder="name (required)" value={operator} maxLength={64} onChange={(e) => setOperator(e.target.value)} />
      </label>
    </footer>
  );
}

function useKeys() {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const p = PAGES.find((x) => x.key === e.key);
      if (p) {
        location.hash = `#/${p.id}`;
        e.preventDefault();
      } else if (e.key === 'a' || e.key === 'A') {
        const { snap, ack } = useGround.getState();
        const top = topAlarm(snap?.alarms);
        if (top) void ack(top.id);
        else return;
        e.preventDefault();
      }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);
}

export function Shell({ page, children }: { page: PageId; children: ReactNode }) {
  useKeys();
  const connect = useGround((s) => s.connect);
  useEffect(() => connect(), [connect]);
  return (
    <div className="flex h-full min-h-[640px] flex-col">
      <TopBar />
      <StateBanner />
      <div className="flex min-h-0 flex-1">
        <LeftNav page={page} />
        <main className="min-w-0 flex-1 overflow-auto" id="page">
          {children}
        </main>
      </div>
      <AlarmLine />
    </div>
  );
}
