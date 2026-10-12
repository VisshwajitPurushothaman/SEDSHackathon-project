import { useMemo } from 'react';
import {
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';
import type { TelemetryFrame } from '../api/types';
import { useThrottledPlayhead } from '../hooks/useMissionRuntime';
import { MASS_CAUTION_G, RELEASE_ALTITUDE_M, START_TANK_PRES_BAR } from '../lib/checks';
import { endTimeOf, interpolateFrame } from '../lib/playback';
import { useMission } from '../store/useMission';
import { ui } from '../theme/tokens';

export const axis = { stroke: ui.border, tick: { fill: ui.muted, fontSize: 10, fontFamily: 'JetBrains Mono' }, tickLine: false };
export const grid = <CartesianGrid stroke={ui.border} strokeDasharray="2 4" vertical={false} />;

/** Backend frames up to the playhead, plus the interpolated point at the playhead. */
export function useRevealed(): { data: TelemetryFrame[]; end: number; current: TelemetryFrame | null } {
  const frames = useMission((s) => s.frames);
  const playhead = useThrottledPlayhead(110);
  return useMemo(() => {
    const current = interpolateFrame(frames, playhead);
    const data = frames.filter((f) => f.time_s <= playhead);
    if (current && (!data.length || data[data.length - 1].time_s < current.time_s)) data.push(current);
    return { data, end: Math.max(endTimeOf(frames), 1), current };
  }, [frames, playhead]);
}

export function ChartCard({ title, legend, children }: { title: string; legend?: React.ReactNode; children: React.ReactNode }) {
  return (
    <figure className="rounded-lg border border-line/70 bg-panel2/40 px-2 pb-1 pt-2">
      <figcaption className="flex items-center justify-between px-1 text-[11px] text-muted">
        <span className="font-medium text-ink/90">{title}</span>
        {legend}
      </figcaption>
      <div className="h-[112px]">{children}</div>
    </figure>
  );
}

const Swatch = ({ cls, label }: { cls: string; label: string }) => (
  <span className="inline-flex items-center gap-1">
    <span className={`inline-block h-0.5 w-3 rounded ${cls}`} aria-hidden />
    {label}
  </span>
);

export function AltitudeChart() {
  const { data, end } = useRevealed();
  const rows = data.map((f) => ({ t: f.time_s, km: f.altitude_m / 1000 }));
  return (
    <ChartCard title="Altitude vs time" legend={<span className="num">km · s</span>}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
          {grid}
          <XAxis type="number" dataKey="t" domain={[0, end]} {...axis} tickCount={5} allowDecimals={false} />
          <YAxis type="number" domain={[0, 30]} ticks={[0, 10, 20, 30]} {...axis} />
          <ReferenceLine y={RELEASE_ALTITUDE_M / 1000} stroke={ui.pass} strokeDasharray="4 3" strokeOpacity={0.7} />
          <Line dataKey="km" stroke={ui.info} strokeWidth={1.6} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function TankChart() {
  const { data, end } = useRevealed();
  const frames = useMission((s) => s.frames);
  const rows = data.map((f) => ({ t: f.time_s, bar: f.tank_pres_bar, g: f.mass_loss_g }));
  const minBar = frames.length ? Math.floor(Math.min(...frames.map((f) => f.tank_pres_bar)) - 1) : 180;
  const maxG = Math.max(12, ...frames.map((f) => Math.ceil(f.mass_loss_g + 1)));
  return (
    <ChartCard
      title="Tank pressure & mass loss"
      legend={
        <span className="flex gap-2">
          <Swatch cls="bg-info" label="bar" />
          <Swatch cls="bg-hold" label="g lost" />
        </span>
      }
    >
      <ResponsiveContainer>
        <ComposedChart data={rows} margin={{ top: 6, right: -12, bottom: 0, left: -18 }}>
          {grid}
          <XAxis type="number" dataKey="t" domain={[0, end]} {...axis} tickCount={5} allowDecimals={false} />
          <YAxis yAxisId="bar" type="number" domain={[minBar, START_TANK_PRES_BAR]} {...axis} allowDecimals={false} />
          <YAxis yAxisId="g" orientation="right" type="number" domain={[0, maxG]} {...axis} allowDecimals={false} />
          <ReferenceLine yAxisId="g" y={MASS_CAUTION_G} stroke={ui.hold} strokeDasharray="4 3" strokeOpacity={0.6} />
          <Line yAxisId="bar" dataKey="bar" stroke={ui.info} strokeWidth={1.6} dot={false} isAnimationActive={false} />
          <Line yAxisId="g" dataKey="g" stroke={ui.hold} strokeWidth={1.6} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
