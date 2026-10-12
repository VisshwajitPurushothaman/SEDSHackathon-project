import { CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { useMission } from '../store/useMission';
import { ui } from '../theme/tokens';
import { axis, ChartCard, useRevealed } from './Charts';

/** Atmospheric sounding: outside temperature against altitude, current point highlighted. */
export function Sounding() {
  const { data, current } = useRevealed();
  const frames = useMission((s) => s.frames);
  const rows = data.map((f) => ({ c: f.ext_temp_c, km: f.altitude_m / 1000 }));
  const lo = frames.length ? Math.floor(Math.min(...frames.map((f) => f.ext_temp_c)) / 10) * 10 : -80;
  const hi = frames.length ? Math.ceil(Math.max(...frames.map((f) => f.ext_temp_c)) / 10) * 10 : 30;
  return (
    <ChartCard title="Atmospheric sounding" legend={<span className="num">°C · km</span>}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
          <CartesianGrid stroke={ui.border} strokeDasharray="2 4" vertical={false} />
          <XAxis type="number" dataKey="c" domain={[lo, hi]} {...axis} tickCount={5} />
          <YAxis type="number" dataKey="km" domain={[0, 30]} ticks={[0, 10, 20, 30]} {...axis} />
          <Line dataKey="km" stroke={ui.muted} strokeWidth={1.4} dot={{ r: 1.6, fill: ui.muted, strokeWidth: 0 }} isAnimationActive={false} />
          {current && (
            <ReferenceDot x={current.ext_temp_c} y={current.altitude_m / 1000} r={4} fill={ui.info} stroke={ui.bg} strokeWidth={1.5} />
          )}
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
