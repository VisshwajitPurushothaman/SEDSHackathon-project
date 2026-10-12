import { describe, expect, it } from 'vitest';
import type { Estimate, GroundFrame } from './api';
import { fmtT, formatEstimate, history } from './ui';

const est = (over: Partial<Estimate<any>>): Estimate<any> => ({ value: 1, unit: 'm', status: 'MEASURED', sigma: null, source: 's', age_s: 0, ...over });

describe('formatEstimate', () => {
  it('scales and offsets numbers', () => {
    expect(formatEstimate(est({ value: 28123 }), { scale: 0.001, digits: 2, unit: 'km' })).toEqual({ text: '28.12', unit: 'km' });
    expect(formatEstimate(est({ value: 273.15 + 10 }), { offset: -273.15, digits: 1, unit: '°C' }).text).toBe('10.0');
  });
  it('never shows a stale value as live: UNKNOWN shows the last value in brackets', () => {
    expect(formatEstimate(est({ value: null, status: 'UNKNOWN', last_value: 5.5 }), { digits: 1 }).text).toBe('(5.5)');
    expect(formatEstimate(est({ value: null, status: 'UNKNOWN' })).text).toBe('--');
  });
  it('formats booleans and missing estimates', () => {
    expect(formatEstimate(est({ value: true })).text).toBe('yes');
    expect(formatEstimate(undefined).text).toBe('--');
  });
});

describe('history', () => {
  it('collects numeric values up to the cursor with their status', () => {
    const frames = [0, 1, 2, 3].map((i) => ({ t: i * 5, estimates: { altitude: est({ value: i * 10, status: i === 2 ? 'PREDICTED' : 'FUSED' }) } }) as unknown as GroundFrame);
    const h = history(frames, 2, 'altitude');
    expect(h.map((p) => p.v)).toEqual([0, 10, 20]);
    expect(h[2].s).toBe('PREDICTED');
    expect(history(frames, 3, 'missing')).toEqual([]);
  });
});

describe('fmtT', () => {
  it('formats mission time as T+h:mm:ss', () => {
    expect(fmtT(0)).toBe('T+0:00:00');
    expect(fmtT(5826.4)).toBe('T+1:37:06');
    expect(fmtT(null)).toBe('--');
  });
});
