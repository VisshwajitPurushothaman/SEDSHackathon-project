/** Read telemetry for display from the ONE pipeline (channel table + fused estimates). No defaults: absent = null. */
import type { ChannelRow, Estimate, Snapshot } from './api';

export interface Reading {
  value: number | null;
  status: string;
  age_s: number | null;
  units: string;
}

export function chan(snap: Snapshot | null | undefined, m: string): Reading {
  const c: ChannelRow | undefined = snap?.channels?.find((r) => r.mnemonic === m);
  const v = c?.value;
  return { value: typeof v === 'number' && Number.isFinite(v) ? v : null, status: c?.status ?? 'UNKNOWN', age_s: c?.age_s ?? null, units: c?.units ?? '' };
}

export function chanText(snap: Snapshot | null | undefined, m: string): string | null {
  const v = snap?.channels?.find((r) => r.mnemonic === m)?.value;
  return typeof v === 'string' ? v : v === null || v === undefined ? null : String(v);
}

export function est(snap: Snapshot | null | undefined, k: string): Reading {
  const e: Estimate | undefined = snap?.ground?.estimates?.[k];
  const v = e?.value;
  return { value: typeof v === 'number' && Number.isFinite(v) ? v : null, status: e?.status ?? 'UNKNOWN', age_s: e?.age_s ?? null, units: e?.unit ?? '' };
}

export const fmt = (r: Reading | number | null, dec = 1, unit = ''): string => {
  const v = typeof r === 'number' || r === null ? r : r.value;
  return v === null ? '---' : `${v.toFixed(dec)}${unit ? ` ${unit}` : ''}`;
};
