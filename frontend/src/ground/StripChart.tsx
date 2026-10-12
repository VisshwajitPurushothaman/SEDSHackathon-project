import { useEffect, useRef } from 'react';
import uPlot, { type AlignedData, type Options } from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { ui } from '../theme/tokens';
import type { Limits, LosPeriod } from './api';

export interface StripSeries {
  label: string;
  values: (number | null)[];
  color: string;
  /** points only (raw measurements) vs a line (fused) */
  points?: boolean;
  dash?: number[];
}

export interface StripProps {
  title: string;
  unit: string;
  x: number[];
  series: StripSeries[];
  limits?: Limits | null;
  los?: LosPeriod[];
  /** predicted tolerance band (lo, hi) aligned with x */
  band?: { lo: (number | null)[]; hi: (number | null)[] } | null;
  decimals?: number;
  height?: number;
}

function hline(u: uPlot, y: number, color: string, dash: number[]) {
  const ctx = u.ctx;
  const py = u.valToPos(y, 'y', true);
  if (py < u.bbox.top || py > u.bbox.top + u.bbox.height) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1 * devicePixelRatio;
  ctx.setLineDash(dash.map((d) => d * devicePixelRatio));
  ctx.beginPath();
  ctx.moveTo(u.bbox.left, py);
  ctx.lineTo(u.bbox.left + u.bbox.width, py);
  ctx.stroke();
  ctx.restore();
}

/** Real-time strip chart: measured points, fused line, yellow/red limit lines, LOS shading, predicted band. */
export function StripChart({ title, unit, x, series, limits, los, band, decimals = 1, height = 150 }: StripProps) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const extra = useRef({ limits, los, band });
  extra.current = { limits, los, band };
  const key = series.map((s) => s.label).join('|') + (band ? '|band' : '');

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const bandSeries = band
      ? [
          { label: 'pred lo', stroke: ui.info, width: 0, points: { show: false } },
          { label: 'pred hi', stroke: ui.info, width: 0, points: { show: false } },
        ]
      : [];
    const opts: Options = {
      width: el.clientWidth || 400,
      height,
      legend: { show: true, live: true },
      cursor: { drag: { x: true, y: false }, points: { size: 5 } },
      scales: { x: { time: false } },
      axes: [
        {
          stroke: ui.muted,
          grid: { stroke: ui.border, width: 1 },
          ticks: { stroke: ui.border, width: 1 },
          values: (_u, ticks) => ticks.map((t) => `${Math.floor(t / 60)}m`),
          size: 26,
        },
        {
          stroke: ui.muted,
          grid: { stroke: ui.border, width: 1 },
          ticks: { stroke: ui.border, width: 1 },
          values: (_u, ticks) => ticks.map((t) => (Math.abs(t) >= 1000 ? `${(t / 1000).toFixed(1)}k` : t.toFixed(Math.max(0, decimals - 1)))),
          size: 48,
        },
      ],
      series: [
        { label: 'MET s', value: (_u, v) => (v === null ? '--' : v.toFixed(0)) },
        ...series.map((s) => ({
          label: s.label,
          stroke: s.color,
          width: s.points ? 0 : 1.5,
          dash: s.dash,
          points: s.points ? { show: true, size: 2.5, stroke: s.color, fill: s.color } : { show: false },
          value: (_u: uPlot, v: number | null) => (v === null ? '--' : v.toFixed(decimals)),
          spanGaps: false,
        })),
        ...bandSeries,
      ],
      bands: band ? [{ series: [series.length + 2, series.length + 1], fill: `${ui.info}26` }] : undefined,
      hooks: {
        drawClear: [
          (u) => {
            const { los: periods } = extra.current;
            if (!periods?.length) return;
            const ctx = u.ctx;
            ctx.save();
            ctx.fillStyle = `${ui.stale}2E`;
            for (const p of periods) {
              const a = u.valToPos(p.start_met, 'x', true);
              const b = u.valToPos(p.end_met ?? (u.scales.x.max ?? p.start_met), 'x', true);
              const l = Math.max(a, u.bbox.left);
              const r = Math.min(b, u.bbox.left + u.bbox.width);
              if (r > l) ctx.fillRect(l, u.bbox.top, r - l, u.bbox.height);
            }
            ctx.restore();
          },
        ],
        draw: [
          (u) => {
            const lim = extra.current.limits;
            if (!lim) return;
            for (const [band_, color] of [
              [lim.yellow, ui.hold],
              [lim.red, ui.fail],
            ] as const) {
              if (!band_) continue;
              for (const v of band_) if (v !== null) hline(u, v, color, [5, 4]);
            }
          },
        ],
      },
    };
    const p = new uPlot(opts, [[]] as unknown as AlignedData, el);
    plot.current = p;
    const ro = new ResizeObserver(() => p.setSize({ width: el.clientWidth, height }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      p.destroy();
      plot.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, height, decimals]);

  useEffect(() => {
    const p = plot.current;
    if (!p) return;
    const data = [x, ...series.map((s) => s.values), ...(band ? [band.lo, band.hi] : [])] as unknown as AlignedData;
    p.setData(data, true);
  }, [x, series, band, limits, los]);

  return (
    <figure className="panel flex min-w-0 flex-col" aria-label={`${title} strip chart`}>
      <figcaption className="panel-head py-1.5">
        <span className="panel-title">{title}</span>
        <span className="unit text-[11px]">{unit}</span>
        {limits && (
          <span className="ml-auto flex items-center gap-2 text-[10.5px] text-muted">
            <span className="flex items-center gap-1">
              <span className="inline-block h-0 w-3 border-t border-dashed border-hold" aria-hidden /> yellow
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-0 w-3 border-t border-dashed border-fail" aria-hidden /> red
            </span>
          </span>
        )}
      </figcaption>
      <div ref={host} className="min-w-0 px-1 pb-1" />
    </figure>
  );
}
