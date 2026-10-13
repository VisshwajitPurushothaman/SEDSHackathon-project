import { useMemo, useState } from 'react';
import type { ChannelRow, Estimate, Gate, GroundFrame, HistoryRow, Limits, Snapshot } from './api';
import { fmtAge, fmtBand, fmtChannel, fmtMET, fmtNum, LIMIT_TAG, STATUS_TAG } from './format';
import { ArtificialHorizon } from './ArtificialHorizon';
import { MissionLiveMap } from './MissionLiveMap';
import { StripChart, type StripSeries } from './StripChart';
import { useGround } from './store';
import { ui } from '../theme/tokens';

// ------------------------------------------------------------------ telemetry table

type SortKey = 'subsystem' | 'mnemonic' | 'status' | 'limit' | 'age';
const LIMIT_RANK: Record<string, number> = { ALARM: 0, CAUTION: 1, NOMINAL: 2, NO_LIMIT: 3 };
const SUBSYSTEMS = ['ALL', 'NAV', 'ENV', 'ATT', 'PROP', 'PWR', 'COMM', 'VEHICLE', 'RAW'];

export function TelemetryTable({ channels }: { channels: ChannelRow[] }) {
  const [filter, setFilter] = useState('');
  const [sub, setSub] = useState('ALL');
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'subsystem', dir: 1 });
  const order = useMemo(() => new Map(channels.map((c, i) => [c.mnemonic, i])), [channels]);
  const rows = useMemo(() => {
    const f = filter.trim().toUpperCase();
    const out = channels.filter((c) => (sub === 'ALL' || c.subsystem === sub) && (!f || c.mnemonic.includes(f) || c.description.toUpperCase().includes(f)));
    const cmp = (a: ChannelRow, b: ChannelRow): number => {
      switch (sort.key) {
        case 'mnemonic':
          return a.mnemonic.localeCompare(b.mnemonic);
        case 'status':
          return a.status.localeCompare(b.status);
        case 'limit':
          return LIMIT_RANK[a.limit] - LIMIT_RANK[b.limit];
        case 'age':
          return (a.age_s ?? 1e9) - (b.age_s ?? 1e9);
        default:
          return SUBSYSTEMS.indexOf(a.subsystem) - SUBSYSTEMS.indexOf(b.subsystem) || (order.get(a.mnemonic) ?? 0) - (order.get(b.mnemonic) ?? 0);
      }
    };
    return out.sort((a, b) => cmp(a, b) * sort.dir);
  }, [channels, filter, sub, sort, order]);

  const head = (key: SortKey | null, label: string, cls = '') => (
    <th className={cls} aria-sort={key && sort.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}>
      {key ? (
        <button type="button" className="uppercase tracking-[0.12em] hover:text-ink" onClick={() => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : 1 }))}>
          {label}
          {sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
        </button>
      ) : (
        label
      )}
    </th>
  );

  let lastSub = '';
  return (
    <section className="panel flex min-h-0 flex-col" aria-labelledby="tlm-title">
      <div className="panel-head">
        <h2 id="tlm-title" className="panel-title">
          Telemetry
        </h2>
        <input className="field ml-auto w-32" placeholder="filter" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter channels" />
        <select className="field" value={sub} onChange={(e) => setSub(e.target.value)} aria-label="Subsystem">
          {SUBSYSTEMS.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="tbl" aria-label="Telemetry channels">
          <thead>
            <tr>
              {head('mnemonic', 'Mnemonic')}
              <th className="text-right">Value</th>
              <th>Units</th>
              {head('status', 'Status')}
              {head('limit', 'Limit')}
              <th>Yellow</th>
              <th>Red</th>
              {head('age', 'Age', 'text-right')}
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const group = sort.key === 'subsystem' && c.subsystem !== lastSub;
              lastSub = c.subsystem;
              const stale = c.status !== 'MEASURED';
              const tint = c.limit === 'ALARM' ? 'bg-fail/15' : c.limit === 'CAUTION' ? 'bg-hold/10' : '';
              const lt = LIMIT_TAG[c.limit];
              return [
                group ? (
                  <tr key={`g-${c.subsystem}`}>
                    <td colSpan={8} className="bg-panel2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.16em] text-muted">
                      {c.subsystem}
                    </td>
                  </tr>
                ) : null,
                <tr key={c.mnemonic} className={`${tint} ${stale ? 'opacity-60' : ''}`} title={c.description}>
                  <td className="mn text-ink">{c.mnemonic}</td>
                  <td className={`num text-right text-[13px] ${c.limit === 'ALARM' ? 'text-fail' : c.limit === 'CAUTION' ? 'text-hold' : 'text-ink'}`}>{fmtChannel(c)}</td>
                  <td className="unit text-[11.5px]">{c.units}</td>
                  <td>
                    <span className={`tag ${STATUS_TAG[c.status]}`}>{c.status}</span>
                  </td>
                  <td title={c.limits?.basis}>
                    <span className={`tag ${lt.cls}`}>{lt.text}</span>
                  </td>
                  <td className="num whitespace-nowrap text-[10.5px] text-hold/90">{fmtBand(c.limits?.yellow)}</td>
                  <td className="num whitespace-nowrap text-[10.5px] text-fail/90">{fmtBand(c.limits?.red)}</td>
                  <td className={`num text-right text-[11.5px] ${stale ? 'text-stale' : 'text-muted'}`}>{fmtAge(c.age_s)}</td>
                </tr>,
              ];
            })}
          </tbody>
        </table>
        {!channels.length && <p className="p-3 text-[12px] text-muted">No telemetry received yet.</p>}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ strip charts

const WINDOWS: { label: string; s: number | null }[] = [
  { label: 'ALL', s: null },
  { label: '30 MIN', s: 1800 },
  { label: '5 MIN', s: 300 },
];

function col(rows: HistoryRow[], key: string): (number | null)[] {
  return rows.map((r) => (typeof r[key] === 'number' ? (r[key] as number) : null));
}

export function useWindowedRows(windowS: number | null): HistoryRow[] {
  const rows = useGround((s) => s.rows);
  return useMemo(() => {
    if (windowS === null || !rows.length) return rows;
    const t0 = rows[rows.length - 1].met - windowS;
    let i = rows.length - 1;
    while (i > 0 && rows[i - 1].met >= t0) i--;
    return rows.slice(i);
  }, [rows, windowS]);
}

function limitsOf(channels: ChannelRow[] | undefined, m: string): Limits | null {
  return channels?.find((c) => c.mnemonic === m)?.limits ?? null;
}

export function FlightCharts({ snap }: { snap: Snapshot | null }) {
  const [win, setWin] = useState<number | null>(null);
  const rows = useWindowedRows(win);
  const los = useGround((s) => s.los);
  const x = useMemo(() => rows.map((r) => r.met), [rows]);
  const charts = useMemo(() => {
    const meas = (label: string, key: string, color: string = ui.pass): StripSeries => ({ label, values: col(rows, key), color, points: true });
    return [
      { title: 'Altitude', unit: 'm', m: 'GNSS_ALT', dec: 0, band: true, series: [meas('GNSS_ALT', 'GNSS_ALT'), { label: 'FUSED', values: col(rows, 'fused_altitude'), color: ui.white }] },
      { title: 'Vertical speed', unit: 'm/s', m: 'GNSS_VU', dec: 2, band: true, series: [meas('GNSS_VU', 'GNSS_VU'), { label: 'FUSED', values: col(rows, 'fused_vertical_speed'), color: ui.white }] },
      { title: 'Tank pressure', unit: 'bar', m: 'TANK_P', dec: 1, band: true, series: [meas('TANK_P', 'TANK_P'), { label: 'PRESSURANT TANK', values: col(rows, 'TANK_P'), color: ui.hold }] },
      { title: 'Gauge pressure', unit: 'hPa', m: 'GAUGE_P', dec: 2, band: true, series: [meas('GAUGE_P', 'GAUGE_P'), { label: 'BALLOON SUPERPRESSURE', values: col(rows, 'GAUGE_P'), color: '#5cd6ff' }] },
      { title: 'Gondola tilt', unit: 'deg', m: 'IMU_TILT', dec: 2, band: false, series: [meas('IMU_TILT', 'IMU_TILT')] },
    ];
  }, [rows]);
  return (
    <section className="flex min-h-0 flex-col gap-2" aria-label="Strip charts">
      <div className="flex items-center gap-2">
        <span className="panel-title">Strip charts</span>
        <span className="text-[11px] text-muted">points = measured · line = fused · cyan = predicted band · grey = LOS</span>
        <div className="seg ml-auto" role="group" aria-label="Chart window">
          {WINDOWS.map((w) => (
            <button key={w.label} type="button" aria-pressed={win === w.s} onClick={() => setWin(w.s)}>
              {w.label}
            </button>
          ))}
        </div>
      </div>
      {charts.map((c) => (
        <StripChart
          key={c.title}
          title={c.title}
          unit={c.unit}
          x={x}
          series={c.series}
          band={c.band ? { lo: col(rows, `band_${c.m}_lo`), hi: col(rows, `band_${c.m}_hi`) } : null}
          limits={limitsOf(snap?.channels, c.m)}
          los={los}
          decimals={c.dec}
          height={112}
        />
      ))}
    </section>
  );
}

// ------------------------------------------------------------------ fused state

/** sigmaUnit: the horizontal sigma of lat/lon is in metres. */
function EstRow({ label, e, unit, dec = 1, sigmaUnit }: { label: string; e: Estimate | undefined; unit: string; dec?: number; sigmaUnit?: string }) {
  const status = e?.status ?? 'UNKNOWN';
  const v = e?.value ?? e?.last_value;
  return (
    <tr>
      <td className="lbl">{label}</td>
      <td className={`num whitespace-nowrap text-right text-[13px] ${status === 'PREDICTED' ? 'text-pred' : status === 'UNKNOWN' ? 'text-stale' : 'text-ink'}`}>
        {fmtNum(v, dec)} <span className="unit text-[11px]">{unit}</span>
        <div className="text-[10.5px] text-muted">{typeof e?.sigma === 'number' ? (sigmaUnit ? `±${e.sigma.toFixed(1)} ${sigmaUnit}` : `±${e.sigma.toFixed(dec)}`) : ''}</div>
      </td>
      <td>
        <span className={`tag ${STATUS_TAG[status]}`}>{status}</span>
      </td>
    </tr>
  );
}

const TRUST_TAG: Record<string, string> = { ACCEPTED: 'tag-nom', SUSPECT: 'tag-cau', EXCLUDED: 'tag-alm' };

export function FusedPanel({ g }: { g: GroundFrame | null | undefined }) {
  const E = g?.estimates ?? {};
  const nav = g?.gates.nav;
  const nv = (nav?.value ?? null) as { chi2_h?: number; chi2_v?: number } | null;
  const nl = (nav?.limit ?? null) as { chi2_h?: number; chi2_v?: number } | null;
  return (
    <section className="panel" aria-labelledby="fused-title">
      <div className="panel-head">
        <h2 id="fused-title" className="panel-title">
          Fused state
        </h2>
        <span className="ml-auto text-[10.5px] text-muted">Kalman · 1σ</span>
      </div>
      <table className="tbl">
        <tbody>
          <EstRow label="Altitude" e={E.altitude} unit="m" dec={0} />
          <EstRow label="Vert. speed" e={E.vertical_speed} unit="m/s" dec={2} />
          <EstRow label="v̄z 60 s" e={E.vertical_speed_mean} unit="m/s" dec={2} />
          <EstRow label="Latitude" e={E.lat} unit="deg" dec={5} sigmaUnit="m" />
          <EstRow label="Longitude" e={E.lon} unit="deg" dec={5} sigmaUnit="m" />
          <EstRow label="Gnd speed" e={E.ground_speed} unit="m/s" dec={1} />
        </tbody>
      </table>
      <div className="grid grid-cols-3 gap-1 border-t border-line p-2">
        {(['gnss', 'navic', 'baro'] as const).map((k) => (
          <div key={k} className="flex flex-col items-start gap-0.5">
            <span className="mn text-[11px] text-muted">{k}</span>
            <span className={`tag ${TRUST_TAG[g?.trust?.[k]?.state ?? ''] ?? 'tag-stale'}`}>{g?.trust?.[k]?.state ?? '---'}</span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 border-t border-line px-3 py-1.5 text-[11.5px]">
        <span className="lbl">χ²</span>
        <span className="num">
          H {fmtNum(nv?.chi2_h, 2)} <span className="text-muted">/ {fmtNum(nl?.chi2_h, 2)}</span>
        </span>
        <span className="num">
          V {fmtNum(nv?.chi2_v, 2)} <span className="text-muted">/ {fmtNum(nl?.chi2_v, 2)}</span>
        </span>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ gates and poll

const GATE_LABEL: Record<string, string> = { float: 'FLOAT', nav: 'NAV', motor: 'MOTOR', swing: 'SWING', impact: 'IMPACT' };

function gateValue(g: Gate): string {
  const v = g.value as Record<string, number> | number | null;
  if (v === null || v === undefined) return '---';
  if (typeof v === 'number') return fmtNum(v, 2);
  if (g.id === 'float') return `${fmtNum(v.z_m / 1000, 2)} km · v̄z ${fmtNum(v.vz_m_s, 2)}`;
  if (g.id === 'nav') return `χ² ${fmtNum(v.chi2_h, 1)} / ${fmtNum(v.chi2_v, 1)}`;
  return JSON.stringify(v).slice(0, 40);
}

const REC: Record<string, { cls: string; text: string }> = {
  GO_RECOMMENDED: { cls: 'tag-nom', text: 'GO RECOMMENDED' },
  NO_GO: { cls: 'tag-cau', text: 'NO-GO' },
  ABORT_RECOMMENDED: { cls: 'tag-alm', text: 'ABORT RECOMMENDED' },
};

export function GatesPanel({ g, snap }: { g: GroundFrame | null | undefined; snap: Snapshot | null }) {
  const poll = useGround((s) => s.poll);
  const operator = useGround((s) => s.operator);
  const [note, setNote] = useState('');
  const rec = g ? REC[g.recommendation] : null;
  const polls = snap?.polls ?? [];
  const go = g?.recommendation === 'GO_RECOMMENDED';
  return (
    <section className="panel" aria-labelledby="gates-title">
      <div className="panel-head">
        <h2 id="gates-title" className="panel-title">
          Launch-commit gates
        </h2>
        {rec && <span className={`tag ${rec.cls} ml-auto text-[12px]`}>{rec.text}</span>}
      </div>
      <table className="tbl">
        <tbody>
          {['float', 'nav', 'motor', 'swing', 'impact'].map((id) => {
            const gt = g?.gates[id];
            const ok = gt?.passed;
            return (
              <tr key={id} title={gt?.detail}>
                <td className="mn">{GATE_LABEL[id]}</td>
                <td className="num text-[12px]">{gt ? gateValue(gt) : '---'}</td>
                <td className="num text-right text-[11px] text-muted">{gt && typeof gt.margin === 'number' ? `margin ${fmtNum(gt.margin, 1)}` : ''}</td>
                <td className="text-right">
                  <span className={`tag ${ok === true ? 'tag-nom' : ok === false ? 'tag-alm' : 'tag-stale'}`}>{ok === true ? 'PASS' : ok === false ? 'FAIL' : 'N/A'}</span>
                </td>
              </tr>
            );
          })}
          <tr>
            <td className="mn">TANK</td>
            <td colSpan={2} className="truncate text-[11.5px] text-muted">
              {g?.tank_criterion.detail ?? '---'}
            </td>
            <td className="text-right">
              <span className={`tag ${g?.tank_criterion.passed === false ? 'tag-alm' : g?.tank_criterion.passed ? 'tag-nom' : 'tag-stale'}`}>
                {g?.tank_criterion.passed === false ? 'FAIL' : g?.tank_criterion.passed ? 'PASS' : 'N/A'}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <div className="border-t border-line p-2">
        <div className="mb-1.5 flex items-center gap-2">
          <span className="lbl">GO / NO-GO poll</span>
          <span className="text-[10.5px] text-muted">recorded with operator name · commands nothing</span>
        </div>
        <div className="flex items-center gap-1.5">
          <input className="field min-w-0 flex-1" placeholder="note (optional)" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} aria-label="Poll note" />
          <button type="button" className="btn border-pass/60 text-pass" disabled={!go || !operator.trim()} onClick={() => void poll('GO', note).then((ok) => ok && setNote(''))} title={go ? '' : 'GO can be polled only while GO is recommended'}>
            POLL GO
          </button>
          <button type="button" className="btn border-fail/60 text-fail" disabled={!g || !operator.trim()} onClick={() => void poll('NO_GO', note).then((ok) => ok && setNote(''))}>
            POLL NO-GO
          </button>
        </div>
        {polls.length > 0 && (
          <ul className="mt-1.5 max-h-20 overflow-auto text-[11.5px]" aria-label="Recorded polls">
            {[...polls].reverse().map((p) => (
              <li key={p.n} className="flex gap-2">
                <span className={`tag ${p.vote === 'GO' ? 'tag-nom' : 'tag-alm'}`}>{p.vote}</span>
                <span className="num text-muted">{fmtMET(p.met)}</span>
                <span className="text-ink">{p.operator}</span>
                <span className="truncate text-muted">{p.note}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ link

function Sparkline({ values, color, max }: { values: number[]; color: string; max: number }) {
  if (values.length < 2) return <div className="h-8" />;
  const w = 200;
  const h = 32;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - (Math.min(v, max) / max) * (h - 2) - 1).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-8 w-full" preserveAspectRatio="none" aria-hidden>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function LinkPanel({ snap }: { snap: Snapshot | null }) {
  const samples = useGround((s) => s.linkSamples);
  const l = snap?.link;
  const gaps = l?.gaps ?? [];
  const los = l?.los_periods ?? [];
  const maxLoss = Math.max(5, ...samples.map((s) => s.loss));
  return (
    <section className="panel" aria-labelledby="link-title">
      <div className="panel-head">
        <h2 id="link-title" className="panel-title">
          Link
        </h2>
        <span className="ml-auto text-[10.5px] text-muted">{snap?.receiver.listen ?? snap?.receiver.playback_file ?? ''}</span>
      </div>
      <div className="grid grid-cols-3 gap-x-3 gap-y-1 px-3 py-2 text-[12px]">
        {[
          ['frames', l?.frames_received ?? '---'],
          ['max seq', l?.max_seq ?? '---'],
          ['loss', l?.loss_pct !== undefined ? `${l.loss_pct.toFixed(2)} %` : '---'],
          ['dup', l?.duplicates ?? '---'],
          ['reorder', l?.out_of_order ?? '---'],
          ['HMAC fail', l?.hmac_failures ?? '---'],
        ].map(([k, v]) => (
          <div key={k as string} className="flex items-baseline justify-between gap-1">
            <span className="lbl">{k}</span>
            <span className={`num ${k === 'HMAC fail' && Number(v) > 0 ? 'text-fail' : 'text-ink'}`}>{v}</span>
          </div>
        ))}
      </div>
      <div className="px-3">
        <span className="lbl">packet loss % (last {samples.length} samples)</span>
        <Sparkline values={samples.map((s) => s.loss)} color={ui.hold} max={maxLoss} />
      </div>
      <div className="grid grid-cols-2 gap-2 border-t border-line p-2 text-[11.5px]">
        <div>
          <span className="lbl">LOS periods ({los.length})</span>
          <ul className="num max-h-16 overflow-auto">
            {los.slice(-6).map((p, i) => (
              <li key={i}>
                {fmtMET(p.start_met)} → {p.end_met === null ? <span className="text-fail">ongoing</span> : fmtMET(p.end_met)}
              </li>
            ))}
            {!los.length && <li className="text-muted">none</li>}
          </ul>
        </div>
        <div>
          <span className="lbl">sequence gaps ({gaps.length})</span>
          <ul className="num max-h-16 overflow-auto">
            {gaps.slice(-8).map(([a, b]) => (
              <li key={a}>{a === b ? a : `${a}–${b}`}</li>
            ))}
            {!gaps.length && <li className="text-muted">none</li>}
          </ul>
        </div>
      </div>
    </section>
  );
}

export default function FlightPage() {
  const snap = useGround((s) => s.snap);
  const g = snap?.ground;
  const [flightView, setFlightView] = useState<'mission_planner' | 'telemetry_grid'>('mission_planner');

  const dt = snap?.digital_twin_telemetry;
  const imu = dt?.imu_sensor;
  const roll = imu?.attitude_rpy_deg?.[0] ?? 0;
  const pitch = imu?.attitude_rpy_deg?.[1] ?? 0;
  const yaw = imu?.attitude_rpy_deg?.[2] ?? 0;
  const rawAlt = dt?.gnss_coordinates?.geodetic_wgs84?.alt_m ?? g?.estimates?.altitude?.value;
  const alt = typeof rawAlt === 'number' ? rawAlt : Number(rawAlt) || 0;

  const rawLat = dt?.gnss_coordinates?.geodetic_wgs84?.lat_deg ?? g?.estimates?.lat?.value;
  const lat = typeof rawLat === 'number' ? rawLat : Number(rawLat) || 37.1;

  const rawLon = dt?.gnss_coordinates?.geodetic_wgs84?.lon_deg ?? g?.estimates?.lon?.value;
  const lon = typeof rawLon === 'number' ? rawLon : Number(rawLon) || -6.733;

  const rawSpd = g?.estimates?.ground_speed?.value;
  const spd = typeof rawSpd === 'number' ? rawSpd : Number(rawSpd) || 0;

  const rawVz = g?.estimates?.vertical_speed?.value;
  const vz = typeof rawVz === 'number' ? rawVz : Number(rawVz) || 0;

  const isAttached = dt?.payload_state?.is_attached ?? true;
  const flightPhase = dt?.flight_phase ?? snap?.fc_phase ?? 'IN-FLIGHT';

  return (
    <div className="flex h-full min-h-[760px] flex-col p-2 gap-2">
      {/* Sub-navigation bar inside Mission/Flight section */}
      <div className="flex items-center justify-between border-b border-[#222e42] bg-[#0c1422] px-3 py-1.5 rounded">
        <div className="flex items-center gap-2">
          <span className="text-[12px] font-bold uppercase tracking-wider text-[#5cd6ff]">
            Flight Mission Operations
          </span>
          <span className="rounded bg-[#1a2638] px-2 py-0.5 text-[10px] text-muted">
            Phase: {flightPhase}
          </span>
        </div>
        <div className="flex items-center gap-1 bg-[#070b12] p-1 rounded border border-[#222e42]">
          <button
            type="button"
            onClick={() => setFlightView('mission_planner')}
            className={`rounded px-3 py-1 text-[11px] font-bold transition-colors ${
              flightView === 'mission_planner'
                ? 'bg-[#1e74ba] text-white shadow'
                : 'text-muted hover:text-white'
            }`}
          >
            🧭 Mission Planner HUD & Map
          </button>
          <button
            type="button"
            onClick={() => setFlightView('telemetry_grid')}
            className={`rounded px-3 py-1 text-[11px] font-bold transition-colors ${
              flightView === 'telemetry_grid'
                ? 'bg-[#1e74ba] text-white shadow'
                : 'text-muted hover:text-white'
            }`}
          >
            📊 Telemetry Channels & Charts
          </button>
        </div>
      </div>

      {flightView === 'mission_planner' ? (
        <div className="grid flex-1 grid-cols-1 gap-2 lg:grid-cols-[440px_minmax(0,1fr)_330px]">
          {/* PFD Gyroscopic Display */}
          <div className="flex flex-col gap-2 min-h-0">
            <div className="h-[360px] shrink-0">
              <ArtificialHorizon
                rollDeg={roll}
                pitchDeg={pitch}
                yawDeg={yaw}
                altitudeM={alt}
                speedMps={spd}
                verticalSpeedMps={vz}
                armedStatus={isAttached ? 'GONDOLA LOCKED' : 'RELEASED'}
                gpsStatus="GPS: 3D FIX"
                flightPhase={flightPhase}
              />
            </div>
            {/* Mission status quick summary */}
            <div className="flex-1 rounded border border-[#222e42] bg-[#0c1422] p-3">
              <span className="lbl block mb-2">Live Flight Metrics</span>
              <div className="grid grid-cols-2 gap-2 text-[12px]">
                <div className="rounded border border-[#30234a] bg-[#140e24] p-2">
                  <span className="text-[10.5px] text-[#b388ff]">Altitude</span>
                  <div className="text-[18px] font-bold text-[#b388ff]">{Math.round(alt).toLocaleString()} m</div>
                </div>
                <div className="rounded border border-[#0d3b19] bg-[#051c0a] p-2">
                  <span className="text-[10.5px] text-[#00e676]">Heading</span>
                  <div className="text-[18px] font-bold text-[#00e676]">{Math.round(yaw)}°</div>
                </div>
                <div className="rounded border border-[#4d2f09] bg-[#211403] p-2">
                  <span className="text-[10.5px] text-[#ff9800]">Groundspeed</span>
                  <div className="text-[18px] font-bold text-[#ff9800]">{spd.toFixed(1)} m/s</div>
                </div>
                <div className="rounded border border-[#4d4409] bg-[#211d03] p-2">
                  <span className="text-[10.5px] text-[#ffeb3b]">Vert. Speed</span>
                  <div className="text-[18px] font-bold text-[#ffeb3b]">{vz >= 0 ? `+${vz.toFixed(1)}` : vz.toFixed(1)} m/s</div>
                </div>
              </div>
            </div>
          </div>

          {/* Live Location Moving Map */}
          <div className="h-full min-h-[500px]">
            <MissionLiveMap
              vehicleLat={lat}
              vehicleLon={lon}
              vehicleAltM={alt}
              yawDeg={yaw}
              flightPhase={flightPhase}
            />
          </div>

          {/* Right side: Commit gates and Fused state */}
          <div className="flex min-h-0 flex-col gap-2 overflow-auto">
            <GatesPanel g={g} snap={snap} />
            <FusedPanel g={g} />
            <LinkPanel snap={snap} />
          </div>
        </div>
      ) : (
        <div className="grid flex-1 min-h-[700px] grid-cols-[minmax(520px,1.05fr)_minmax(380px,1fr)_330px] gap-2">
          <TelemetryTable channels={snap?.channels ?? []} />
          <div className="min-h-0 overflow-auto">
            <FlightCharts snap={snap} />
          </div>
          <div className="flex min-h-0 flex-col gap-2 overflow-auto">
            <GatesPanel g={g} snap={snap} />
            <FusedPanel g={g} />
            <LinkPanel snap={snap} />
          </div>
        </div>
      )}
    </div>
  );
}
