/**
 * Pre-flight / launch-commit checks, derived from the current (interpolated)
 * frame only. The decision itself is the backend ground station's
 * (`recommendation`, `gates`); this module only explains it per check, using
 * the same limits as config/mission.yaml:
 *   - leak: ground detector (least-squares slope of the estimated tank mass)
 *   - release band 26-30 km, |v_z| < 1 m/s
 *   - swing AMPLITUDE <= 1.5 deg (not the instantaneous tilt)
 *   - motor >= -10 degC (qualified minimum, ASSUMPTION)
 * The 5 g mass-balance caution colour is display only and labelled as such.
 */
import type { TelemetryFrame, Verdict } from '../api/types';
import type { PlaybackFrame } from './playback';

export const RELEASE_ALTITUDE_M = 26_000;
export const CEILING_ALTITUDE_M = 30_000;
export const SWING_LIMIT_DEG = 1.5;
/** Kept as an alias: the wedge in the 3D view still shows the ±1.5° window. */
export const TILT_LIMIT_DEG = SWING_LIMIT_DEG;
export const MOTOR_MIN_C = -10;
export const START_TANK_PRES_BAR = 200;
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
  tank: { id: 'tank', num: 1, name: 'Pressurant tank integrity', rule: 'no leak declared by the ground detector (abort criterion)', gated: true },
  pressure: { id: 'pressure', num: 2, name: 'Tank pressure', rule: 'vs 200 bar fill · shown, not gated', gated: false },
  mass: {
    id: 'mass',
    num: 3,
    name: 'Gas mass balance',
    rule: 'leak = significant negative slope of m̂ = PV/(ZRT) · caution > 5 g (display only)',
    gated: true,
  },
  altitude: { id: 'altitude', num: 4, name: 'Release altitude', rule: '26–30 km band, |v_z| < 1 m/s', gated: true },
  tilt: { id: 'tilt', num: 5, name: 'Gondola swing', rule: 'swing amplitude ≤ 1.5° (not the instantaneous tilt)', gated: true },
  motor: { id: 'motor', num: 6, name: 'Motor temperature', rule: '≥ −10 °C qualified minimum (ASSUMPTION)', gated: true },
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

/** Swing amplitude if the backend sent it (new backend), else |tilt| (old recordings). */
export function swingOf(frame: TelemetryFrame): number {
  return frame.swing_amplitude_deg ?? Math.abs(frame.tilt_deg);
}

/** Derive every check from one frame. `null` (no data yet) gives all PENDING. */
export function deriveChecks(frame: TelemetryFrame | null): CheckMap {
  if (!frame) {
    return Object.fromEntries(CHECK_ORDER.map((id) => [id, pending(id)])) as CheckMap;
  }
  const swing = swingOf(frame);
  const inBand = frame.altitude_m >= RELEASE_ALTITUDE_M && frame.altitude_m <= CEILING_ALTITUDE_M;

  return {
    tank: frame.leak_detected
      ? { id: 'tank', state: 'FAIL', note: `leak declared · est. ${parseLeakUm(frame.leak_status) ?? '?'} µm` }
      : { id: 'tank', state: 'PASS', note: 'no significant mass loss' },
    pressure: {
      id: 'pressure',
      state: 'MONITORING',
      note: `${((frame.tank_pres_bar / START_TANK_PRES_BAR) * 100).toFixed(1)} % of fill`,
    },
    mass: frame.leak_detected
      ? { id: 'mass', state: 'FAIL', note: 'ground detector: significant loss' }
      : frame.mass_loss_g > MASS_CAUTION_G
        ? { id: 'mass', state: 'HOLD', note: 'above 5 g display caution' }
        : { id: 'mass', state: 'MONITORING', note: 'balance nominal' },
    altitude: inBand
      ? { id: 'altitude', state: 'PASS', note: 'in release band' }
      : frame.altitude_m > CEILING_ALTITUDE_M
        ? { id: 'altitude', state: 'HOLD', note: 'above release band' }
        : {
            id: 'altitude',
            state: 'MONITORING',
            note: `${((RELEASE_ALTITUDE_M - frame.altitude_m) / 1000).toFixed(1)} km to release band`,
          },
    tilt:
      swing <= SWING_LIMIT_DEG
        ? { id: 'tilt', state: 'PASS', note: 'amplitude within 1.5°' }
        : { id: 'tilt', state: 'HOLD', note: 'swinging more than 1.5°' },
    motor:
      frame.motor_temp_c >= MOTOR_MIN_C
        ? { id: 'motor', state: 'PASS', note: 'above qualified minimum' }
        : { id: 'motor', state: 'HOLD', note: 'below qualified minimum' },
    atmosphere: { id: 'atmosphere', state: 'MONITORING', note: frame.atmo_source ?? 'sounding' },
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

export interface GateExplanation {
  id: string;
  label: string;
  ok: boolean | null;
}

const GATE_LABELS: Record<string, string> = {
  float: 'float 26–30 km',
  nav: 'nav integrity',
  motor: 'motor ≥ −10 °C',
  swing: 'swing ≤ 1.5°',
  impact: 'impact area',
};

/** The backend's five commit gates plus the leak criterion, as sent with the frame. */
export function gateConditions(frame: TelemetryFrame): GateExplanation[] {
  const g = frame.gates ?? {};
  const out: GateExplanation[] = Object.keys(GATE_LABELS).map((id) => ({ id, label: GATE_LABELS[id], ok: g[id] ?? null }));
  out.push({ id: 'tank', label: 'no leak', ok: !frame.leak_detected });
  return out;
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

const isGo = (f: TelemetryFrame) => f.go_recommended ?? f.gate_status === 'ARMED';

/**
 * Event markers for the timeline. Frame times come from the backend, except
 * the release-band crossing, which is interpolated between the bracketing frames.
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
    events.push({ kind: 'release', t, label: 'Reached 26 km release band', derived: ri > 0 });
  }

  const go = frames.find(isGo);
  if (go) events.push({ kind: 'armed', t: go.time_s, label: 'GO recommended', derived: false });

  const abort = frames.find((f) => f.is_abort);
  if (abort) events.push({ kind: 'abort', t: abort.time_s, label: 'ABORT recommended', derived: false });

  if (complete && frames.length && !go && !abort) {
    const last = frames[frames.length - 1];
    events.push({ kind: 'terminated', t: last.time_s, label: 'No recommendation', derived: false });
  }
  return events.sort((a, b) => a.t - b.t);
}

/**
 * Verdict for a run whose summary we did not get from POST /api/simulate
 * (live stream and demo fixtures). Mirrors aerofire/api/legacy.py's summarize().
 */
export function deriveVerdict(
  frames: readonly TelemetryFrame[],
  complete: boolean,
): { verdict: Verdict; abort_reason: string | null } | null {
  if (frames.find(isGo)) return { verdict: 'GO_RECOMMENDED', abort_reason: null };
  const abort = frames.find((f) => f.is_abort);
  if (abort) return { verdict: 'ABORT_RECOMMENDED', abort_reason: `Pressurant leak detected: ${abort.leak_status}` };
  if (complete && frames.length) return { verdict: 'NO_GO', abort_reason: null };
  return null;
}

export type Phase = 'Standby' | 'Pad' | 'Ascent' | 'Release band' | 'Armed' | 'Aborted' | 'Terminated';

/** Phase name for the heads-up overlay, from the frame at the playhead. */
export function derivePhase(frame: PlaybackFrame | null, atEnd: boolean, verdict: Verdict | null): Phase {
  if (!frame) return 'Standby';
  if (frame.is_abort) return 'Aborted';
  if (isGo(frame)) return 'Armed';
  if (atEnd && verdict === 'NO_GO') return 'Terminated';
  if (frame.altitude_m >= RELEASE_ALTITUDE_M) return 'Release band';
  if (frame.altitude_m < 300) return 'Pad';
  return 'Ascent';
}
