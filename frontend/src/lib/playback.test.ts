import { describe, expect, it } from 'vitest';
import { fixtures } from '../data/fixtures';
import {
  advancePlayhead,
  ascentRateAt,
  endTimeOf,
  formatMissionClock,
  frameIndexAt,
  interpolateFrame,
  playbackDurationMs,
} from './playback';

const sealed = fixtures.sealed;
const leak = fixtures.leak100;

describe('frameIndexAt', () => {
  it('finds the latest frame at or before t', () => {
    expect(frameIndexAt(sealed, 0)).toBe(-1);
    expect(frameIndexAt(sealed, 1)).toBe(0);
    expect(frameIndexAt(sealed, 25.99)).toBe(0);
    expect(frameIndexAt(sealed, 26)).toBe(1);
    expect(frameIndexAt(sealed, 500)).toBe(sealed.length - 1);
  });
});

describe('interpolateFrame', () => {
  it('returns null with no frames', () => {
    expect(interpolateFrame([], 10)).toBeNull();
  });

  it('interpolates numeric fields linearly by time_s', () => {
    // Halfway between t=1 (120 m) and t=26 (3120 m).
    const f = interpolateFrame(sealed, 13.5)!;
    expect(f.time_s).toBe(13.5);
    expect(f.altitude_m).toBeCloseTo(1620, 6);
    expect(f.ext_temp_c).toBeCloseTo((27.2 + 7.7) / 2, 6);
    expect(f.tank_pres_bar).toBeCloseTo((200 + 199.71) / 2, 6);
    expect(f.tilt_deg).toBeCloseTo((2.76 + 1.12) / 2, 6);
    expect(f.frameIndex).toBe(0);
    expect(f.exact).toBe(false);
  });

  it('returns exact backend values on a frame', () => {
    const f = interpolateFrame(sealed, 101)!;
    expect(f.altitude_m).toBe(12120);
    expect(f.exact).toBe(true);
    expect(f.frameIndex).toBe(4);
  });

  it('switches booleans and strings only at the frame where they change', () => {
    const before = interpolateFrame(leak, 112.99)!;
    expect(before.leak_detected).toBe(false);
    expect(before.is_abort).toBe(false);
    expect(before.leak_status).toBe('SEALED (OK)');
    // ...while the mass balance has already ticked most of the way to 10.09 g.
    expect(before.mass_loss_g).toBeGreaterThan(10);

    const at = interpolateFrame(leak, 113)!;
    expect(at.leak_detected).toBe(true);
    expect(at.is_abort).toBe(true);
    expect(at.leak_status).toBe('LEAK:101µm');

    const armedBefore = interpolateFrame(sealed, 209.5)!;
    expect(armedBefore.gate_status).toBe('NO-GO');
    expect(interpolateFrame(sealed, 210)!.gate_status).toBe('ARMED');
  });

  it('clamps before the first and after the last frame', () => {
    const pad = interpolateFrame(sealed, 0)!;
    expect(pad.altitude_m).toBe(120);
    expect(pad.time_s).toBe(0);
    const after = interpolateFrame(sealed, 999)!;
    expect(after.altitude_m).toBe(25200);
    expect(after.is_ignition).toBe(true);
  });

  it('is a pure function of the playhead (scrubbing back restores state)', () => {
    const a = interpolateFrame(leak, 60);
    interpolateFrame(leak, 113);
    expect(interpolateFrame(leak, 60)).toEqual(a);
  });

  it('does not mutate the input frames', () => {
    const copy = JSON.parse(JSON.stringify(sealed));
    interpolateFrame(sealed, 150);
    expect(sealed).toEqual(copy);
  });
});

describe('mission clock', () => {
  it('plays 1 mission second per 40 ms at 1x', () => {
    expect(advancePlayhead(0, 40, 1, 210)).toBeCloseTo(1, 9);
    expect(advancePlayhead(0, 40, 4, 210)).toBeCloseTo(4, 9);
    expect(advancePlayhead(0, 40, 0.5, 210)).toBeCloseTo(0.5, 9);
  });

  it('clamps at the end of the buffer', () => {
    expect(advancePlayhead(209, 1000, 1, 210)).toBe(210);
    expect(advancePlayhead(5, 100, 1, 0)).toBe(0);
  });

  it('a sealed run plays in about 8.4 s at 1x', () => {
    expect(playbackDurationMs(endTimeOf(sealed), 1)).toBe(8400);
  });

  it('formats the mission clock', () => {
    expect(formatMissionClock(0)).toBe('T+ 000:00');
    expect(formatMissionClock(113.7)).toBe('T+ 001:53');
    expect(formatMissionClock(210)).toBe('T+ 003:30');
  });

  it('derives ascent rate from bracketing frames', () => {
    expect(ascentRateAt(sealed, 30)).toBeCloseTo(120, 6);
    expect(ascentRateAt([sealed[0]], 0)).toBeNull();
  });
});
