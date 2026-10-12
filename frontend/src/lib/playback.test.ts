import { describe, expect, it } from 'vitest';
import { fixtures } from '../data/fixtures';
import {
  advancePlayhead,
  ascentRateAt,
  endTimeOf,
  formatMissionClock,
  frameIndexAt,
  interpolateFrame,
  MS_PER_MISSION_SECOND,
  playbackDurationMs,
} from './playback';

const sealed = fixtures.sealed;
const leak = fixtures.leak100;

describe('frameIndexAt', () => {
  it('finds the latest frame at or before t', () => {
    const t1 = sealed[1].time_s;
    expect(frameIndexAt(sealed, sealed[0].time_s - 0.5)).toBe(-1);
    expect(frameIndexAt(sealed, sealed[0].time_s)).toBe(0);
    expect(frameIndexAt(sealed, t1 - 0.01)).toBe(0);
    expect(frameIndexAt(sealed, t1)).toBe(1);
    expect(frameIndexAt(sealed, 1e9)).toBe(sealed.length - 1);
  });
});

describe('interpolateFrame', () => {
  it('returns null with no frames', () => {
    expect(interpolateFrame([], 10)).toBeNull();
  });

  it('interpolates numeric fields linearly by time_s', () => {
    const [a, b] = [sealed[10], sealed[11]];
    const t = (a.time_s + b.time_s) / 2;
    const f = interpolateFrame(sealed, t)!;
    expect(f.time_s).toBe(t);
    expect(f.altitude_m).toBeCloseTo((a.altitude_m + b.altitude_m) / 2, 6);
    expect(f.ext_temp_c).toBeCloseTo((a.ext_temp_c + b.ext_temp_c) / 2, 6);
    expect(f.tank_pres_bar).toBeCloseTo((a.tank_pres_bar + b.tank_pres_bar) / 2, 6);
    expect(f.swing_amplitude_deg).toBeCloseTo((a.swing_amplitude_deg! + b.swing_amplitude_deg!) / 2, 6);
    expect(f.frameIndex).toBe(10);
    expect(f.exact).toBe(false);
  });

  it('returns exact backend values on a frame', () => {
    const f = interpolateFrame(sealed, sealed[20].time_s)!;
    expect(f.altitude_m).toBe(sealed[20].altitude_m);
    expect(f.exact).toBe(true);
    expect(f.frameIndex).toBe(20);
  });

  it('switches booleans and strings only at the frame where they change', () => {
    const last = leak[leak.length - 1];
    const before = interpolateFrame(leak, last.time_s - 0.01)!;
    expect(before.leak_detected).toBe(false);
    expect(before.is_abort).toBe(false);
    expect(before.leak_status).toBe('SEALED (OK)');
    const at = interpolateFrame(leak, last.time_s)!;
    expect(at.leak_detected).toBe(true);
    expect(at.is_abort).toBe(true);
    expect(at.leak_status).toMatch(/^LEAK:\d+µm$/);

    const end = sealed[sealed.length - 1];
    expect(interpolateFrame(sealed, end.time_s - 0.01)!.gate_status).toBe('NO-GO');
    expect(interpolateFrame(sealed, end.time_s)!.gate_status).toBe('ARMED');
  });

  it('clamps before the first and after the last frame', () => {
    const pad = interpolateFrame(sealed, 0)!;
    expect(pad.altitude_m).toBe(sealed[0].altitude_m);
    expect(pad.time_s).toBe(0);
    const after = interpolateFrame(sealed, 1e9)!;
    expect(after.go_recommended).toBe(true);
    expect(after.is_ignition).toBe(false); // software never confirms ignition
  });

  it('is a pure function of the playhead (scrubbing back restores state)', () => {
    const a = interpolateFrame(sealed, 3000);
    interpolateFrame(sealed, 5800);
    expect(interpolateFrame(sealed, 3000)).toEqual(a);
  });

  it('does not mutate the input frames', () => {
    const copy = JSON.parse(JSON.stringify(sealed));
    interpolateFrame(sealed, 4000);
    expect(sealed).toEqual(copy);
  });
});

describe('mission clock', () => {
  it('plays 1 mission second per MS_PER_MISSION_SECOND at 1x', () => {
    expect(advancePlayhead(0, MS_PER_MISSION_SECOND, 1, 6000)).toBeCloseTo(1, 9);
    expect(advancePlayhead(0, MS_PER_MISSION_SECOND, 4, 6000)).toBeCloseTo(4, 9);
    expect(advancePlayhead(0, MS_PER_MISSION_SECOND, 0.5, 6000)).toBeCloseTo(0.5, 9);
  });

  it('clamps at the end of the buffer', () => {
    expect(advancePlayhead(209, 1000, 1, 210)).toBe(210);
    expect(advancePlayhead(5, 100, 1, 0)).toBe(0);
  });

  it('a sealed run (about 97 min to GO) plays in 8-15 s at 1x', () => {
    const ms = playbackDurationMs(endTimeOf(sealed), 1);
    expect(ms).toBeGreaterThan(8000);
    expect(ms).toBeLessThan(15000);
  });

  it('formats the mission clock', () => {
    expect(formatMissionClock(0)).toBe('T+ 000:00');
    expect(formatMissionClock(113.7)).toBe('T+ 001:53');
    expect(formatMissionClock(5827)).toBe('T+ 097:07');
  });

  it('derives ascent rate from bracketing frames (balloon physics: 3-8 m/s)', () => {
    const r = ascentRateAt(sealed, 2000)!;
    expect(r).toBeGreaterThan(3);
    expect(r).toBeLessThan(8);
    expect(ascentRateAt([sealed[0]], 0)).toBeNull();
  });
});
