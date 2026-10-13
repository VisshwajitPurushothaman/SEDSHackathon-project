import { describe, expect, it } from 'vitest';
import { fmtAge, fmtBand, fmtChannel, fmtMET, fmtUTC } from './format';

describe('console formatting', () => {
  it('formats mission elapsed time', () => {
    expect(fmtMET(0)).toBe('T+00:00:00');
    expect(fmtMET(5829.7)).toBe('T+01:37:09');
    expect(fmtMET(-12)).toBe('T-00:00:12');
    expect(fmtMET(null)).toBe('T+--:--:--');
  });
  it('formats UTC, ages and limit bands', () => {
    expect(fmtUTC(0)).toBe('00:00:00Z');
    expect(fmtAge(2.345)).toBe('2.3 s');
    expect(fmtAge(125)).toBe('125 s');
    expect(fmtAge(900)).toBe('15 min');
    expect(fmtBand([null, 1.5])).toBe('≤ 1.5');
    expect(fmtBand([3, null])).toBe('≥ 3');
    expect(fmtBand([-1, 1])).toBe('-1 … 1');
  });
  it('uses the dictionary decimals and shows raw non-numeric values', () => {
    expect(fmtChannel({ value: 175.456, decimals: 1 })).toBe('175.5');
    expect(fmtChannel({ value: 'FLOAT', decimals: null })).toBe('FLOAT');
    expect(fmtChannel({ value: null, decimals: 2 })).toBe('---');
  });
});
