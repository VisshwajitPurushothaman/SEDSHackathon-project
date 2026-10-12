import type { ChannelRow, EstStatus, LimitState } from './api';

/** Mission elapsed time as T+HH:MM:SS. */
export function fmtMET(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return 'T+--:--:--';
  const sign = s < 0 ? '-' : '+';
  const a = Math.floor(Math.abs(s));
  const h = Math.floor(a / 3600);
  const m = Math.floor((a % 3600) / 60);
  const sec = a % 60;
  return `T${sign}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

export function fmtUTC(utc: number | null | undefined, withDate = false): string {
  if (utc === null || utc === undefined) return '--:--:--';
  const d = new Date(utc * 1000).toISOString();
  return withDate ? `${d.slice(0, 10)} ${d.slice(11, 19)}Z` : `${d.slice(11, 19)}Z`;
}

export function fmtNum(v: unknown, decimals = 2): string {
  if (typeof v === 'number' && Number.isFinite(v)) return v.toFixed(decimals);
  if (typeof v === 'boolean') return v ? '1' : '0';
  if (v === null || v === undefined) return '---';
  return String(v);
}

export function fmtChannel(r: Pick<ChannelRow, 'value' | 'decimals'>): string {
  return typeof r.value === 'number' ? fmtNum(r.value, r.decimals ?? 2) : fmtNum(r.value);
}

export function fmtAge(age: number | null | undefined): string {
  if (age === null || age === undefined) return '---';
  if (age < 10) return `${age.toFixed(1)} s`;
  if (age < 600) return `${age.toFixed(0)} s`;
  return `${(age / 60).toFixed(0)} min`;
}

export function fmtBand(b: [number | null, number | null] | undefined): string {
  if (!b) return '';
  const [lo, hi] = b;
  if (lo === null && hi === null) return '';
  if (lo === null) return `≤ ${hi}`;
  if (hi === null) return `≥ ${lo}`;
  return `${lo} … ${hi}`;
}

export const LIMIT_TAG: Record<LimitState, { cls: string; text: string }> = {
  NOMINAL: { cls: 'tag-nom', text: 'NOM' },
  CAUTION: { cls: 'tag-cau', text: 'YEL' },
  ALARM: { cls: 'tag-alm', text: 'RED' },
  NO_LIMIT: { cls: 'tag-stale', text: '—' },
};

export const STATUS_TAG: Record<EstStatus | 'STALE', string> = {
  MEASURED: 'tag-nom',
  FUSED: 'tag-fused',
  PREDICTED: 'tag-pred',
  RECONSTRUCTED: 'tag-pred',
  UNKNOWN: 'tag-stale',
  STALE: 'tag-stale',
};
