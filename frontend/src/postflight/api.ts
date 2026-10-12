/** Client for the post-flight endpoints (see docs/API.md). */
import { API_URL } from '../api/client';
import type { EventLogEntry, Footprint } from '../flight/api';

export interface RecoveryStatus {
  state: 'idle' | 'running' | 'done';
  stage?: string;
  expected?: number;
  have?: number;
  received_live?: number;
  missing_at_start?: number;
  recovered?: number;
  retries?: number;
  failed_batches?: number;
  lost?: number[];
  lost_count?: number;
  hmac_failures?: number[];
  chain_ok?: boolean | null;
  chain_first_bad?: number | null;
  progress?: { stage: string; have: number; expected: number; recovered: number; lost: number }[];
}

export interface Requirement {
  id: string;
  text: string;
  status: 'PASS' | 'FAIL' | 'NOT TESTED' | 'INCONCLUSIVE';
  evidence: Record<string, unknown>;
}

export interface FaultRow {
  id: string;
  kind: string;
  injected: Record<string, unknown> | null;
  detected_at: number | null;
  detected_by: string | null;
  detection_delay_s: number | null;
  corrective_action: { by: string; text: string; wall: number } | null;
  estimated_diameter_um?: number | null;
  hindsight_onset_t?: number | null;
}

export interface TrackPoint {
  t: number;
  lat: number;
  lon: number;
  alt?: number;
  u?: number;
  z?: number;
  status?: string;
  sigma_h?: number;
}

export interface Scorecard {
  mode: string;
  reconnect_errors: { t: number; blackout_s: number; error_vs_truth_m: number }[];
  region_coverage: Record<'50' | '90' | '99', { claimed: number; achieved: number | null; snapshots: number }>;
  false_alarms: { t: number; key: string; title: string }[];
  landing?: { error_m: number; inside_90: boolean; truth: { lat: number; lon: number }; predicted_mean: { lat: number; lon: number } };
  leak?: { injected_um: number; onset_s: number; estimated_um: number | null; detected_at: number | null };
}

export interface Postflight {
  mission_id: string;
  state: string;
  grading: boolean;
  recovery: RecoveryStatus | null;
  requirements: Requirement[];
  faults: FaultRow[];
  operator_track: TrackPoint[] | null;
  reconstruction: TrackPoint[] | null;
  truth_track?: TrackPoint[] | null;
  scorecard?: Scorecard;
  footprint_at_cutdown: Footprint | null;
  landing_fix: { t: number; lat: number; lon: number } | null;
  closure: Record<string, { title: string; done: { by: string; wall: number } | null }>;
  event_log: EventLogEntry[];
  prediction_log: unknown[];
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(String(detail));
  }
  return (await res.json()) as T;
}

const post = (path: string, body?: unknown) =>
  fetch(`${API_URL}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });

export const postflightApi = {
  recover: (id: string) => post(`/api/missions/${id}/recover?batch_delay_s=0.05`).then((r) => json<RecoveryStatus>(r)),
  recoverStatus: (id: string) => fetch(`${API_URL}/api/missions/${id}/recover`).then((r) => json<RecoveryStatus>(r)),
  report: (id: string) => fetch(`${API_URL}/api/missions/${id}/postflight`).then((r) => json<Postflight>(r)),
  action: (id: string, faultId: string, operator: string, text: string) => post(`/api/missions/${id}/faults/${faultId}/action`, { operator, text }).then((r) => json(r)),
  closure: (id: string, item: string, operator: string) => post(`/api/missions/${id}/closure/${item}`, { operator }).then((r) => json(r)),
  exportUrl: (id: string, format: 'csv' | 'json') => `${API_URL}/api/missions/${id}/export?format=${format}`,
};
