import { useRef, useState } from 'react';
import { ArtificialHorizon } from './ArtificialHorizon';
import { MissionLiveMap } from './MissionLiveMap';
import { type DigitalTwinTelemetry } from './api';
import { useGround } from './store';

// Default baseline data matching user specification
const DEFAULT_TELEMETRY: DigitalTwinTelemetry = {
  event: 'device_handshake',
  device_id: 'client_node_01',
  timestamp_ms: 5406250,
  simulation_state: {
    status: 'RUNNING',
    is_running: true,
    is_paused: false,
    is_resumed: false,
    is_pinging: true,
    last_action: 'STARTED',
    pause_count: 0,
    resume_count: 0,
    last_pause_timestamp_ms: null,
    last_resume_timestamp_ms: null,
    pause_resume_events: [
      {
        action: 'STARTED',
        mission_time_ms: 0,
        iso_time: '2026-10-12T22:58:59.354Z',
      },
      {
        action: 'PING_STARTED',
        mission_time_ms: 0,
        iso_time: '2026-10-12T22:58:59.354Z',
      },
    ],
  },
  tcp_socket_stream: {
    server_ip: '172.16.59.95',
    port: 65432,
    ping_interval_seconds: 0.5,
    is_pinging: true,
    protocol: '4-byte Big-Endian uint32 (!I) length header + UTF-8 JSON',
    packets_dispatched: 8048,
    socket_status: 'PINGING_0.5S',
    last_server_ack: {
      status: 'success',
      received_bytes: 4626,
    },
  },
  flight_phase: 'PRELAUNCH',
  staging_sub_phase: 'ATTACHED_TO_GONDOLA',
  gnss_coordinates: {
    origin_reference: 'LAUNCH_PAD_ORIGIN [0, 0, 0]',
    position_from_origin_xyz_m: [0, 0, 0],
    x_downrange_m: 0,
    y_altitude_m: 0,
    z_crossrange_m: 0,
    slant_range_3d_m: 0,
    geodetic_wgs84: {
      lat_deg: 10.3015,
      lon_deg: 79.8512,
      alt_m: 0,
    },
    status: 'NOMINAL',
  },
  orbital_and_aerothermal_physics: {
    specific_orbital_energy_mj_kg: -62.56,
    specific_angular_momentum_m2_s: 0,
    eccentricity: 1,
    semi_major_axis_km: 0,
    apoapsis_km: 0,
    periapsis_km: 0,
    remaining_delta_v_mps: 10219.8,
    dynamic_pressure_q_pa: 0,
    max_q_pa: 0,
    aerodynamic_heat_flux_wm2: 0,
    mach_number: 0,
    membrane_hoop_stress_mpa: 46.76,
    differential_superpressure_pa: 142.25,
  },
  barometric_sensor: {
    pressure_hpa: 1013.25,
    derived_altitude_m: 0,
    status: 'NOMINAL',
  },
  imu_sensor: {
    accel_mps2: [-0.011, 0.002, 9.8],
    gyro_dps: [0.006, 0.009, 0.009],
    attitude_rpy_deg: [0, 0, 0],
    status: 'NOMINAL',
  },
  environmental_sensors: {
    ambient_temp_c: 15.0,
    relative_humidity_pct: 65.0,
    effective_wind_vector_mps: [0.25, 0, -1.91],
  },
  payload_state: {
    is_attached: true,
    balloon_volume_m3: 4017,
    thrust_n: 0,
  },
};

type MissionTab = 'quick' | 'gauges' | 'status' | 'servo' | 'balloon' | 'logs';

export function OverviewPage() {
  const snap = useGround((s) => s.snap);
  const [activeTab, setActiveTab] = useState<MissionTab>('quick');
  const [copied, setCopied] = useState(false);

  // Sticky latch ref: never fluctuate back to 0/default between pings
  const lastValidRef = useRef<DigitalTwinTelemetry>(DEFAULT_TELEMETRY);
  const rawDt = snap?.digital_twin_telemetry;
  if (rawDt) {
    const rawAlt = rawDt.gnss_coordinates?.geodetic_wgs84?.alt_m ?? 0;
    const prevAlt = lastValidRef.current.gnss_coordinates?.geodetic_wgs84?.alt_m ?? 0;
    if (rawAlt > 0 || prevAlt === 0) {
      lastValidRef.current = rawDt;
    } else {
      // Retain gained flight coordinates and merge stream updates
      lastValidRef.current = {
        ...lastValidRef.current,
        timestamp_ms: rawDt.timestamp_ms || lastValidRef.current.timestamp_ms,
        ping_paused: rawDt.ping_paused,
        is_prediction_active: rawDt.is_prediction_active,
        simulation_state: rawDt.simulation_state ?? lastValidRef.current.simulation_state,
        tcp_socket_stream: rawDt.tcp_socket_stream ?? lastValidRef.current.tcp_socket_stream,
      };
    }
  }

  const dt: DigitalTwinTelemetry = lastValidRef.current;
  const dtStatus = snap?.digital_twin;

  // Detect ping_paused (if 1 or true, prompt user that stream is paused & showing prediction values)
  const isPaused =
    dt.ping_paused === 1 ||
    dt.ping_paused === true ||
    dt.ping_paused === '1' ||
    dt.is_prediction_active === true ||
    dt.simulation_state?.is_paused === true;

  // Safe property extraction
  const imu = dt.imu_sensor ?? {
    accel_mps2: [0, 0, 0],
    gyro_dps: [0, 0, 0],
    attitude_rpy_deg: [0, 0, 0],
    status: 'NOMINAL',
  };
  const roll = imu.attitude_rpy_deg?.[0] ?? 0;
  const pitch = imu.attitude_rpy_deg?.[1] ?? 0;
  const yaw = imu.attitude_rpy_deg?.[2] ?? 0;

  const gnss = dt.gnss_coordinates ?? {
    origin_reference: 'LAUNCH_PAD_ORIGIN',
    position_from_origin_xyz_m: [0, 0, 0],
    x_downrange_m: 0,
    y_altitude_m: 0,
    z_crossrange_m: 0,
    slant_range_3d_m: 0,
    geodetic_wgs84: { lat_deg: 10.3015, lon_deg: 79.8512, alt_m: 0 },
    status: 'NOMINAL',
  };

  const baro = dt.barometric_sensor ?? {
    pressure_hpa: 1013.25,
    derived_altitude_m: 0,
    status: 'NOMINAL',
  };

  const env = dt.environmental_sensors ?? {
    ambient_temp_c: 15,
    relative_humidity_pct: 50,
    effective_wind_vector_mps: [0, 0, 0],
  };

  const payload = dt.payload_state ?? {
    is_attached: true,
    balloon_volume_m3: 4017,
    thrust_n: 0,
  };

  // Speed and altitude
  const altitude = gnss.geodetic_wgs84?.alt_m || baro.derived_altitude_m || 0;
  const downrangeM = Math.abs(gnss.x_downrange_m || 0);
  const slantRangeM = gnss.slant_range_3d_m || downrangeM || 0;

  // Vertical velocity from wind or derived
  const vSpeedMps = env.effective_wind_vector_mps?.[1] ?? 0;
  // Ground speed
  const gSpeedMps = Math.hypot(
    env.effective_wind_vector_mps?.[0] ?? 0,
    env.effective_wind_vector_mps?.[2] ?? 0,
  );

  const isConnected = dtStatus?.server_status === 'CONNECTED' || (dtStatus?.packets_received ?? 0) > 0;
  const flightPhase = dt.flight_phase ?? 'PRELAUNCH';
  const isAttached = payload.is_attached ?? true;

  const handleCopyJson = () => {
    void navigator.clipboard.writeText(JSON.stringify(dt, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex h-full min-h-[calc(100vh-56px)] flex-col bg-[#05080f] text-white">
      {/* ------------------- MISSION PLANNER TOP CONTROL BAR ------------------- */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-[#222e42] bg-[#0c1422] px-3 font-mono text-[12px]">
        {/* Left: Mission Planner Toolbar Icons (Config/Tuning removed per instruction) */}
        <div className="flex items-center gap-1">
          {[
            { id: 'flight_data', label: 'FLIGHT DATA', icon: '✈️', active: true, href: '#/overview' },
            { id: 'flight_plan', label: 'FLIGHT PLAN', icon: '🌐', href: '#/trajectory' },
            { id: 'initial_setup', label: 'INITIAL SETUP', icon: '⚙️', href: '#/preflight' },
            { id: 'post_flight', label: 'POST-FLIGHT', icon: '📋', href: '#/postflight' },
            { id: 'terminal', label: 'TERMINAL', icon: '💻', action: () => setActiveTab('logs') },
          ].map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={item.action ?? (() => { if (item.href) window.location.hash = item.href; })}
              className={`flex flex-col items-center justify-center px-2.5 py-1 rounded transition-colors text-[9.5px] font-bold ${
                item.active
                  ? 'bg-[#1a2942] text-[#5cd6ff] border border-[#2a3f66]'
                  : 'text-[#9cb2cf] hover:bg-[#121c2d] hover:text-white'
              }`}
            >
              <span className="text-[14px] leading-tight">{item.icon}</span>
              <span className="tracking-wider mt-0.5">{item.label}</span>
            </button>
          ))}
        </div>

        {/* Center: Phase & Gondola status badge with Prediction indicator */}
        <div className="hidden items-center gap-2 xl:flex">
          <span
            className={`rounded border px-2.5 py-1 font-bold text-[11px] ${
              isPaused
                ? 'border-[#f5c542]/60 bg-[#f5c542]/15 text-[#f5c542]'
                : 'border-[#3fd68a]/40 bg-[#3fd68a]/10 text-[#3fd68a]'
            }`}
          >
            PHASE: {isPaused ? 'PAUSED (PREDICTION ACTIVE)' : flightPhase}
            {isPaused && <span className="tag tag-pred ml-2 text-[10px]">PRED</span>}
          </span>
          <span className="rounded border border-[#f5c542]/40 bg-[#f5c542]/10 px-2.5 py-1 font-bold text-[#f5c542] text-[11px]">
            GONDOLA: {isAttached ? 'LOCKED' : 'RELEASED'}
          </span>
        </div>

        {/* Right: COM / TCP Connection dropdowns & Connect button (Mission Planner style) */}
        <div className="flex items-center gap-2">
          {/* Port Dropdown */}
          <div className="flex flex-col">
            <span className="text-[8.5px] text-muted font-sans uppercase">Link Port</span>
            <select
              aria-label="Link Port"
              className="rounded border border-[#222e42] bg-[#070b12] px-2 py-0.5 text-[11px] font-bold text-[#5cd6ff] outline-none"
              defaultValue="TCP65432"
            >
              <option value="TCP65432">TCP 65432 (LIVE)</option>
              <option value="COM32">COM32 (UART)</option>
              <option value="UDP5005">UDP 5005</option>
            </select>
          </div>

          {/* Baud Rate / Ping Rate */}
          <div className="flex flex-col">
            <span className="text-[8.5px] text-muted font-sans uppercase">Baud / Rate</span>
            <select
              aria-label="Baud / Rate"
              className="rounded border border-[#222e42] bg-[#070b12] px-2 py-0.5 text-[11px] font-bold text-white outline-none"
              defaultValue="57600"
            >
              <option value="57600">57600 / 0.5s</option>
              <option value="115200">115200 / 10Hz</option>
              <option value="9600">9600</option>
            </select>
          </div>

          {/* Connect / Streaming Plug Button */}
          <button
            type="button"
            className={`flex items-center gap-1.5 rounded px-3 py-1.5 font-bold text-[11.5px] shadow transition-all ${
              isConnected
                ? 'bg-[#1b5e20] text-[#a5d6a7] border border-[#2e7d32] hover:bg-[#256e2b]'
                : 'bg-[#b71c1c] text-[#ffcdd2] border border-[#c62828] hover:bg-[#c62828]'
            }`}
          >
            <span className="text-[13px]">{isConnected ? '🔌' : '⚡'}</span>
            <span>{isConnected ? 'CONNECT' : 'DISCONNECT'}</span>
          </button>

          <button
            type="button"
            onClick={handleCopyJson}
            className="rounded border border-[#2a3649] bg-[#121c2d] px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-[#1a2942]"
            title="Copy current telemetry JSON"
          >
            {copied ? 'Copied ✓' : 'JSON'}
          </button>
        </div>
      </header>

      {/* ------------------- PROMPT BANNER WHEN PING PAUSED (PREDICTION ACTIVE) ------------------- */}
      {isPaused && (
        <div className="flex items-center justify-between border-b border-[#f5c542]/40 bg-[#f5c542]/15 px-4 py-2 font-mono text-[12px] text-[#f5c542] shadow-sm animate-pulse">
          <div className="flex items-center gap-2">
            <span className="text-[14px]">⚠️</span>
            <span className="font-bold tracking-wide">
              TELEMETRY STREAM PAUSED: FLIGHT SYSTEM RUNNING ON FUSED KALMAN PREDICTION ALGORITHM — DISPLAYING PROPAGATED TELEMETRY
            </span>
          </div>
          <span className="rounded bg-[#f5c542]/25 px-2 py-0.5 text-[10.5px] font-extrabold text-[#f5c542] border border-[#f5c542]/50">
            PREDICTION ACTIVE
          </span>
        </div>
      )}

      {/* ------------------- MAIN SPLIT INTERFACE ------------------- */}
      <main className="grid flex-1 grid-cols-1 gap-2 p-2 lg:grid-cols-[460px_minmax(0,1fr)] xl:grid-cols-[480px_minmax(0,1fr)]">
        {/* ================= LEFT COLUMN: PFD + HUD TABS ================= */}
        <div className="flex flex-col gap-2 min-h-0">
          {/* 1. PRIMARY FLIGHT DISPLAY (ARTIFICIAL HORIZON / GYROSCOPIC HUD) */}
          <div className="h-[340px] shrink-0">
            <ArtificialHorizon
              rollDeg={roll}
              pitchDeg={pitch}
              yawDeg={yaw}
              altitudeM={altitude}
              speedMps={gSpeedMps}
              verticalSpeedMps={vSpeedMps}
              armedStatus={
                isPaused
                  ? 'PREDICTION ACTIVE'
                  : isAttached
                  ? 'GONDOLA LOCKED'
                  : 'ROCKET RELEASED'
              }
              gpsStatus={gnss.status === 'NOMINAL' ? 'GPS: 3D FIX (NOMINAL)' : 'GPS: NO FIX'}
              flightPhase={isPaused ? 'PREDICTION ACTIVE' : flightPhase}
            />
          </div>

          {/* 2. TABBED QUICK TELEMETRY PANEL (MISSION PLANNER STYLE) */}
          <div className="flex flex-1 flex-col rounded border border-[#222e42] bg-[#0c1422] shadow">
            {/* Tabs Header */}
            <div className="flex flex-wrap items-center border-b border-[#222e42] bg-[#080d16] px-1 text-[11.5px] font-bold">
              {[
                { id: 'quick', label: 'Quick' },
                { id: 'gauges', label: 'Gauges' },
                { id: 'status', label: 'Status' },
                { id: 'servo', label: 'Servo' },
                { id: 'balloon', label: 'Balloon & Payload' },
                { id: 'logs', label: 'TCP Logs' },
              ].map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setActiveTab(t.id as MissionTab)}
                  className={`border-b-2 px-3 py-1.5 uppercase tracking-wide transition-colors ${
                    activeTab === t.id
                      ? 'border-[#5cd6ff] bg-[#101a2b] text-[#5cd6ff]'
                      : 'border-transparent text-muted hover:text-white'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {/* Tab Contents */}
            <div className="flex-1 p-3 overflow-auto">
              {/* TAB 1: QUICK TELEMETRY HUD (6 LARGE VIBRANT READOUTS) */}
              {activeTab === 'quick' && (
                <div className="grid h-full grid-cols-2 gap-3">
                  {/* Altitude (Meters) - Violet */}
                  <div className="flex flex-col justify-center rounded border border-[#30234a] bg-[#140e24] p-3 shadow-inner">
                    <span className="font-sans text-[11px] font-semibold uppercase tracking-wider text-[#b388ff]/80">
                      Altitude (Meters)
                    </span>
                    <span className="num mt-1 text-[32px] font-bold leading-none text-[#b388ff] drop-shadow-[0_2px_8px_rgba(179,136,255,0.4)]">
                      {altitude.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                    <span className="text-[11px] text-[#b388ff]/60 mt-1">
                      Baro: {(baro.pressure_hpa ?? 0).toFixed(1)} hPa
                    </span>
                  </div>

                  {/* GroundSpeed (m/s) - Orange */}
                  <div className="flex flex-col justify-center rounded border border-[#4d2f09] bg-[#211403] p-3 shadow-inner">
                    <span className="font-sans text-[11px] font-semibold uppercase tracking-wider text-[#ff9800]/80">
                      GroundSpeed (m/s)
                    </span>
                    <span className="num mt-1 text-[32px] font-bold leading-none text-[#ff9800] drop-shadow-[0_2px_8px_rgba(255,152,0,0.4)]">
                      {gSpeedMps.toFixed(2)}
                    </span>
                    <span className="text-[11px] text-[#ff9800]/60 mt-1">
                      Horiz wind drift
                    </span>
                  </div>

                  {/* Dist to Launch / Downrange - Coral / Red */}
                  <div className="flex flex-col justify-center rounded border border-[#4d1616] bg-[#210909] p-3 shadow-inner">
                    <span className="font-sans text-[11px] font-semibold uppercase tracking-wider text-[#ff5252]/80">
                      Downrange (X) (Meters)
                    </span>
                    <span className="num mt-1 text-[32px] font-bold leading-none text-[#ff5252] drop-shadow-[0_2px_8px_rgba(255,82,82,0.4)]">
                      {downrangeM.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                    </span>
                    <span className="text-[11px] text-[#ff5252]/60 mt-1">
                      Crossrange: {Math.round(gnss.z_crossrange_m || 0).toLocaleString()} m
                    </span>
                  </div>

                  {/* Yaw (deg) - Bright Green */}
                  <div className="flex flex-col justify-center rounded border border-[#0d3b19] bg-[#051c0a] p-3 shadow-inner">
                    <span className="font-sans text-[11px] font-semibold uppercase tracking-wider text-[#00e676]/80">
                      Yaw (deg)
                    </span>
                    <span className="num mt-1 text-[32px] font-bold leading-none text-[#00e676] drop-shadow-[0_2px_8px_rgba(0,230,118,0.4)]">
                      {yaw.toFixed(2)}
                    </span>
                    <span className="text-[11px] text-[#00e676]/60 mt-1">
                      Pitch: {pitch.toFixed(1)}° | Roll: {roll.toFixed(1)}°
                    </span>
                  </div>

                  {/* Vertical Speed (m/s) - Yellow */}
                  <div className="flex flex-col justify-center rounded border border-[#4d4409] bg-[#211d03] p-3 shadow-inner">
                    <span className="font-sans text-[11px] font-semibold uppercase tracking-wider text-[#ffeb3b]/80">
                      Vertical Speed (m/s)
                    </span>
                    <span className="num mt-1 text-[32px] font-bold leading-none text-[#ffeb3b] drop-shadow-[0_2px_8px_rgba(255,235,59,0.4)]">
                      {vSpeedMps >= 0 ? `+${vSpeedMps.toFixed(2)}` : vSpeedMps.toFixed(2)}
                    </span>
                    <span className="text-[11px] text-[#ffeb3b]/60 mt-1">
                      Stratospheric ascent
                    </span>
                  </div>

                  {/* DistToMAV / Slant Range - Cyan */}
                  <div className="flex flex-col justify-center rounded border border-[#0a3845] bg-[#041a21] p-3 shadow-inner">
                    <span className="font-sans text-[11px] font-semibold uppercase tracking-wider text-[#00e5ff]/80">
                      Slant Range (Meters)
                    </span>
                    <span className="num mt-1 text-[32px] font-bold leading-none text-[#00e5ff] drop-shadow-[0_2px_8px_rgba(0,229,255,0.4)]">
                      {slantRangeM.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                    </span>
                    <span className="text-[11px] text-[#00e5ff]/60 mt-1">
                      Direct line of sight
                    </span>
                  </div>
                </div>
              )}

              {/* TAB 3: GAUGES */}
              {activeTab === 'gauges' && (
                <div className="grid grid-cols-3 gap-2 text-center text-[12px]">
                  {/* Gauge 1: Altitude */}
                  <div className="rounded border border-[#222e42] bg-[#080d16] p-2 flex flex-col items-center">
                    <span className="lbl mb-2">Altitude</span>
                    <div className="relative flex h-20 w-20 items-center justify-center rounded-full border-4 border-[#b388ff] bg-black">
                      <span className="num text-[14px] font-bold text-[#b388ff]">
                        {(altitude / 1000).toFixed(1)}k
                      </span>
                    </div>
                    <span className="text-[10.5px] text-muted mt-1">meters</span>
                  </div>

                  {/* Gauge 2: Heading */}
                  <div className="rounded border border-[#222e42] bg-[#080d16] p-2 flex flex-col items-center">
                    <span className="lbl mb-2">Heading</span>
                    <div className="relative flex h-20 w-20 items-center justify-center rounded-full border-4 border-[#00e676] bg-black">
                      <span className="num text-[14px] font-bold text-[#00e676]">
                        {Math.round(yaw)}°
                      </span>
                    </div>
                    <span className="text-[10.5px] text-muted mt-1">compass</span>
                  </div>

                  {/* Gauge 3: Pressure */}
                  <div className="rounded border border-[#222e42] bg-[#080d16] p-2 flex flex-col items-center">
                    <span className="lbl mb-2">Pressure</span>
                    <div className="relative flex h-20 w-20 items-center justify-center rounded-full border-4 border-[#ffeb3b] bg-black">
                      <span className="num text-[14px] font-bold text-[#ffeb3b]">
                        {(baro.pressure_hpa ?? 0).toFixed(0)}
                      </span>
                    </div>
                    <span className="text-[10.5px] text-muted mt-1">hPa</span>
                  </div>
                </div>
              )}

              {/* TAB 4: STATUS (IMU & ENVIRONMENT) */}
              {activeTab === 'status' && (
                <div className="flex flex-col gap-2 text-[12px]">
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded border border-[#222e42] bg-[#070b12] p-2">
                      <span className="lbl block mb-1">IMU Accelerometer (m/s²)</span>
                      <span className="font-mono text-[13px] text-white">
                        X: {(imu.accel_mps2?.[0] ?? 0).toFixed(3)} | Y: {(imu.accel_mps2?.[1] ?? 0).toFixed(3)} | Z: {(imu.accel_mps2?.[2] ?? 0).toFixed(3)}
                      </span>
                    </div>
                    <div className="rounded border border-[#222e42] bg-[#070b12] p-2">
                      <span className="lbl block mb-1">IMU Gyroscope (°/s)</span>
                      <span className="font-mono text-[13px] text-white">
                        X: {(imu.gyro_dps?.[0] ?? 0).toFixed(3)} | Y: {(imu.gyro_dps?.[1] ?? 0).toFixed(3)} | Z: {(imu.gyro_dps?.[2] ?? 0).toFixed(3)}
                      </span>
                    </div>
                  </div>

                  <div className="rounded border border-[#222e42] bg-[#070b12] p-2.5">
                    <span className="lbl block mb-1">Environmental Array</span>
                    <div className="grid grid-cols-3 gap-2 text-[11.5px]">
                      <div>
                        <span className="text-muted">Temp: </span>
                        <span className="font-bold text-white">{(env.ambient_temp_c ?? 0).toFixed(1)} °C</span>
                      </div>
                      <div>
                        <span className="text-muted">Humidity: </span>
                        <span className="font-bold text-white">{(env.relative_humidity_pct ?? 0).toFixed(1)} %</span>
                      </div>
                      <div>
                        <span className="text-muted">Wind: </span>
                        <span className="font-mono font-bold text-white">[{env.effective_wind_vector_mps?.join(', ')}] m/s</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB: SERVO & PYRO ACTUATION (MISSION PLANNER STYLE) */}
              {activeTab === 'servo' && (
                <div className="flex flex-col gap-2.5 text-[12px]">
                  <div className="rounded border border-[#222e42] bg-[#070b12] p-2.5">
                    <span className="lbl block mb-2">SBRL Staging & Release Servos</span>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="flex items-center justify-between rounded bg-[#0d1420] px-3 py-2 border border-[#1e2a3d]">
                        <span className="text-white font-medium">Gondola Release Clamp:</span>
                        <span className={`tag ${isAttached ? 'tag-nom' : 'tag-alm'}`}>
                          {isAttached ? 'LATCHED (SERVO 1)' : 'RELEASED'}
                        </span>
                      </div>
                      <div className="flex items-center justify-between rounded bg-[#0d1420] px-3 py-2 border border-[#1e2a3d]">
                        <span className="text-white font-medium">Pyro Ejection Pin:</span>
                        <span className="tag tag-cau">ARMED (CIRCUIT 1)</span>
                      </div>
                      <div className="flex items-center justify-between rounded bg-[#0d1420] px-3 py-2 border border-[#1e2a3d]">
                        <span className="text-white font-medium">Balloon Vent Valve:</span>
                        <span className="tag tag-nom">CLOSED (0% VENT)</span>
                      </div>
                      <div className="flex items-center justify-between rounded bg-[#0d1420] px-3 py-2 border border-[#1e2a3d]">
                        <span className="text-white font-medium">Ignition Interlock:</span>
                        <span className="tag tag-pred">
                          {altitude >= 18000 ? 'READY FOR IGNITION' : 'INTERLOCKED (<18km)'}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 5: BALLOON & PAYLOAD */}
              {activeTab === 'balloon' && (
                <div className="flex flex-col gap-2 text-[12px]">
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded border border-[#222e42] bg-[#070b12] p-2.5">
                      <span className="lbl block">Balloon Gas Volume</span>
                      <span className="font-mono text-[20px] font-bold text-[#5cd6ff]">
                        {(payload.balloon_volume_m3 ?? 0).toLocaleString()} <span className="text-xs text-muted">m³</span>
                      </span>
                    </div>
                    <div className="rounded border border-[#222e42] bg-[#070b12] p-2.5">
                      <span className="lbl block">Engine Thrust</span>
                      <span className="font-mono text-[20px] font-bold text-white">
                        {payload.thrust_n ?? 0} <span className="text-xs text-muted">N</span>
                      </span>
                    </div>
                  </div>

                  <div className="rounded border border-[#222e42] bg-[#070b12] p-2.5">
                    <span className="lbl block mb-1.5">Mass Budget & Propellant</span>
                    <div className="grid grid-cols-2 gap-2 text-[11.5px]">
                      <div>
                        <span className="text-muted">Propellant Mass: </span>
                        <span className="font-bold text-white">{(payload as any).propellant_kg ?? 2000} kg</span>
                      </div>
                      <div>
                        <span className="text-muted">Total Gross Mass: </span>
                        <span className="font-bold text-white">{(payload as any).total_mass_kg ?? 3670} kg</span>
                      </div>
                      <div>
                        <span className="text-muted">Staging Sub-Phase: </span>
                        <span className="font-bold text-[#3fd68a]">{dt.staging_sub_phase ?? 'ATTACHED'}</span>
                      </div>
                      <div>
                        <span className="text-muted">Burst Risk Margin: </span>
                        <span className="font-bold text-[#3fd68a]">NOMINAL (NO BURST)</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 6: TCP STREAM LOG */}
              {activeTab === 'logs' && (
                <div className="flex flex-col gap-1.5 text-[11.5px]">
                  <div className="flex justify-between text-muted text-[11px] pb-1 border-b border-[#222e42]">
                    <span>TCP Socket: {dtStatus?.host ?? '0.0.0.0'}:{dtStatus?.port ?? 65432}</span>
                    <span>Client: {dtStatus?.connected_client ?? '172.16.58.215'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Packets Received:</span>
                    <span className="font-bold text-[#3fd68a]">{dtStatus?.packets_received ?? 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Bytes Transferred:</span>
                    <span className="font-bold text-white">{((dtStatus?.bytes_received ?? 0) / 1024).toFixed(1)} KB</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Last Server ACK:</span>
                    <span className="font-mono text-[#5cd6ff]">{dtStatus?.last_payload_len ?? 0} bytes acked</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Age of Last Packet:</span>
                    <span className="font-bold text-[#ffeb3b]">{dtStatus?.last_frame_age_s ?? 0} s</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ================= RIGHT COLUMN: LIVE LOCATION MAP ================= */}
        <div className="h-full min-h-[500px]">
          <MissionLiveMap
            vehicleLat={gnss.geodetic_wgs84?.lat_deg ?? 37.1}
            vehicleLon={gnss.geodetic_wgs84?.lon_deg ?? -6.733}
            vehicleAltM={altitude}
            yawDeg={yaw}
            homeLat={37.1}
            homeLon={-6.733}
            slantRangeM={slantRangeM}
            flightPhase={flightPhase}
          />
        </div>
      </main>
    </div>
  );
}
