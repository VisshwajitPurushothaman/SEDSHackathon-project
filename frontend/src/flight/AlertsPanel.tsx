import { Bell, Check, ScrollText } from 'lucide-react';
import { useState } from 'react';
import type { Alert, GroundFrame } from './api';
import { useFlight } from './store';
import { fmtT } from './ui';

const SEV: Record<Alert['severity'], string> = {
  INFO: 'border-info/40 bg-info/10 text-info',
  CAUTION: 'border-hold/50 bg-hold/10 text-hold',
  WARNING: 'border-fail/60 bg-fail/10 text-fail',
};

function Evidence({ ev }: { ev: Record<string, unknown> }) {
  const entries = Object.entries(ev).filter(([, v]) => v !== null && v !== undefined);
  if (!entries.length) return null;
  return (
    <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-[10.5px]">
      {entries.slice(0, 8).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-faint">{k}</dt>
          <dd className="num truncate text-ink/80" title={JSON.stringify(v)}>
            {typeof v === 'number' ? (Math.abs(v) < 1e-3 || Math.abs(v) > 1e5 ? v.toExponential(2) : v.toFixed(2)) : typeof v === 'object' ? JSON.stringify(v).slice(0, 80) : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Alerts with evidence; every one needs an operator acknowledgement (name and time are logged). */
export function AlertsPanel({ frame }: { frame: GroundFrame | null }) {
  const ack = useFlight((s) => s.ack);
  const operator = useFlight((s) => s.operator);
  const [open, setOpen] = useState<number | null>(null);
  const alerts = frame ? [...frame.alerts].reverse() : [];
  const unacked = alerts.filter((a) => !a.acknowledged_by).length;
  return (
    <section className="panel flex min-h-0 flex-1 flex-col" aria-labelledby="alerts-title">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <Bell size={15} className="text-muted" aria-hidden />
        <h2 id="alerts-title" className="panel-title flex-1">
          Alerts
        </h2>
        <span className={`num text-[11px] ${unacked ? 'text-hold' : 'text-faint'}`}>{unacked} unacknowledged</span>
      </div>
      <ol className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-3" aria-live="polite">
        {alerts.length === 0 && <li className="text-[12px] text-faint">No alerts.</li>}
        {alerts.map((a) => (
          <li key={a.id} className={`rounded-lg border px-2.5 py-1.5 ${a.acknowledged_by ? 'border-line/60 opacity-75' : 'border-line'}`}>
            <div className="flex items-center gap-2">
              <span className={`rounded border px-1 text-[9.5px] font-semibold ${SEV[a.severity]}`}>{a.severity}</span>
              <button type="button" className="min-w-0 flex-1 truncate text-left text-[12px] text-ink" onClick={() => setOpen(open === a.id ? null : a.id)} aria-expanded={open === a.id}>
                {a.title}
              </button>
              <span className="num shrink-0 text-[10.5px] text-muted">{fmtT(a.t)}</span>
            </div>
            {open === a.id && <Evidence ev={a.evidence} />}
            <div className="mt-1 flex items-center justify-end gap-2 text-[10.5px]">
              {a.acknowledged_by ? (
                <span className="inline-flex items-center gap-1 text-muted">
                  <Check size={11} aria-hidden /> {a.acknowledged_by} · {a.acknowledged_at ? new Date(a.acknowledged_at * 1000).toLocaleTimeString([], { hour12: false }) : ''}
                </span>
              ) : (
                <button type="button" className="btn px-2 py-0.5 text-[11px]" onClick={() => void ack(a.id)} disabled={!operator.trim()} title={operator.trim() ? `Acknowledge as ${operator}` : 'Enter the operator name first'}>
                  Acknowledge
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Append-only mission event log (operator actions, vehicle events, system). */
export function EventLogPanel() {
  const events = useFlight((s) => s.events);
  return (
    <section className="rounded-lg border border-line/70 bg-panel2/40" aria-labelledby="flight-log-title">
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        <ScrollText size={13} className="text-muted" aria-hidden />
        <h3 id="flight-log-title" className="text-[11px] font-semibold uppercase tracking-wider text-muted">
          Event log (append-only)
        </h3>
      </div>
      <ol className="max-h-40 overflow-y-auto border-t border-line/70 px-2.5 py-1 font-mono text-[10.5px] leading-relaxed">
        {events.length === 0 && <li className="text-faint">No events.</li>}
        {[...events].reverse().map((e) => (
          <li key={e.n} className="flex gap-2">
            <span className="shrink-0 text-faint">{fmtT(e.t)}</span>
            <span className={`shrink-0 ${e.actor === 'vehicle' ? 'text-info' : e.actor === 'system' ? 'text-muted' : 'text-armed'}`}>{e.actor}</span>
            <span className="min-w-0 text-ink/90">{e.action}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
