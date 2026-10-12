import type { Estimate, EstStatus, GroundFrame } from './api';

/** Status colours: green measured/fused, amber predicted, blue reconstructed, grey unknown (text always shown). */
export const STATUS_STYLE: Record<EstStatus, { cls: string; short: string }> = {
  MEASURED: { cls: 'border-pass/40 bg-pass/10 text-pass', short: 'MEAS' },
  FUSED: { cls: 'border-pass/40 bg-pass/10 text-pass', short: 'FUSED' },
  PREDICTED: { cls: 'border-hold/50 bg-hold/10 text-hold', short: 'PRED' },
  RECONSTRUCTED: { cls: 'border-info/50 bg-info/10 text-info', short: 'RECON' },
  UNKNOWN: { cls: 'border-line bg-panel2 text-muted', short: 'UNKN' },
};

export function StatusBadge({ status, title }: { status: EstStatus; title?: string }) {
  const s = STATUS_STYLE[status];
  return (
    <span className={`inline-flex shrink-0 items-center rounded border px-1 py-px text-[9.5px] font-semibold tracking-wide ${s.cls}`} title={title ?? status}>
      {s.short}
    </span>
  );
}

export interface Fmt {
  scale?: number;
  offset?: number;
  digits?: number;
  unit?: string;
}

export function formatEstimate(e: Estimate<any> | undefined, fmt: Fmt = {}): { text: string; unit: string } {
  if (!e) return { text: '--', unit: fmt.unit ?? '' };
  const v = e.value ?? null;
  const unit = fmt.unit ?? e.unit;
  if (v === null || v === undefined) {
    if (e.last_value !== undefined && typeof e.last_value === 'number') {
      return { text: `(${(e.last_value * (fmt.scale ?? 1) + (fmt.offset ?? 0)).toFixed(fmt.digits ?? 1)})`, unit };
    }
    return { text: '--', unit };
  }
  if (typeof v === 'boolean') return { text: v ? 'yes' : 'no', unit: '' };
  if (typeof v === 'number') return { text: (v * (fmt.scale ?? 1) + (fmt.offset ?? 0)).toFixed(fmt.digits ?? 1), unit };
  return { text: String(v), unit };
}

/** History of one estimate across frames up to the cursor (numbers only). */
export function history(frames: GroundFrame[], cursor: number, key: string, maxPoints = 120): { t: number; v: number; s: EstStatus }[] {
  const out: { t: number; v: number; s: EstStatus }[] = [];
  const start = Math.max(0, cursor - maxPoints + 1);
  for (let i = start; i <= cursor && i < frames.length; i++) {
    const e = frames[i].estimates[key];
    if (e && typeof e.value === 'number') out.push({ t: frames[i].t, v: e.value, s: e.status });
  }
  return out;
}

export function Spark({ pts, scale = 1, offset = 0 }: { pts: { t: number; v: number; s: EstStatus }[]; scale?: number; offset?: number }) {
  const W = 76;
  const H = 20;
  if (pts.length < 2) return <svg width={W} height={H} aria-hidden />;
  const vs = pts.map((p) => p.v * scale + offset);
  let lo = Math.min(...vs);
  let hi = Math.max(...vs);
  if (hi - lo < 1e-9) {
    hi += 1;
    lo -= 1;
  }
  const t0 = pts[0].t;
  const t1 = pts[pts.length - 1].t;
  const x = (t: number) => ((t - t0) / Math.max(t1 - t0, 1e-9)) * (W - 2) + 1;
  const y = (v: number) => H - 2 - ((v - lo) / (hi - lo)) * (H - 4);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(vs[i]).toFixed(1)}`).join('');
  const predicted = pts[pts.length - 1].s === 'PREDICTED';
  return (
    <svg width={W} height={H} aria-hidden className="shrink-0">
      <path d={d} fill="none" className={predicted ? 'stroke-hold' : 'stroke-info'} strokeWidth={1.2} strokeDasharray={predicted ? '3 2' : undefined} />
    </svg>
  );
}

export function fmtT(t: number | null | undefined): string {
  if (t === null || t === undefined) return '--';
  const s = Math.max(0, Math.floor(t));
  return `T+${String(Math.floor(s / 3600)).padStart(1, '0')}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
