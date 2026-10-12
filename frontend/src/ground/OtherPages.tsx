import { useEffect, useMemo, useState } from 'react';
import { groundApi, type EventEntry, type Estimate } from './api';
import { FlightCharts, GatesPanel } from './FlightPage';
import { fmtMET, fmtNum, fmtUTC, STATUS_TAG } from './format';
import { SevTag } from './Shell';
import { useGround } from './store';

function Tile({ label, e, unit, dec = 1, value }: { label: string; e?: Estimate; unit?: string; dec?: number; value?: string }) {
  const status = e?.status;
  return (
    <div className="panel flex flex-col gap-1 px-3 py-2">
      <span className="lbl">{label}</span>
      <span className={`num text-[26px] leading-8 ${status === 'PREDICTED' ? 'text-pred' : status === 'UNKNOWN' ? 'text-stale' : 'text-ink'}`}>
        {value ?? fmtNum(e?.value ?? e?.last_value, dec)}
        {unit && <span className="unit ml-1 text-[13px]">{unit}</span>}
      </span>
      {status && <span className={`tag self-start ${STATUS_TAG[status]}`}>{status}</span>}
    </div>
  );
}

export function OverviewPage() {
  const snap = useGround((s) => s.snap);
  const g = snap?.ground;
  const E = g?.estimates ?? {};
  const recent = (snap?.alarms ?? []).slice(-8).reverse();
  return (
    <div className="flex flex-col gap-2 p-2">
      <div className="grid grid-cols-6 gap-2">
        <Tile label="Altitude (fused)" e={E.altitude} unit="m" dec={0} />
        <Tile label="Vertical speed" e={E.vertical_speed} unit="m/s" dec={2} />
        <Tile label="Tank pressure" e={E.tank_pressure && { ...E.tank_pressure, value: typeof E.tank_pressure.value === 'number' ? E.tank_pressure.value / 1e5 : null }} unit="bar" dec={1} />
        <Tile label="Motor temp" e={E.motor_temperature && { ...E.motor_temperature, value: typeof E.motor_temperature.value === 'number' ? E.motor_temperature.value - 273.15 : null }} unit="°C" dec={1} />
        <Tile label="Swing amplitude" e={E.swing_amplitude} unit="deg" dec={2} />
        <Tile label="Wind (forecast)" e={E.wind_speed} unit="m/s" dec={1} />
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_380px] gap-2">
        <FlightCharts snap={snap} />
        <div className="flex flex-col gap-2">
          <GatesPanel g={g} snap={snap} />
          <section className="panel" aria-labelledby="ov-alarms">
            <div className="panel-head">
              <h2 id="ov-alarms" className="panel-title">
                Recent alarms
              </h2>
              <a className="ml-auto text-[11px] text-info hover:underline" href="#/events">
                all events (6)
              </a>
            </div>
            <ul className="divide-y divide-line/60 text-[12px]">
              {recent.map((a) => (
                <li key={a.id} className={`flex items-center gap-2 px-3 py-1 ${a.acknowledged_by ? 'opacity-60' : ''}`}>
                  <SevTag sev={a.severity} />
                  <span className="num text-muted">{fmtMET(a.met)}</span>
                  <span className="truncate">{a.title}</span>
                </li>
              ))}
              {!recent.length && <li className="px-3 py-2 text-muted">none</li>}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

export function EventsPage() {
  const snap = useGround((s) => s.snap);
  const ack = useGround((s) => s.ack);
  const operator = useGround((s) => s.operator);
  const [events, setEvents] = useState<EventEntry[]>([]);
  const [onlyOpen, setOnlyOpen] = useState(false);
  useEffect(() => {
    let live = true;
    const pull = () => groundApi.events().then((e) => live && setEvents(e)).catch(() => undefined);
    void pull();
    const id = setInterval(pull, 2000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [snap?.mission_id]);
  const alarms = useMemo(() => [...(snap?.alarms ?? [])].reverse().filter((a) => !onlyOpen || !a.acknowledged_by), [snap?.alarms, onlyOpen]);
  return (
    <div className="grid h-full min-h-[700px] grid-cols-2 gap-2 p-2">
      <section className="panel flex min-h-0 flex-col" aria-labelledby="ev-alarms">
        <div className="panel-head">
          <h2 id="ev-alarms" className="panel-title">
            Alarms
          </h2>
          <label className="ml-auto flex items-center gap-1 text-[11.5px] text-muted">
            <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> unacknowledged only
          </label>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th>#</th>
                <th>Sev</th>
                <th>MET</th>
                <th>Alarm</th>
                <th>Source</th>
                <th>Acknowledged</th>
              </tr>
            </thead>
            <tbody>
              {alarms.map((a) => (
                <tr key={a.id} className={a.acknowledged_by ? 'opacity-60' : ''}>
                  <td className="num text-muted">{a.id}</td>
                  <td>
                    <SevTag sev={a.severity} />
                  </td>
                  <td className="num">{fmtMET(a.met)}</td>
                  <td title={JSON.stringify(a.evidence)}>{a.title}</td>
                  <td className="text-[11px] text-muted">{a.source}</td>
                  <td>
                    {a.acknowledged_by ? (
                      <span className="text-[11.5px]">
                        {a.acknowledged_by} <span className="num text-muted">{fmtUTC(a.acknowledged_utc)}</span>
                      </span>
                    ) : (
                      <button type="button" className="btn px-2 py-0.5 text-[11px]" disabled={!operator.trim()} onClick={() => void ack(a.id)}>
                        ACK
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel flex min-h-0 flex-col" aria-labelledby="ev-log">
        <div className="panel-head">
          <h2 id="ev-log" className="panel-title">
            Event log (append-only)
          </h2>
          <span className="ml-auto text-[11px] text-muted">{events.length} entries</span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th>#</th>
                <th>UTC</th>
                <th>MET</th>
                <th>Actor</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {[...events].reverse().map((e) => (
                <tr key={e.n}>
                  <td className="num text-muted">{e.n}</td>
                  <td className="num">{fmtUTC(e.utc)}</td>
                  <td className="num">{fmtMET(e.met)}</td>
                  <td className="text-[12px]">{e.actor}</td>
                  <td className="text-[12px]" title={JSON.stringify(e.detail)}>
                    {e.action}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export function Planned({ title, step, items }: { title: string; step: string; items: string[] }) {
  return (
    <div className="p-2">
      <section className="panel max-w-3xl p-4">
        <h2 className="panel-title">{title}</h2>
        <p className="mt-2 text-[13px] text-ink">Not implemented yet ({step} of the telemetry-console plan).</p>
        <ul className="mt-2 list-disc pl-5 text-[12.5px] text-muted">
          {items.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
