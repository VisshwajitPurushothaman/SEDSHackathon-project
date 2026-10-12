/** Client for /api/missions/* (see docs/API.md). Types mirror aerofire/ground/station.py frames. */
import { API_URL } from '../api/client';

export type EstStatus = 'MEASURED' | 'FUSED' | 'PREDICTED' | 'RECONSTRUCTED' | 'UNKNOWN';

export interface Estimate<T = number> {
  value: T | null;
  unit: string;
  status: EstStatus;
  sigma: number | null;
  source: string;
  age_s: number | null;
  last_value?: T;
  ci95?: [number, number];
}

export interface Region {
  level: 50 | 90 | 99;
  polygon: [number, number][]; // [lat, lon]
  semi_axes_m: number[];
}

export interface Prediction {
  t: number;
  since_last_fix_s: number | null;
  mean: { east: number; north: number; up: number; lat: number; lon: number };
  sigma_h_m: number;
  sigma_v_m: number;
  regions: Region[];
  status: 'PREDICTED';
}

export interface Gate {
  id: string;
  name: string;
  passed: boolean | null;
  value: unknown;
  limit: unknown;
  margin: number | null;
  unit: string;
  detail: string;
  evidence: Record<string, unknown>;
}

export interface Alert {
  id: number;
  t: number;
  severity: 'INFO' | 'CAUTION' | 'WARNING';
  key: string;
  title: string;
  evidence: Record<string, unknown>;
  acknowledged_by: string | null;
  acknowledged_at: number | null;
}

export interface ImpactPoint {
  case: string;
  lat: number;
  lon: number;
  inside: boolean;
  range_m: number;
  apogee_m: number;
}

export interface Footprint {
  t: number;
  basis: string;
  mean: { lat: number; lon: number; east: number; north: number };
  regions: Region[];
  rocket_aboard: boolean;
}

export type Recommendation = 'GO_RECOMMENDED' | 'NO_GO' | 'ABORT_RECOMMENDED';

export interface GroundFrame {
  id: number;
  mission_id: string;
  state: string;
  t: number;
  phase: string;
  phase_index: number;
  vehicle_phase: string;
  rocket_phase: string;
  estimates: Record<string, Estimate<any>> & { prediction?: Prediction };
  gates: Record<'float' | 'nav' | 'motor' | 'swing' | 'impact', Gate>;
  tank_criterion: Gate;
  recommendation: Recommendation;
  operator: { state: 'none' | 'held' | 'committed' | 'terminated'; by?: string; at_t?: number };
  atmo_source: string;
  atmo_note: string;
  predicting: boolean;
  last_fix_t: number | null;
  trust: Record<string, { state: string; history: [number, string][] }>;
  landing_footprint: Footprint | null;
  impact_points: ImpactPoint[] | null;
  alerts: Alert[];
}

export interface MissionSummary {
  id: string;
  name: string;
  state: string;
  end_reason: string | null;
  t: number;
  phase: string;
  recommendation: Recommendation;
  operator: GroundFrame['operator'];
  faults: Record<string, unknown>;
  time_scale: number;
  atmo_source: string;
  frames: number;
}

export interface Faults {
  tank_leak_um?: number;
  tank_leak_onset_s?: number;
  balloon_leak_mm?: number;
  gnss_spoof_start_s?: number | null;
  motor_heater_failed?: boolean;
  blackouts?: [number, number][];
}

export interface EventLogEntry {
  n: number;
  wall: number;
  t: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

const post = (path: string, body?: unknown) =>
  fetch(`${API_URL}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });

export const missionsApi = {
  create: (body: { name?: string; seed?: number; time_scale?: number; atmosphere?: 'live' | 'snapshot'; faults?: Faults }) =>
    post('/api/missions', body).then((r) => json<{ mission_id: string; summary: MissionSummary }>(r)),
  list: () => fetch(`${API_URL}/api/missions`).then((r) => json<MissionSummary[]>(r)),
  summary: (id: string) => fetch(`${API_URL}/api/missions/${id}`).then((r) => json<MissionSummary>(r)),
  commit: (id: string, operator: string) => post(`/api/missions/${id}/commit`, { operator }).then((r) => json(r)),
  hold: (id: string, operator: string, release = false) => post(`/api/missions/${id}/hold`, { operator, release }).then((r) => json(r)),
  terminate: (id: string, operator: string) => post(`/api/missions/${id}/terminate`, { operator }).then((r) => json(r)),
  ack: (id: string, alertId: number, operator: string) => post(`/api/missions/${id}/alerts/${alertId}/ack`, { operator }).then((r) => json<Alert>(r)),
  events: (id: string) => fetch(`${API_URL}/api/missions/${id}/events`).then((r) => json<EventLogEntry[]>(r)),
};

/**
 * SSE of ground frames. Resumes after the last received id on reconnect (Last-Event-ID is sent by the
 * browser automatically). Closes on the server's explicit `event: end`, so EventSource never
 * reconnects to a finished mission.
 */
export function streamMission(id: string, fromId: number, handlers: { onFrame: (f: GroundFrame) => void; onEnd: () => void; onError: (m: string) => void }) {
  const src = new EventSource(`${API_URL}/api/missions/${id}/stream?from=${fromId}`);
  let closed = false;
  const close = () => {
    if (!closed) {
      closed = true;
      src.close();
    }
  };
  src.onmessage = (ev: MessageEvent<string>) => {
    try {
      handlers.onFrame(JSON.parse(ev.data) as GroundFrame);
    } catch {
      /* ignore malformed */
    }
  };
  src.addEventListener('end', () => {
    close();
    handlers.onEnd();
  });
  src.onerror = () => {
    // EventSource retries by itself with Last-Event-ID; report only, do not close.
    handlers.onError('stream interrupted, reconnecting');
  };
  return close;
}
