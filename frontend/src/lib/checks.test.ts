import { describe, expect, it } from 'vitest';
import { fixtures } from '../data/fixtures';
import { interpolateFrame } from './playback';
import {
  deriveChecks,
  deriveMissionEvents,
  derivePhase,
  deriveVerdict,
  gateConditions,
  parseLeakUm,
  summarizeChecks,
} from './checks';

const sealed = fixtures.sealed;
const leak = fixtures.leak100;
const at = (frames: typeof sealed, t: number) => interpolateFrame(frames, t)!;

describe('deriveChecks', () => {
  it('is all PENDING before any telemetry', () => {
    const c = deriveChecks(null);
    expect(Object.values(c).every((r) => r.state === 'PENDING')).toBe(true);
    expect(summarizeChecks(c)).toMatchObject({ active: 0, failed: 0 });
  });

  it('sealed run: tank PASS, altitude MONITORING during the climb', () => {
    const c = deriveChecks(at(sealed, 60));
    expect(c.tank.state).toBe('PASS');
    expect(c.altitude.state).toBe('MONITORING');
    expect(c.pressure.state).toBe('MONITORING');
    expect(c.motor.state).toBe('MONITORING');
    expect(c.mass.state).toBe('MONITORING');
  });

  it('release altitude passes at >= 25 000 m', () => {
    expect(deriveChecks(at(sealed, 201)).altitude.state).toBe('MONITORING');
    expect(deriveChecks(at(sealed, 209)).altitude.state).toBe('PASS');
  });

  it('tilt passes only inside ±1.5°', () => {
    expect(deriveChecks(at(sealed, 1)).tilt.state).toBe('HOLD'); // 2.76°
    expect(deriveChecks(at(sealed, 26)).tilt.state).toBe('PASS'); // 1.12°
    expect(deriveChecks(at(sealed, 210)).tilt.state).toBe('PASS'); // -0.72°
    expect(deriveChecks(at(sealed, 209)).tilt.state).toBe('HOLD'); // -2.2°
  });

  it('mass balance: caution above 5 g (display), FAIL when the backend flags the leak', () => {
    expect(deriveChecks(at(leak, 26)).mass.state).toBe('MONITORING'); // 2.33 g
    expect(deriveChecks(at(leak, 76)).mass.state).toBe('HOLD'); // 6.8 g
    expect(deriveChecks(at(leak, 113)).mass.state).toBe('FAIL');
  });

  it('100 µm leak: tank FAILs exactly at the leak frame and scrubbing back restores PASS', () => {
    expect(deriveChecks(at(leak, 112.9)).tank.state).toBe('PASS');
    const failed = deriveChecks(at(leak, 113));
    expect(failed.tank.state).toBe('FAIL');
    expect(failed.tank.note).toContain('101');
    expect(summarizeChecks(failed).failed).toBe(2);
    expect(deriveChecks(at(leak, 50)).tank.state).toBe('PASS');
  });
});

describe('gate and verdict', () => {
  it('explains the gate with the backend arming rule', () => {
    expect(gateConditions(at(sealed, 210))).toEqual({ altitude: true, tilt: true, sealed: true });
    expect(gateConditions(at(sealed, 209))).toEqual({ altitude: true, tilt: false, sealed: true });
  });

  it('derives verdicts like the backend', () => {
    expect(deriveVerdict(sealed, true)).toEqual({ verdict: 'IGNITION_CONFIRMED', abort_reason: null });
    expect(deriveVerdict(leak, true)).toEqual({
      verdict: 'MISSION_ABORTED',
      abort_reason: 'Pneumatic Depletion Detected: LEAK:101µm',
    });
    expect(deriveVerdict(sealed.slice(0, 3), false)).toBeNull();
    expect(deriveVerdict(sealed.slice(0, 3), true)?.verdict).toBe('TERMINATED');
  });

  it('derives the phase from the playhead', () => {
    expect(derivePhase(null, false, null)).toBe('Standby');
    expect(derivePhase(at(sealed, 0), false, null)).toBe('Pad');
    expect(derivePhase(at(sealed, 60), false, null)).toBe('Ascent');
    expect(derivePhase(at(sealed, 209), false, null)).toBe('Release band');
    expect(derivePhase(at(sealed, 210), true, 'IGNITION_CONFIRMED')).toBe('Armed');
    expect(derivePhase(at(leak, 113), true, 'MISSION_ABORTED')).toBe('Aborted');
  });
});

describe('deriveMissionEvents', () => {
  it('sealed run: release crossing is interpolated, then ARMED', () => {
    const ev = deriveMissionEvents(sealed, true);
    expect(ev.map((e) => e.kind)).toEqual(['release', 'armed']);
    // 24 120 m @ 201 s -> 25 080 m @ 209 s: 25 000 m at 201 + 880/960*8.
    expect(ev[0].t).toBeCloseTo(201 + (880 / 960) * 8, 6);
    expect(ev[0].derived).toBe(true);
    expect(ev[1].t).toBe(210);
  });

  it('leak run: leak and abort at 113 s', () => {
    const ev = deriveMissionEvents(leak, true);
    expect(ev.map((e) => e.kind).sort()).toEqual(['abort', 'leak']);
    expect(ev.every((e) => e.t === 113)).toBe(true);
  });

  it('parses the leak estimate', () => {
    expect(parseLeakUm('LEAK:101µm')).toBe(101);
    expect(parseLeakUm('SEALED (OK)')).toBeNull();
  });
});
