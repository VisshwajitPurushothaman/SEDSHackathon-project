import { useMemo, useState } from 'react';
import { ArtificialHorizon } from './ArtificialHorizon';
import { GatesPanel } from './FlightPage';
import { fmtAge, fmtMET, STATUS_TAG } from './format';
import { chan, chanText, est, type Reading } from './live';
import { MissionLiveMap } from './MissionLiveMap';
import { SevTag } from './Shell';
import { useGround } from './store';
import { useProfile } from './PreflightPage';

/** Overview: one pipeline only. Every value is a channel or a fused estimate with its status; absent = UNKNOWN. */

function Tile({ label, r, dec, unit }: { label: string; r: Reading; dec: number; unit: string }) {
  const st = r.status as keyof typeof STATUS_TAG;
  return (
    <div className="panel flex flex-col gap-0.5 px-3 py-2">
      <span className="lbl">{label}</span>
      <span className={`num text-[22px] leading-7 ${r.status === 'PREDICTED' ? 'text-pred' : r.value === null ? 'text-stale' : 'text-ink'}`}>
        {r.value === null ? '---' : r.value.toFixed(dec)}
        <span className="unit ml-1 text-[12px]">{unit}</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className={`tag ${STATUS_TAG[st] ?? 'tag-stale'}`}>{r.status}</span>
        {r.age_s !== null && r.status === 'STALE' && <span className="num text-[10.5px] text-stale">{fmtAge(r.age_s)}</span>}
      </span>
    </div>
  );
}

const REASONS: Record<string, string> = {
  zero_time: 'zero-time (PRELAUNCH handshakes)',
  not_locked: 'not advancing (no lock yet)',
  repeat: 'repeated MET',
  backward: 'MET went backwards',
  jump: 'MET jump (other stream)',
  foreign_source: 'other source IP',
  malformed: 'malformed',
};

export function OverviewPage() {
  const snap = useGround((s) => s.snap);
  const track = useGround((s) => s.track);
  const [showRaw, setShowRaw] = useState(false);
  const g = snap?.ground;
  const dts = snap?.digital_twin;
  const flt = (dts as unknown as { filter?: Record<string, unknown> } | undefined)?.filter as
    | { source: string | null; epoch: number; accepted: number; rejected: Record<string, number>; rate_hz: number; last_accept_age_s: number | null; last_met_s: number | null }
    | undefined;
  const roll = chan(snap, 'IMU_ROLL');
  const pitch = chan(snap, 'IMU_PITCH');
  const yaw = chan(snap, 'IMU_YAW');
  const alt = est(snap, 'altitude');
  const lat = est(snap, 'lat');
  const lon = est(snap, 'lon');
  const vz = est(snap, 'vertical_speed');
  const gs = est(snap, 'ground_speed');
  const rawPhase = chanText(snap, 'FC_PHASE_RAW');
  const paused = chan(snap, 'TWIN_PAUSED').value === 1;
  const trail = useMemo(() => track.filter((p) => p.status === 'FUSED').map((p) => [p.lat, p.lon] as [number, number]), [track]);
  const recent = (snap?.alarms ?? []).slice(-8).reverse();
  const site = useProfile()?.range.site ?? null;
  const hasFix = lat.value !== null && lon.value !== null && alt.value !== null && site !== null;
  const hasAtt = roll.value !== null && pitch.value !== null && yaw.value !== null;

  return (
    <div className="flex flex-col gap-2 p-2">
      <section className="panel flex flex-wrap items-center gap-x-5 gap-y-1 px-3 py-2" aria-label="Mission summary">
        <span className="lbl">Phase</span>
        <span className="num text-[14px] text-ink">
          {snap?.fc_phase ?? '---'}
          {rawPhase && <span className="ml-2 text-[11px] text-muted">sender: {rawPhase}</span>}
        </span>
        <span className="lbl">MET</span>
        <span className="num text-[14px]">{fmtMET(snap?.met)}</span>
        <span className="lbl">Recommendation</span>
        <span className="num text-[14px]">{g?.recommendation ?? '---'}</span>
        {paused && <span className="tag tag-cau">SENDER PAUSED: values held by Laptop A</span>}
        {!snap?.mission_id && <span className="tag tag-stale">NO MISSION: no advancing telemetry stream locked</span>}
      </section>

      <div className="grid grid-cols-6 gap-2">
        <Tile label="Altitude (fused)" r={alt} dec={0} unit="m" />
        <Tile label="Vertical speed (fused)" r={vz} dec={2} unit="m/s" />
        <Tile label="Ground speed (fused)" r={gs} dec={1} unit="m/s" />
        <Tile label="Static pressure" r={chan(snap, 'BARO_P')} dec={2} unit="hPa" />
        <Tile label="Air temperature" r={chan(snap, 'AIR_T')} dec={1} unit="°C" />
        <Tile label="Balloon superpressure" r={chan(snap, 'BLN_DP')} dec={1} unit="Pa" />
      </div>

      <div className="grid min-h-[520px] grid-cols-[380px_minmax(0,1fr)_360px] gap-2">
        <div className="flex flex-col gap-2">
          <div className="h-[340px]">
            {hasAtt ? (
              <ArtificialHorizon
                rollDeg={roll.value as number}
                pitchDeg={pitch.value as number}
                yawDeg={yaw.value as number}
                altitudeM={alt.value ?? Number.NaN}
                speedMps={gs.value ?? Number.NaN}
                verticalSpeedMps={vz.value ?? Number.NaN}
                armedStatus={`ROCKET ${chanText(snap, 'FC_RKT_PHASE') ?? 'UNKNOWN'}`}
                gpsStatus={`POSITION: ${alt.status}`}
                flightPhase={rawPhase ?? snap?.fc_phase ?? 'NO DATA'}
              />
            ) : (
              <div className="panel grid h-full place-items-center text-[12px] text-muted">No attitude telemetry: UNKNOWN</div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Tile label="Tilt (from accel.)" r={chan(snap, 'IMU_TILT')} dec={2} unit="deg" />
            <Tile label="Balloon volume" r={chan(snap, 'BLN_VOL')} dec={0} unit="m³" />
            <Tile label="Propellant (sender)" r={chan(snap, 'PROP_MASS')} dec={1} unit="kg" />
            <Tile label="Total mass (sender)" r={chan(snap, 'TOTAL_MASS')} dec={1} unit="kg" />
          </div>
        </div>

        <div className="min-h-[520px]">
          {hasFix ? (
            <MissionLiveMap vehicleLat={lat.value as number} vehicleLon={lon.value as number} vehicleAltM={alt.value as number} yawDeg={yaw.value ?? 0} homeLat={site!.lat} homeLon={site!.lon} flightPhase={rawPhase ?? undefined} historyTrail={trail} />
          ) : (
            <div className="panel grid h-full place-items-center text-[12px] text-muted">No fused position yet: UNKNOWN</div>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <GatesPanel g={g} snap={snap} />
          <section className="panel" aria-labelledby="ov-stream">
            <div className="panel-head">
              <h2 id="ov-stream" className="panel-title">
                Laptop A twin stream
              </h2>
              <span className={`tag ml-auto ${flt && flt.last_accept_age_s !== null && flt.last_accept_age_s <= 3 ? 'tag-nom' : 'tag-stale'}`}>
                {flt?.source ? `LOCKED ${flt.source}` : 'NOT LOCKED'}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 px-3 py-2 text-[12px]">
              <span className="lbl">TCP socket</span>
              <span className="num">
                {dts ? `${dts.server_status} :${dts.port}` : '---'}
              </span>
              <span className="lbl">accepted</span>
              <span className="num">
                {flt?.accepted ?? 0} · {flt ? flt.rate_hz.toFixed(1) : '0.0'} Hz
              </span>
              <span className="lbl">last accepted</span>
              <span className="num">{flt?.last_accept_age_s == null ? '---' : `${fmtAge(flt.last_accept_age_s)} ago · MET ${fmtMET(flt.last_met_s)}`}</span>
            </div>
            <ul className="border-t border-line px-3 py-1.5 text-[11.5px]" aria-label="Rejected packets">
              {Object.entries(flt?.rejected ?? {}).map(([k, n]) => (
                <li key={k} className="flex justify-between">
                  <span className="text-muted">rejected: {REASONS[k] ?? k}</span>
                  <span className="num">{n}</span>
                </li>
              ))}
              {!Object.keys(flt?.rejected ?? {}).length && <li className="text-muted">no rejected packets</li>}
            </ul>
            <div className="border-t border-line px-3 py-1.5">
              <button type="button" className="btn px-2 py-0.5 text-[11px]" onClick={() => setShowRaw((v) => !v)} disabled={!snap?.digital_twin_telemetry}>
                {showRaw ? 'Hide' : 'Show'} RAW last accepted packet
              </button>
              {showRaw && snap?.digital_twin_telemetry && (
                <pre className="num mt-1 max-h-48 overflow-auto text-[10px] text-muted" aria-label="Raw packet (unprocessed)">
                  {JSON.stringify(snap.digital_twin_telemetry, null, 1)}
                </pre>
              )}
            </div>
          </section>
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
