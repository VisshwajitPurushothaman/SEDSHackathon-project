import { AnimatePresence, motion } from 'framer-motion';
import { ChevronDown, ScrollText } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useMission, type LogLevel } from '../store/useMission';

const LEVEL: Record<LogLevel, { cls: string; tag: string }> = {
  info: { cls: 'text-muted', tag: 'INFO' },
  ok: { cls: 'text-pass', tag: 'OK' },
  warn: { cls: 'text-hold', tag: 'WARN' },
  fail: { cls: 'text-fail', tag: 'FAIL' },
};

const wallTime = (ms: number) => new Date(ms).toLocaleTimeString([], { hour12: false });

/** Append-only, time-stamped log. Entries are never edited or removed by scrubbing. */
export function EventLog() {
  const events = useMission((s) => s.events);
  const [open, setOpen] = useState(true);
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    if (open && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [events.length, open]);

  return (
    <section className="rounded-lg border border-line/70 bg-panel2/40" aria-labelledby="eventlog-title">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="eventlog-list"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left"
      >
        <ScrollText size={14} className="text-muted" aria-hidden />
        <h3 id="eventlog-title" className="panel-title flex-1 text-[12px]">
          Event log
        </h3>
        <span className="num text-[11px] text-faint">{events.length}</span>
        <ChevronDown size={14} className={`text-muted transition-transform duration-200 ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            className="overflow-hidden"
          >
            <ol
              id="eventlog-list"
              ref={list}
              className="max-h-48 overflow-y-auto border-t border-line/70 px-3 py-2 font-mono text-[11px] leading-relaxed"
              aria-live="polite"
              aria-relevant="additions"
            >
              {events.length === 0 && <li className="text-faint">No events yet.</li>}
              {events.map((e) => (
                <li key={e.id} className="flex gap-2 tabular-nums">
                  <span className="shrink-0 text-faint" title="Wall-clock time">
                    {wallTime(e.wall)}
                  </span>
                  <span className="w-[60px] shrink-0 text-muted" title="Mission time">
                    {e.t === null ? '' : `T+${Math.round(e.t)} s`}
                  </span>
                  <span className={`shrink-0 ${LEVEL[e.level].cls}`}>{LEVEL[e.level].tag}</span>
                  <span className="min-w-0 text-ink/90">{e.text}</span>
                </li>
              ))}
            </ol>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
