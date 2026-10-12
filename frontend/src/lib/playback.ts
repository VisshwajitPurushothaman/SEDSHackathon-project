/**
 * Playback engine.
 *
 * The backend emits very few frames (one per ~3 km of climb), so the UI never
 * animates frame-to-frame. Instead a mission clock (the playhead, in mission
 * seconds) moves at a fixed screen rate and every numeric field is linearly
 * interpolated by `time_s` between the two bracketing frames. Booleans and
 * strings are step functions: they take the value of the latest frame whose
 * `time_s` is at or before the playhead.
 *
 * Everything here is pure so that scrubbing in any direction is exact: the
 * state at a playhead is a function of (frames, playhead) only.
 */
import type { TelemetryFrame } from '../api/types';

/** Default screen time per mission second (1x). A sealed run (210 s) plays in ~8.4 s. */
export const MS_PER_MISSION_SECOND = 40;

export const SPEEDS = [0.5, 1, 2, 4] as const;
export type Speed = (typeof SPEEDS)[number];

/** Fields that are interpolated linearly between frames. */
export const NUMERIC_FIELDS = [
  'time_s',
  'altitude_m',
  'ext_temp_c',
  'ext_pres_hpa',
  'tank_pres_bar',
  'motor_temp_c',
  'mass_loss_g',
  'tilt_deg',
] as const satisfies readonly (keyof TelemetryFrame)[];

export type NumericField = (typeof NUMERIC_FIELDS)[number];

/** A frame at an arbitrary playhead, plus where it came from. */
export interface PlaybackFrame extends TelemetryFrame {
  /** Index of the latest backend frame at or before the playhead (source of discrete fields). */
  frameIndex: number;
  /** True when the playhead sits exactly on a backend frame (no interpolation involved). */
  exact: boolean;
}

/** Index of the latest frame with time_s <= t, or -1 if t is before the first frame. */
export function frameIndexAt(frames: readonly TelemetryFrame[], t: number): number {
  let lo = 0;
  let hi = frames.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].time_s <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

const lerp = (a: number, b: number, k: number) => a + (b - a) * k;

/**
 * Telemetry at playhead `t`.
 * - Before the first frame the vehicle is shown at its first frame (on the pad).
 * - After the last frame values hold at the last frame.
 * Returns null when there are no frames.
 */
export function interpolateFrame(frames: readonly TelemetryFrame[], t: number): PlaybackFrame | null {
  if (frames.length === 0) return null;
  const first = frames[0];
  const last = frames[frames.length - 1];

  if (t <= first.time_s) return { ...first, time_s: Math.max(0, t), frameIndex: 0, exact: t === first.time_s };
  if (t >= last.time_s) return { ...last, frameIndex: frames.length - 1, exact: t === last.time_s };

  const i = frameIndexAt(frames, t);
  const a = frames[i];
  const b = frames[i + 1];
  const span = b.time_s - a.time_s;
  const k = span > 0 ? (t - a.time_s) / span : 0;

  // Discrete fields come from `a` (the frame at or before t).
  const out: PlaybackFrame = { ...a, frameIndex: i, exact: k === 0 };
  for (const key of NUMERIC_FIELDS) out[key] = lerp(a[key], b[key], k);
  out.time_s = t;
  return out;
}

/** Mission time covered by the buffer (0 when empty). */
export function endTimeOf(frames: readonly TelemetryFrame[]): number {
  return frames.length ? frames[frames.length - 1].time_s : 0;
}

/** Advance the playhead by a wall-clock delta, clamped to [0, endTime]. */
export function advancePlayhead(playhead: number, dtMs: number, speed: number, endTime: number): number {
  const next = playhead + (dtMs / MS_PER_MISSION_SECOND) * speed;
  return clampPlayhead(next, endTime);
}

export function clampPlayhead(t: number, endTime: number): number {
  if (!Number.isFinite(t)) return 0;
  return Math.min(Math.max(t, 0), Math.max(endTime, 0));
}

/** Screen duration of a run at a given speed, in ms. */
export function playbackDurationMs(endTime: number, speed: number): number {
  return (endTime * MS_PER_MISSION_SECOND) / speed;
}

/**
 * Ascent rate (m/s) derived from the two backend frames that bracket t.
 * This is a frontend derivation, not a backend field.
 */
export function ascentRateAt(frames: readonly TelemetryFrame[], t: number): number | null {
  if (frames.length < 2) return null;
  let i = frameIndexAt(frames, t);
  if (i < 0) i = 0;
  if (i >= frames.length - 1) i = frames.length - 2;
  const a = frames[i];
  const b = frames[i + 1];
  const dt = b.time_s - a.time_s;
  return dt > 0 ? (b.altitude_m - a.altitude_m) / dt : null;
}

/** "T+ 003:30": minutes zero-padded to three digits, then seconds. */
export function formatMissionClock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const m = Math.floor(s / 60);
  return `T+ ${String(m).padStart(3, '0')}:${String(s % 60).padStart(2, '0')}`;
}
