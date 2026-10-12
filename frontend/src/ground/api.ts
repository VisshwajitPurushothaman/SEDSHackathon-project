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
};

export const wsUrl = (hz = 4) => `${API_URL.replace(/^http/, 'ws')}/ws/telemetry?hz=${hz}`;
