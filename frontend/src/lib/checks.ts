/**
 * Pre-flight / launch-commit checks, derived from the current (interpolated)
 * frame only. Thresholds are the backend's own (backend.py):
 *   - leak flag: total mass lost > 10 g (and loss rate > 0.05 g/s)
 *   - arming:    altitude >= 25 000 m AND |tilt| <= 1.5° AND no leak
 * Anything else here (the 5 g caution colour) is a display-only threshold and
 * is labelled as such in the UI.
 */
import type { TelemetryFrame, Verdict } from '../api/types';
import type { PlaybackFrame } from './playback';

export const RELEASE_ALTITUDE_M = 25_000;
export const CEILING_ALTITUDE_M = 30_000;
export const TILT_LIMIT_DEG = 1.5;
export const START_TANK_PRES_BAR = 200;
export const LEAK_FLAG_MASS_G = 10;
/** Display-only caution level for the mass balance. Not a backend rule. */
export const MASS_CAUTION_G = 5;

export type CheckState = 'PENDING' | 'MONITORING' | 'PASS' | 'FAIL' | 'HOLD';

export type CheckId = 'tank' | 'pressure' | 'mass' | 'altitude' | 'tilt' | 'motor' | 'atmosphere';

export const CHECK_ORDER: CheckId[] = ['tank', 'pressure', 'mass', 'altitude', 'tilt', 'motor', 'atmosphere'];

export interface CheckDef {
  id: CheckId;
  num: number;
  name: string;
  /** The rule, as shown on the card. */
  rule: string;
  /** Does the backend gate on this check? */
  gated: boolean;
}

export const CHECK_DEFS: Record<CheckId, CheckDef> = {
  tank: { id: 'tank', num: 1, name: 'Pressurant tank integrity', rule: 'leak_detected = false', gated: true },
  pressure: { id: 'pressure', num: 2, name: 'Tank pressure', rule: 'vs 200 bar start · shown, not gated', gated: false },
  mass: {
    id: 'mass',
    num: 3,
    name: 'Gas mass balance',
    rule: 'leak flag > 10 g lost (backend) · caution > 5 g (display only)',
    gated: true,
  },
  altitude: { id: 'altitude', num: 4, name: 'Release altitude', rule: 'altitude ≥ 25 000 m', gated: true },
  tilt: { id: 'tilt', num: 5, name: 'Gondola swing (tilt)', rule: '|tilt| ≤ 1.5°', gated: true },
  motor: { id: 'motor', num: 6, name: 'Motor temperature', rule: 'monitored, no limit set', gated: false },
  atmosphere: { id: 'atmosphere', num: 7, name: 'Atmosphere', rule: 'outside temperature & pressure · not gated', gated: false },
};

export interface CheckResult {
  id: CheckId;
  state: CheckState;
  /** Short status note shown under the value (e.g. "inside window"). */
  note: string;
}

export type CheckMap = Record<CheckId, CheckResult>;

const pending = (id: CheckId): CheckResult => ({ id, state: 'PENDING', note: 'awaiting telemetry' });

/** Derive every check from one frame. `null` (no data yet) gives all PENDING. */
export function deriveChecks(frame: TelemetryFrame | null): CheckMap {
  if (!frame) {
    return Object.fromEntries(CHECK_ORDER.map((id) => [id, pending(id)])) as CheckMap;
  }
  const absTilt = Math.abs(frame.tilt_deg);
  const reached = frame.altitude_m >= RELEASE_ALTITUDE_M;

  return {
    tank: frame.leak_detected
      ? { id: 'tank', state: 'FAIL', note: `leak detected · est. ${parseLeakUm(frame.leak_status) ?? '?'} µm` }
      : { id: 'tank', state: 'PASS', note: 'sealed · monitoring' },
    pressure: {
      id: 'pressure',
      state: 'MONITORING',
      note: `${((frame.tank_pres_bar / START_TANK_PRES_BAR) * 100).toFixed(1)} % of start`,
    },
    mass: frame.leak_detected
      ? { id: 'mass', state: 'FAIL', note: 'backend leak flag raised' }
      : frame.mass_loss_g > MASS_CAUTION_G
        ? { id: 'mass', state: 'HOLD', note: 'above 5 g display caution' }
        : { id: 'mass', state: 'MONITORING', note: 'balance nominal' },
    altitude: reached
      ? { id: 'altitude', state: 'PASS', note: 'in release band' }
      : {
          id: 'altitude',
          state: 'MONITORING',
          note: `${((RELEASE_ALTITUDE_M - frame.altitude_m) / 1000).toFixed(1)} km to release band`,
        },
    tilt:
      absTilt <= TILT_LIMIT_DEG
        ? { id: 'tilt', state: 'PASS', note: 'inside ±1.5° window' }
        : { id: 'tilt', state: 'HOLD', note: 'outside ±1.5° window' },
    motor: { id: 'motor', state: 'MONITORING', note: 'no limit set by backend' },
    atmosphere: { id: 'atmosphere', state: 'MONITORING', note: 'live sounding' },
  };
}

export interface CheckSummary {
  active: number;
  passing: number;
  failed: number;
  holds: number;
  total: number;
}

export function summarizeChecks(checks: CheckMap): CheckSummary {
  const list = CHECK_ORDER.map((id) => checks[id]);
  return {
    active: list.filter((c) => c.state !== 'PENDING').length,
    passing: list.filter((c) => c.state === 'PASS').length,
    failed: list.filter((c) => c.state === 'FAIL').length,
    holds: list.filter((c) => c.state === 'HOLD').length,
    total: list.length,
  };
}

/** "LEAK:101µm" -> 101. Returns null for "SEALED (OK)". */
export function parseLeakUm(status: string): number | null {
  const m = /LEAK:\s*(\d+(?:\.\d+)?)/i.exec(status);
  return m ? Number(m[1]) : null;
}

/** The backend's arming conditions, evaluated on a frame (to explain NO-GO). */
export function gateConditions(frame: TelemetryFrame) {
  return {
    altitude: frame.altitude_m >= RELEASE_ALTITUDE_M,
    tilt: Math.abs(frame.tilt_deg) <= TILT_LIMIT_DEG,
    sealed: !frame.leak_detected,
  };
}

// ---------------------------------------------------------------------------
// Mission-level derivations (timeline markers, phase, verdict)
// ---------------------------------------------------------------------------

export type MissionEventKind = 'leak' | 'release' | 'armed' | 'abort' | 'terminated';

export interface MissionEvent {
  kind: MissionEventKind;
  t: number;
  label: string;
  /** True when the time is computed in the frontend rather than a frame time. */
  derived: boolean;
}

/**
 * Event markers for the timeline. Frame times come from the backend, except
 * the 25 km crossing, which is interpolated between the bracketing frames.
 * `complete` = the run has ended (replay, or stream closed).
 */
export function deriveMissionEvents(frames: readonly TelemetryFrame[], complete: boolean): MissionEvent[] {
  const events: MissionEvent[] = [];
  const leak = frames.find((f) => f.leak_detected);
  if (leak) {
    events.push({ kind: 'leak', t: leak.time_s, label: `Leak detected · ${parseLeakUm(leak.leak_status) ?? '?'} µm`, derived: false });
  }

  const ri = frames.findIndex((f) => f.altitude_m >= RELEASE_ALTITUDE_M);
  if (ri >= 0) {
    let t = frames[ri].time_s;
    if (ri > 0) {
      const a = frames[ri - 1];
      const b = frames[ri];
      t = a.time_s + ((RELEASE_ALTITUDE_M - a.altitude_m) / (b.altitude_m - a.altitude_m)) * (b.time_s - a.time_s);
    }
    events.push({ kind: 'release', t, label: 'Reached 25 km release band', derived: ri > 0 });
  }

  const armed = frames.find((f) => f.gate_status === 'ARMED');
  if (armed) events.push({ kind: 'armed', t: armed.time_s, label: 'Gate ARMED', derived: false });

  const abort = frames.find((f) => f.is_abort);
  if (abort) events.push({ kind: 'abort', t: abort.time_s, label: 'ABORT', derived: false });

  if (complete && frames.length && !armed && !abort) {
    const last = frames[frames.length - 1];
    events.push({ kind: 'terminated', t: last.time_s, label: 'Terminated', derived: false });
  }
  return events.sort((a, b) => a.t - b.t);
}

/**
 * Verdict for a run whose summary we did not get from POST /api/simulate
 * (live stream and demo fixtures). Mirrors backend.py's own rules.
 */
export function deriveVerdict(
  frames: readonly TelemetryFrame[],
  complete: boolean,
): { verdict: Verdict; abort_reason: string | null } | null {
  const ign = frames.find((f) => f.is_ignition);
  if (ign) return { verdict: 'IGNITION_CONFIRMED', abort_reason: null };
  const abort = frames.find((f) => f.is_abort);
  if (abort) return { verdict: 'MISSION_ABORTED', abort_reason: `Pneumatic Depletion Detected: ${abort.leak_status}` };
  if (complete && frames.length) return { verdict: 'TERMINATED', abort_reason: null };
  return null;
}

export type Phase = 'Standby' | 'Pad' | 'Ascent' | 'Release band' | 'Armed' | 'Aborted' | 'Terminated';

/** Phase name for the heads-up overlay, from the frame at the playhead. */
export function derivePhase(frame: PlaybackFrame | null, atEnd: boolean, verdict: Verdict | null): Phase {
  if (!frame) return 'Standby';
  if (frame.is_abort) return 'Aborted';
  if (frame.gate_status === 'ARMED') return 'Armed';
  if (atEnd && verdict === 'TERMINATED') return 'Terminated';
  if (frame.altitude_m >= RELEASE_ALTITUDE_M) return 'Release band';
  if (frame.altitude_m < 300) return 'Pad';
  return 'Ascent';
}
