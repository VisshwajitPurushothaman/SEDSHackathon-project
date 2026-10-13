/** Ground-station API (docs/API.md, "Ground station"). Read-only telemetry plus operator records. */
import { API_URL } from '../api/client';

export type EstStatus = 'MEASURED' | 'FUSED' | 'PREDICTED' | 'RECONSTRUCTED' | 'UNKNOWN';
export type ChanStatus = 'MEASURED' | 'STALE' | 'UNKNOWN';
export type LimitState = 'NOMINAL' | 'CAUTION' | 'ALARM' | 'NO_LIMIT';
export type Band = [number | null, number | null];

export interface Limits {
  yellow?: Band;
  red?: Band;
  basis?: string;
}

export interface ChannelRow {
  mnemonic: string;
  value: number | string | boolean | null;
  units: string;
  subsystem: string;
  description: string;
  decimals: number | null;
  status: ChanStatus;
  age_s: number | null;
  met: number | null;
  limit: LimitState;
  limits: Limits | null;
  known: boolean;
}

export interface LosPeriod {
  start_utc: number;
  end_utc: number | null;
  start_met: number;
  end_met: number | null;
}

export interface Link {
  aos: boolean;
  last_frame_age_s?: number | null;
  rate_hz?: number;
  frames_received?: number;
  max_seq?: number;
  loss_pct?: number;
  latency_s?: number | null;
  clock_offset_s?: number | null;
  latency_note?: string;
  duplicates?: number;
  out_of_order?: number;
  hmac_failures?: number;
  chain_breaks?: number;
  gaps?: [number, number][];
  los_periods?: LosPeriod[];
  time_scale?: number;
}

export interface Estimate {
  value: unknown;
  unit?: string;
  status: EstStatus;
  sigma?: number | null;
  source?: string;
  age_s?: number | null;
  last_value?: unknown;
}

export interface Gate {
  id: string;
  passed: boolean | null;
  value: unknown;
  limit: unknown;
  margin: number | null;
  detail: string;
  evidence?: Record<string, unknown>;
}

export interface Trust {
  state: string;
  [k: string]: unknown;
}

export interface Region {
  level: number;
  polygon: [number, number][];
}

export interface GroundFrame {
  t: number;
  phase: string;
  vehicle_phase: string;
  rocket_phase: string;
  estimates: Record<string, Estimate>;
  gates: Record<string, Gate>;
  tank_criterion: { passed: boolean | null; detail: string };
  recommendation: 'GO_RECOMMENDED' | 'NO_GO' | 'ABORT_RECOMMENDED';
  predicting: boolean;
  last_fix_t: number | null;
  trust: Record<string, Trust>;
  landing_footprint: { basis: string; regions: Region[] } | null;
  atmo_source: string;
}

export interface Alarm {
  id: number;
  key: string;
  severity: 'INFO' | 'CAUTION' | 'WARNING';
  title: string;
  evidence: Record<string, unknown>;
  source: string;
  utc: number | null;
  met: number;
  acknowledged_by: string | null;
  acknowledged_utc: number | null;
}

export interface Poll {
  n: number;
  operator: string;
  vote: 'GO' | 'NO_GO';
  note: string;
  utc: number;
  met: number;
  recommendation: string;
  gates: Record<string, boolean | null>;
}

export interface DigitalTwinTelemetry {
  event: string;
  device_id: string;
  timestamp_ms: number;
  ping_paused?: number | boolean | string;
  is_prediction_active?: boolean;
  simulation_state: {
    status: string;
    is_running: boolean;
    is_paused: boolean;
    is_resumed: boolean;
    is_pinging: boolean;
    last_action: string;
    pause_count: number;
    resume_count: number;
    last_pause_timestamp_ms: number | null;
    last_resume_timestamp_ms: number | null;
    pause_resume_events: Array<{
      action: string;
      mission_time_ms: number;
      iso_time: string;
    }>;
  };
  tcp_socket_stream: {
    server_ip: string;
    port: number;
    ping_interval_seconds: number;
    is_pinging: boolean;
    protocol: string;
    packets_dispatched: number;
    socket_status: string;
    last_server_ack: {
      status: string;
      received_bytes: number;
    };
  };
  flight_phase: string;
  staging_sub_phase: string;
  gnss_coordinates: {
    origin_reference: string;
    position_from_origin_xyz_m: [number, number, number];
    x_downrange_m: number;
    y_altitude_m: number;
    z_crossrange_m: number;
    slant_range_3d_m: number;
    geodetic_wgs84: {
      lat_deg: number;
      lon_deg: number;
      alt_m: number;
    };
    status: string;
  };
  orbital_and_aerothermal_physics: {
    specific_orbital_energy_mj_kg: number;
    specific_angular_momentum_m2_s: number;
    eccentricity: number;
    semi_major_axis_km: number;
    apoapsis_km: number;
    periapsis_km: number;
    remaining_delta_v_mps: number;
    dynamic_pressure_q_pa: number;
    max_q_pa: number;
    aerodynamic_heat_flux_wm2: number;
    mach_number: number;
    membrane_hoop_stress_mpa: number;
    differential_superpressure_pa: number;
  };
  barometric_sensor: {
    pressure_hpa: number;
    derived_altitude_m: number;
    status: string;
  };
  imu_sensor: {
    accel_mps2: [number, number, number];
    gyro_dps: [number, number, number];
    attitude_rpy_deg: [number, number, number];
    status: string;
  };
  environmental_sensors: {
    ambient_temp_c: number;
    relative_humidity_pct: number;
    effective_wind_vector_mps: [number, number, number];
  };
  payload_state: {
    is_attached: boolean;
    balloon_volume_m3: number;
    thrust_n: number;
  };
}

export interface DigitalTwinStatus {
  server_status: string;
  host: string;
  port: number;
  active_clients: number;
  connected_client: string | null;
  packets_received: number;
  bytes_received: number;
  last_received_utc: number | null;
  last_frame_age_s: number | null;
  last_payload_len: number | null;
  last_ack: { status: string; received_bytes: number } | null;
  has_received_live: boolean;
}

export interface Snapshot {
  receiver: { mode: string; listen: string | null; rejected_datagrams: number; playback_file: string | null; playback_done: boolean };
  mission_id: string | null;
  source: string;
  recording?: boolean;
  session_file?: string | null;
  utc?: number;
  met?: number;
  met_status?: EstStatus;
  fc_phase?: string;
  link: Link;
  channels?: ChannelRow[];
  ground?: GroundFrame | null;
  alarms?: Alarm[];
  active_alarms?: number;
  polls?: Poll[];
  digital_twin?: DigitalTwinStatus;
  digital_twin_telemetry?: DigitalTwinTelemetry;
}

export interface HistoryRow {
  met: number;
  phase: string;
  rec: string;
  fused_altitude?: number;
  fused_altitude_sigma?: number | null;
  fused_vertical_speed?: number;
  fused_vertical_speed_sigma?: number | null;
  [mnemonic: string]: number | string | null | undefined;
}

export interface TrackPoint {
  met: number;
  lat: number;
  lon: number;
  alt: number | null;
  status: EstStatus;
}

export interface History {
  mission_id: string;
  rows: HistoryRow[];
  track: TrackPoint[];
  los_periods: LosPeriod[];
}

export interface EventEntry {
  n: number;
  utc: number | null;
  met: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      detail = ((await r.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await r.json()) as T;
}

const post = (path: string, body: unknown) =>
  fetch(`${API_URL}${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

export const groundApi = {
  history: (since?: number) => fetch(`${API_URL}/api/ground/history${since === undefined ? '' : `?since=${since}`}`).then((r) => json<History>(r)),
  events: () => fetch(`${API_URL}/api/ground/events`).then((r) => json<EventEntry[]>(r)),
  ack: (id: number, operator: string) => post(`/api/ground/alarms/${id}/ack`, { operator }).then((r) => json<Alarm>(r)),
  poll: (operator: string, vote: 'GO' | 'NO_GO', note: string) => post('/api/ground/poll', { operator, vote, note }).then((r) => json<Poll>(r)),
  digitalTwin: () => fetch(`${API_URL}/api/ground/digital-twin`).then((r) => json<{ status: DigitalTwinStatus; telemetry: DigitalTwinTelemetry }>(r)),
  injectDigitalTwin: (payload: Partial<DigitalTwinTelemetry>) => post('/api/ground/digital-twin/inject', payload).then((r) => json<DigitalTwinTelemetry>(r)),
  resetDigitalTwin: () => post('/api/ground/digital-twin/reset', {}).then((r) => json<DigitalTwinTelemetry>(r)),
};

export const wsUrl = (hz = 4) => `${API_URL.replace(/^http/, 'ws')}/ws/telemetry?hz=${hz}`;
