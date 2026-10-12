import { Activity } from 'lucide-react';
import type { GroundFrame } from './api';
import { useFlight } from './store';
import { formatEstimate, history, Spark, StatusBadge, type Fmt } from './ui';

interface Row {
  key: string;
  label: string;
  fmt?: Fmt;
  spark?: boolean;
}

const GROUPS: { title: string; rows: Row[] }[] = [
  {
    title: 'Position',
    rows: [
      { key: 'altitude', label: 'Altitude', fmt: { scale: 0.001, digits: 3, unit: 'km' }, spark: true },
      { key: 'vertical_speed', label: 'Vertical speed', fmt: { digits: 2 }, spark: true },
      { key: 'lat', label: 'Latitude', fmt: { digits: 5, unit: '°' } },
      { key: 'lon', label: 'Longitude', fmt: { digits: 5, unit: '°' } },
      { key: 'ground_speed', label: 'Ground speed', fmt: { digits: 1 }, spark: true },
      { key: 'heading', label: 'Heading', fmt: { digits: 0, unit: '°' } },
    ],
  },
  {
    title: 'Atmosphere',
    rows: [
      { key: 'air_temperature', label: 'Air temperature', fmt: { offset: -273.15, digits: 1, unit: '°C' }, spark: true },
      { key: 'pressure', label: 'Pressure', fmt: { scale: 0.01, digits: 2, unit: 'hPa' }, spark: true },
      { key: 'air_density', label: 'Density', fmt: { digits: 4 } },
      { key: 'wind_speed', label: 'Wind (forecast)', fmt: { digits: 1 } },
      { key: 'wind_from', label: 'Wind from', fmt: { digits: 0, unit: '°' } },
    ],
  },
  {
    title: 'Balloon',
    rows: [
      { key: 'balloon_volume', label: 'Volume', fmt: { digits: 0, unit: 'm³' }, spark: true },
      { key: 'balloon_vent_rate', label: 'Vent rate', fmt: { scale: 1000, digits: 1, unit: 'g/s' }, spark: true },
      { key: 'balloon_venting', label: 'Venting (> 3σ)' },
      { key: 'free_lift', label: 'Free lift (excl. ballast)', fmt: { digits: 0 } },
      { key: 'ballast_drop', label: 'Ballast drop', fmt: { scale: 1000, digits: 1, unit: 'g/s' } },
    ],
  },
  {
    title: 'Pressurant tank',
    rows: [
      { key: 'tank_pressure', label: 'Pressure', fmt: { scale: 1e-5, digits: 2, unit: 'bar' }, spark: true },
      { key: 'tank_temperature', label: 'Temperature', fmt: { offset: -273.15, digits: 1, unit: '°C' }, spark: true },
      { key: 'tank_mass', label: 'Est. He mass', fmt: { scale: 1000, digits: 1, unit: 'g' }, spark: true },
      { key: 'tank_leak_slope', label: 'Mass slope', fmt: { scale: 1e6, digits: 2, unit: 'mg/s' }, spark: true },
      { key: 'tank_leak_t_stat', label: 'Slope t-stat', fmt: { digits: 1, unit: '' } },
      { key: 'tank_leak_detected', label: 'Leak declared' },
      { key: 'tank_leak_diameter', label: 'Est. hole diameter', fmt: { digits: 0, unit: 'µm' } },
    ],
  },
  {
    title: 'Motor & swing',
    rows: [
      { key: 'motor_temperature', label: 'Motor temperature', fmt: { offset: -273.15, digits: 1, unit: '°C' }, spark: true },
      { key: 'motor_heater_on', label: 'Heater on' },
      { key: 'swing_amplitude', label: 'Swing amplitude', fmt: { digits: 2, unit: '°' }, spark: true },
      { key: 'tilt', label: 'Tilt (instant)', fmt: { digits: 2, unit: '°' } },
    ],
  },
  {
    title: 'Link',
    rows: [
      { key: 'link_rssi', label: 'RSSI', fmt: { digits: 1, unit: 'dBm' }, spark: true },
      { key: 'link_packet_loss', label: 'Packet loss', fmt: { scale: 100, digits: 1, unit: '%' } },
      { key: 'link_latency', label: 'Latency', fmt: { digits: 2, unit: 's' } },
      { key: 'link_last_packet_age', label: 'Last packet age', fmt: { digits: 1, unit: 's' } },
    ],
  },
  {
    title: 'Rocket (illustrative model, not validated)',
    rows: [
      { key: 'rocket_altitude', label: 'Altitude', fmt: { scale: 0.001, digits: 2, unit: 'km' } },
      { key: 'rocket_downrange', label: 'Downrange', fmt: { scale: 0.001, digits: 2, unit: 'km' } },
      { key: 'rocket_speed', label: 'Speed', fmt: { digits: 0, unit: 'm/s' } },
    ],
  },
];

/** Every number with its status badge and a sparkline over the frames received so far. */
export function TelemetryGroups({ frame }: { frame: GroundFrame | null }) {
  const frames = useFlight((s) => s.frames);
  const cursor = useFlight((s) => s.cursor);
  if (!frame) return null;
  const E = frame.estimates;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <Activity size={13} aria-hidden /> Atmosphere source: <span className="num text-ink">{frame.atmo_source}</span>
      </div>
      {GROUPS.map((g) => {
        const rows = g.rows.filter((r) => E[r.key] !== undefined);
        if (!rows.length) return null;
        return (
          <section key={g.title} className="rounded-lg border border-line/70 bg-panel2/40 px-2.5 py-1.5" aria-label={g.title}>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">{g.title}</h3>
            <dl className="flex flex-col gap-0.5">
              {rows.map((r) => {
                const e = E[r.key];
                const f = formatEstimate(e, r.fmt);
                const tip = `${e.source}${e.age_s !== null && e.age_s !== undefined ? ` · age ${e.age_s.toFixed(1)} s` : ''}${e.sigma ? ` · σ ${e.sigma.toPrecision(2)}` : ''}${e.status === 'UNKNOWN' ? ' · stale: last value in brackets' : ''}`;
                return (
                  <div key={r.key} className="flex items-center gap-2" title={tip}>
                    <dt className="min-w-0 flex-1 truncate text-[11.5px] text-muted">{r.label}</dt>
                    {r.spark && <Spark pts={history(frames, cursor, r.key)} scale={r.fmt?.scale} offset={r.fmt?.offset} />}
                    <dd className={`num w-[96px] shrink-0 text-right text-[12.5px] ${e.status === 'UNKNOWN' ? 'text-muted' : e.status === 'PREDICTED' ? 'text-hold' : 'text-ink'}`}>
                      {f.text}
                      {f.unit && <span className="unit ml-0.5 text-[10px]">{f.unit}</span>}
                    </dd>
                    <StatusBadge status={e.status} title={tip} />
                  </div>
                );
              })}
            </dl>
          </section>
        );
      })}
    </div>
  );
}
