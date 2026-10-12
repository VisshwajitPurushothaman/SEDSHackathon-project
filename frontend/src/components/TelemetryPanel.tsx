import { Activity } from 'lucide-react';
import { ascentRateAt } from '../lib/playback';
import { useCurrentFrame, useMission } from '../store/useMission';
import { AltitudeChart, TankChart } from './Charts';
import { EventLog } from './EventLog';
import { Sounding } from './Sounding';
import { Ticker } from './Ticker';

const fmtInt = (n: number) => Math.round(n).toLocaleString('en-US').replace(/,/g, ' ');
const signed = (n: number, d: number) => `${n >= 0 ? '+' : ''}${n.toFixed(d)}`;

function Tickers() {
  const f = useCurrentFrame();
  const frames = useMission((s) => s.frames);
  const playhead = useMission((s) => s.uiPlayhead);
  const source = useMission((s) => s.source);
  const rate = f ? ascentRateAt(frames, playhead) : null;
  const dash = '--';
  const bool = (b: boolean | undefined) => (b === undefined ? dash : b ? 'true' : 'false');

  return (
    <div className="grid grid-cols-2 gap-1.5">
      <Ticker label="Mission time" field="time_s" value={f ? f.time_s.toFixed(1) : dash} unit="s" source="interp" />
      <Ticker
        label="Backend frame"
        value={f ? `${f.frameIndex + 1} / ${frames.length}` : dash}
        source="frame"
        tone="text-muted"
        field={source === 'demo' ? 'demo fixture' : 'buffered frames'}
      />
      <Ticker label="Altitude" field="altitude_m" value={f ? fmtInt(f.altitude_m) : dash} unit="m" source="interp" />
      <Ticker label="Ascent rate" value={rate === null ? dash : rate.toFixed(0)} unit="m/s" source="derived" />
      <Ticker label="Outside temp" field="ext_temp_c" value={f ? f.ext_temp_c.toFixed(1) : dash} unit="°C" source="interp" />
      <Ticker label="Outside pressure" field="ext_pres_hpa" value={f ? f.ext_pres_hpa.toFixed(1) : dash} unit="hPa" source="interp" />
      <Ticker label="Tank pressure" field="tank_pres_bar" value={f ? f.tank_pres_bar.toFixed(2) : dash} unit="bar" source="interp" />
      <Ticker label="Motor temp" field="motor_temp_c" value={f ? f.motor_temp_c.toFixed(1) : dash} unit="°C" source="interp" />
      <Ticker
        label="Mass lost"
        field="mass_loss_g"
        value={f ? f.mass_loss_g.toFixed(2) : dash}
        unit="g"
        source="interp"
        tone={f?.leak_detected ? 'text-fail' : f && f.mass_loss_g > 5 ? 'text-hold' : 'text-ink'}
      />
      <Ticker label="Tilt" field="tilt_deg" value={f ? signed(f.tilt_deg, 2) : dash} unit="°" source="interp" />
      <Ticker
        label="Leak status"
        field="leak_status"
        value={f?.leak_status ?? dash}
        source="frame"
        tone={f?.leak_detected ? 'text-fail' : 'text-ink'}
        flashKey={f?.leak_status}
      />
      <Ticker
        label="Leak detected"
        field="leak_detected"
        value={bool(f?.leak_detected)}
        source="frame"
        tone={f?.leak_detected ? 'text-fail' : 'text-ink'}
        flashKey={String(f?.leak_detected)}
      />
      <Ticker
        label="Gate status"
        field="gate_status"
        value={f?.gate_status ?? dash}
        source="frame"
        tone={f?.gate_status === 'ARMED' ? 'text-pass' : 'text-hold'}
        flashKey={f?.gate_status}
      />
      <Ticker
        label="is_abort · is_ignition"
        field="is_abort, is_ignition"
        value={f ? `${bool(f.is_abort)} · ${bool(f.is_ignition)}` : dash}
        source="frame"
        tone={f?.is_abort ? 'text-fail' : f?.is_ignition ? 'text-pass' : 'text-ink'}
        flashKey={f ? `${f.is_abort}${f.is_ignition}` : undefined}
      />
    </div>
  );
}

/** Right column: every frame field, three charts, and the event log. */
export function TelemetryPanel() {
  return (
    <section className="panel flex min-h-0 flex-col" aria-labelledby="telemetry-title">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Activity size={15} className="text-muted" aria-hidden />
        <h2 id="telemetry-title" className="panel-title">
          Telemetry
        </h2>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3">
        <Tickers />
        <AltitudeChart />
        <TankChart />
        <Sounding />
        <EventLog />
      </div>
    </section>
  );
}
