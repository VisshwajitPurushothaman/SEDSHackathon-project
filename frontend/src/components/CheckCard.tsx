import { motion } from 'framer-motion';
import { memo } from 'react';
import type { TelemetryFrame } from '../api/types';
import { CHECK_DEFS, deriveChecks, START_TANK_PRES_BAR, swingOf, TILT_LIMIT_DEG, type CheckId } from '../lib/checks';
import { endTimeOf, type PlaybackFrame } from '../lib/playback';
import { useCurrentFrame, useMission } from '../store/useMission';
import { CHECK_ICONS, InterpMark, STATE_BORDER, STATE_TEXT, StatePill } from './ui';

/** Current value for a check, formatted, plus whether it is interpolated. */
function checkValue(id: CheckId, f: PlaybackFrame | null): { value: string; unit: string; interp: boolean } {
  if (!f) return { value: '--', unit: '', interp: false };
  const interp = !f.exact;
  switch (id) {
    case 'tank':
      return { value: f.leak_status, unit: '', interp: false };
    case 'pressure':
      return { value: f.tank_pres_bar.toFixed(2), unit: 'bar', interp };
    case 'mass':
      return { value: f.mass_loss_g.toFixed(2), unit: 'g lost', interp };
    case 'altitude':
      return { value: (f.altitude_m / 1000).toFixed(2), unit: 'km', interp };
    case 'tilt':
      return { value: swingOf(f).toFixed(2), unit: '° amplitude', interp };
    case 'motor':
      return { value: f.motor_temp_c.toFixed(1), unit: '°C', interp };
    case 'atmosphere':
      return { value: `${f.ext_temp_c.toFixed(1)} °C · ${f.ext_pres_hpa.toFixed(1)}`, unit: 'hPa', interp };
  }
}

/** Series plotted in each card's sparkline. */
const SERIES: Record<CheckId, (f: TelemetryFrame) => number> = {
  tank: (f) => f.tank_pres_bar,
  pressure: (f) => f.tank_pres_bar,
  mass: (f) => f.mass_loss_g,
  altitude: (f) => f.altitude_m,
  tilt: (f) => swingOf(f),
  motor: (f) => f.motor_temp_c,
  atmosphere: (f) => f.ext_temp_c,
};

const SW = 112;
const SH = 30;

/** Tiny hand-drawn SVG sparkline (cheap enough to redraw every frame). */
function Sparkline({ id, frames, current, endTime }: { id: CheckId; frames: TelemetryFrame[]; current: PlaybackFrame | null; endTime: number }) {
  if (!frames.length || !current) return <svg width={SW} height={SH} aria-hidden />;
  const get = SERIES[id];
  let lo = Infinity;
  let hi = -Infinity;
  for (const f of frames) {
    const v = get(f);
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (id === 'tilt') {
    lo = 0;
    hi = Math.max(hi, TILT_LIMIT_DEG * 1.2);
  }
  if (id === 'pressure' || id === 'tank') hi = Math.max(hi, START_TANK_PRES_BAR);
  if (hi - lo < 1e-6) {
    hi += 1;
    lo -= 1;
  }
  const T = Math.max(endTime, 1);
  const x = (t: number) => (t / T) * (SW - 4) + 2;
  const y = (v: number) => SH - 3 - ((v - lo) / (hi - lo)) * (SH - 6);

  const pts: string[] = [];
  for (const f of frames) {
    if (f.time_s > current.time_s) break;
    pts.push(`${x(f.time_s).toFixed(1)},${y(get(f)).toFixed(1)}`);
  }
  pts.push(`${x(current.time_s).toFixed(1)},${y(get(current)).toFixed(1)}`);

  return (
    <svg width={SW} height={SH} className="overflow-visible" aria-hidden>
      {id === 'tilt' && (
        <rect x={0} y={y(TILT_LIMIT_DEG)} width={SW} height={Math.max(1, y(0) - y(TILT_LIMIT_DEG))} className="fill-pass/10" />
      )}
      <line x1={0} x2={SW} y1={SH - 1} y2={SH - 1} className="stroke-line" strokeWidth={1} />
      <polyline points={pts.join(' ')} fill="none" className="stroke-info" strokeWidth={1.4} strokeLinejoin="round" />
      <circle cx={x(current.time_s)} cy={y(get(current))} r={2.2} className="fill-ink" />
    </svg>
  );
}

const SHAKE = { x: [0, -7, 7, -5, 5, -2, 0] };
const STILL = { x: 0 };

/**
 * One check. Subscribes to the playhead frame itself so the layout wrapper in
 * ChecklistPanel only re-renders when the card order changes.
 */
export const CheckCard = memo(function CheckCard({ id }: { id: CheckId }) {
  const frame = useCurrentFrame();
  const frames = useMission((s) => s.frames);
  const hovered = useMission((s) => s.hovered === id);
  const setHovered = useMission((s) => s.setHovered);
  const result = deriveChecks(frame)[id];
  const def = CHECK_DEFS[id];
  const Icon = CHECK_ICONS[id];
  const v = checkValue(id, frame);
  const failed = result.state === 'FAIL';

  return (
    <motion.div
      data-check-card={id}
      tabIndex={0}
      role="group"
      aria-label={`${def.name}: ${result.state}, ${v.value} ${v.unit}`}
      onMouseEnter={() => setHovered(id, 'card')}
      onMouseLeave={() => setHovered(null)}
      onFocus={() => setHovered(id, 'card')}
      onBlur={() => setHovered(null)}
      animate={failed ? SHAKE : STILL}
      transition={{ duration: 0.45, ease: 'easeOut' }}
      className={`group relative rounded-[10px] border bg-panel2/60 px-3 py-2.5 transition-colors duration-200 ease-out ${STATE_BORDER[result.state]} ${
        hovered ? 'bg-panel2 ring-1 ring-info/70' : 'hover:bg-panel2'
      } ${failed ? 'bg-fail/[0.06]' : ''}`}
    >
      <div className="flex items-center gap-2">
        <Icon size={15} className={STATE_TEXT[result.state]} aria-hidden />
        <span className="num text-[10.5px] text-faint">{String(def.num).padStart(2, '0')}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{def.name}</span>
        <StatePill state={result.state} />
      </div>
      <div className="mt-1.5 flex items-end justify-between gap-2">
        <div className="min-w-0">
          <div className={`num truncate text-[17px] leading-tight ${id === 'tank' ? STATE_TEXT[result.state] : 'text-ink'}`}>
            {v.value}
            {v.unit && <span className="unit ml-1 text-[11px]">{v.unit}</span>}
            {v.interp && <InterpMark />}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-muted">{result.note}</div>
        </div>
        <Sparkline id={id} frames={frames} current={frame} endTime={endTimeOf(frames)} />
      </div>
      <div className="mt-1 truncate text-[10.5px] text-faint" title={def.rule}>
        Rule: <span className="num">{def.rule}</span>
      </div>
    </motion.div>
  );
});
