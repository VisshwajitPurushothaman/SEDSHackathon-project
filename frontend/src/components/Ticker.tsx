import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { InterpMark } from './ui';

export type TickerSource = 'interp' | 'frame' | 'derived';

const SOURCE_HINT: Record<TickerSource, string> = {
  interp: 'Backend field, interpolated linearly between frames by time_s',
  frame: 'Backend field: value of the latest frame at or before the playhead',
  derived: 'Derived in the frontend from backend frames',
};

interface Props {
  label: string;
  /** Backend field name, shown for traceability. */
  field?: string;
  value: ReactNode;
  unit?: string;
  source: TickerSource;
  tone?: string;
  /** Changing key replays a brief highlight (used for discrete fields). */
  flashKey?: string;
}

/** One telemetry readout. Numbers are mono with tabular figures; they tick because the playhead moves. */
export function Ticker({ label, field, value, unit, source, tone = 'text-ink', flashKey }: Props) {
  return (
    <div className="min-w-0 rounded-lg border border-line/70 bg-panel2/40 px-2.5 py-1.5" title={`${field ? `${field} · ` : ''}${SOURCE_HINT[source]}`}>
      <div className="flex items-baseline justify-between gap-1">
        <span className="truncate text-[10.5px] text-muted">{label}</span>
        {source === 'derived' && <span className="text-[9.5px] uppercase tracking-wider text-faint">derived</span>}
      </div>
      <motion.div
        key={flashKey}
        initial={flashKey ? { opacity: 0.35 } : false}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.25, ease: 'easeOut' }}
        className={`num truncate text-[14.5px] leading-snug ${tone}`}
      >
        {value}
        {unit && <span className="unit ml-1 text-[11px]">{unit}</span>}
        {source === 'interp' && <InterpMark />}
      </motion.div>
    </div>
  );
}
