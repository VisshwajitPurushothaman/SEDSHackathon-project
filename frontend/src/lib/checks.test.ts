import { describe, expect, it } from 'vitest';
import type { TelemetryFrame } from '../api/types';
import { fixtures } from '../data/fixtures';
import { interpolateFrame } from './playback';
import {
  deriveChecks,
  deriveMissionEvents,
  derivePhase,
  deriveVerdict,
  gateConditions,
  parseLeakUm,
  RELEASE_ALTITUDE_M,
  summarizeChecks,
} from './checks';

const sealed = fixtures.sealed;
const leak = fixtures.leak100;
const at = (frames: TelemetryFrame[], t: number) => interpolateFrame(frames, t)!;
const last = (frames: TelemetryFrame[]) => frames[frames.length - 1];
const frame = (over: Partial<TelemetryFrame>): TelemetryFrame => ({ ...sealed[30], ...over });

describe('deriveChecks', () => {
  it('is all PENDING before any telemetry', () => {
    const c = deriveChecks(null);
    expect(Object.values(c).every((r) => r.state === 'PENDING')).toBe(true);
    expect(summarizeChecks(c)).toMatchObject({ active: 0, failed: 0 });
  });

  it('sealed run: tank PASS, altitude MONITORING during the climb', () => {
    const c = deriveChecks(at(sealed, 2000));
    expect(c.tank.state).toBe('PASS');
    expect(c.altitude.state).toBe('MONITORING');
    expect(c.pressure.state).toBe('MONITORING');
    expect(c.mass.state).toBe('MONITORING');
  });

  it('release altitude passes inside the 26-30 km band', () => {
    expect(deriveChecks(frame({ altitude_m: 25_000 })).altitude.state).toBe('MONITORING');
    expect(deriveChecks(frame({ altitude_m: 28_000 })).altitude.state).toBe('PASS');
    expect(deriveChecks(frame({ altitude_m: 31_000 })).altitude.state).toBe('HOLD');
    expect(deriveChecks(last(sealed)).altitude.state).toBe('PASS');
  });

  it('swing is judged on the amplitude, not the instantaneous tilt', () => {
    expect(deriveChecks(frame({ tilt_deg: 0.01, swing_amplitude_deg: 2.0 })).tilt.state).toBe('HOLD');
    expect(deriveChecks(frame({ tilt_deg: 1.0, swing_amplitude_deg: 1.2 })).tilt.state).toBe('PASS');
  });

  it('motor below the qualified minimum holds', () => {
    expect(deriveChecks(frame({ motor_temp_c: -12 })).motor.state).toBe('HOLD');
    expect(deriveChecks(frame({ motor_temp_c: 5 })).motor.state).toBe('PASS');
  });

  it('mass balance: caution above 5 g (display), FAIL when the ground detector declares a leak', () => {
    expect(deriveChecks(frame({ mass_loss_g: 1 })).mass.state).toBe('MONITORING');
    expect(deriveChecks(frame({ mass_loss_g: 6 })).mass.state).toBe('HOLD');
    expect(deriveChecks(last(leak)).mass.state).toBe('FAIL');
  });

  it('100 um leak: tank FAILs exactly at the detection frame and scrubbing back restores PASS', () => {
    const tLeak = last(leak).time_s;
    expect(deriveChecks(at(leak, tLeak - 0.1)).tank.state).toBe('PASS');
    const failed = deriveChecks(at(leak, tLeak));
    expect(failed.tank.state).toBe('FAIL');
    expect(failed.tank.note).toMatch(/\d+ µm/);
    expect(summarizeChecks(failed).failed).toBe(2);
    expect(deriveChecks(at(leak, 10)).tank.state).toBe('PASS');
  });
});

describe('gate and verdict', () => {
  it('explains the gate with the backend gate states', () => {
    const g = gateConditions(last(sealed));
    expect(g.map((x) => x.id)).toEqual(['float', 'nav', 'motor', 'swing', 'impact', 'tank']);
    expect(g.every((x) => x.ok === true)).toBe(true);
  });

  it('derives verdicts like the backend; never "ignition confirmed"', () => {
    expect(deriveVerdict(sealed, true)).toEqual({ verdict: 'GO_RECOMMENDED', abort_reason: null });
    expect(deriveVerdict(leak, true)?.verdict).toBe('ABORT_RECOMMENDED');
    expect(deriveVerdict(leak, true)?.abort_reason).toMatch(/^Pressurant leak detected: LEAK:\d+µm$/);
    expect(deriveVerdict(sealed.slice(0, 3), false)).toBeNull();
    expect(deriveVerdict(sealed.slice(0, 3), true)?.verdict).toBe('NO_GO');
  });

  it('derives the phase from the playhead', () => {
    expect(derivePhase(null, false, null)).toBe('Standby');
    expect(derivePhase(at(sealed, 0), false, null)).toBe('Pad');
    expect(derivePhase(at(sealed, 2000), false, null)).toBe('Ascent');
    expect(derivePhase(at(sealed, last(sealed).time_s), true, 'GO_RECOMMENDED')).toBe('Armed');
    expect(derivePhase(at(leak, last(leak).time_s), true, 'ABORT_RECOMMENDED')).toBe('Aborted');
  });
});

describe('deriveMissionEvents', () => {
  it('sealed run: release crossing is interpolated, then GO', () => {
    const ev = deriveMissionEvents(sealed, true);
    expect(ev.map((e) => e.kind)).toEqual(['release', 'armed']);
    const i = sealed.findIndex((f) => f.altitude_m >= RELEASE_ALTITUDE_M);
    const [a, b] = [sealed[i - 1], sealed[i]];
    expect(ev[0].t).toBeCloseTo(a.time_s + ((RELEASE_ALTITUDE_M - a.altitude_m) / (b.altitude_m - a.altitude_m)) * (b.time_s - a.time_s), 6);
    expect(ev[0].derived).toBe(true);
    expect(ev[1].t).toBe(last(sealed).time_s);
  });

  it('leak run: leak and abort on the detection frame', () => {
    const ev = deriveMissionEvents(leak, true);
    expect(ev.map((e) => e.kind).sort()).toEqual(['abort', 'leak']);
    expect(ev.every((e) => e.t === last(leak).time_s)).toBe(true);
  });

  it('parses the leak estimate', () => {
    expect(parseLeakUm('LEAK:101µm')).toBe(101);
    expect(parseLeakUm('SEALED (OK)')).toBeNull();
  });
});
