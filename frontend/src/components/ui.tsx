import { motion } from 'framer-motion';
import {
  Activity,
  CircleCheck,
  CircleDashed,
  CircleX,
  CloudSun,
  Flame,
  Gauge,
  MoveUp,
  Rotate3d,
  Scale,
  TriangleAlert,
  Cylinder,
  type LucideIcon,
} from 'lucide-react';
import type { CheckId, CheckState } from '../lib/checks';

export const CHECK_ICONS: Record<CheckId, LucideIcon> = {
  tank: Cylinder,
  pressure: Gauge,
  mass: Scale,
  altitude: MoveUp,
  tilt: Rotate3d,
  motor: Flame,
  atmosphere: CloudSun,
};

const STATE_STYLE: Record<CheckState, { icon: LucideIcon; cls: string; label: string }> = {
  PENDING: { icon: CircleDashed, cls: 'border-line bg-panel2 text-muted', label: 'PENDING' },
  MONITORING: { icon: Activity, cls: 'border-info/40 bg-info/10 text-info', label: 'MONITORING' },
  PASS: { icon: CircleCheck, cls: 'border-pass/40 bg-pass/10 text-pass', label: 'PASS' },
  HOLD: { icon: TriangleAlert, cls: 'border-hold/50 bg-hold/10 text-hold', label: 'HOLD' },
  FAIL: { icon: CircleX, cls: 'border-fail/60 bg-fail/15 text-fail', label: 'FAIL' },
};

/** Text colour per state (for values and borders). */
export const STATE_TEXT: Record<CheckState, string> = {
  PENDING: 'text-muted',
  MONITORING: 'text-ink',
  PASS: 'text-pass',
  HOLD: 'text-hold',
  FAIL: 'text-fail',
};

export const STATE_BORDER: Record<CheckState, string> = {
  PENDING: 'border-line',
  MONITORING: 'border-line',
  PASS: 'border-pass/30',
  HOLD: 'border-hold/45',
  FAIL: 'border-fail/70',
};

/** State pill: icon + text, never colour alone. Crossfades on change. */
export function StatePill({ state, label }: { state: CheckState; label?: string }) {
  const s = STATE_STYLE[state];
  const Icon = s.icon;
  return (
    <motion.span
      key={state}
      initial={{ opacity: 0.4 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold tracking-wide ${s.cls}`}
    >
      <Icon size={12} strokeWidth={2.4} aria-hidden />
      {label ?? s.label}
    </motion.span>
  );
}

/** Marks a value that is interpolated between backend frames. */
export function InterpMark() {
  return (
    <span className="ml-0.5 cursor-help text-faint" title="Interpolated linearly between backend frames by time_s" aria-label="interpolated">
      ≈
    </span>
  );
}
