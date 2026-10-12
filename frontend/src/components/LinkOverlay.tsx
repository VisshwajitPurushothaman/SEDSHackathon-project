import { useEffect, useRef } from 'react';
import { CHECK_DEFS, deriveChecks, type CheckId } from '../lib/checks';
import { screenAnchor } from '../scene/shared';
import { useCurrentFrame, useMission } from '../store/useMission';
import { STATE_TEXT, StatePill } from './ui';

/** Tooltip shown next to a 3D part while it is hovered in the scene. */
function PartTooltip({ id }: { id: CheckId | 'gate' }) {
  const f = useCurrentFrame();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let raf = 0;
    const loop = () => {
      if (box.current) {
        box.current.style.transform = `translate(${screenAnchor.x + 14}px, ${screenAnchor.y - 14}px)`;
        box.current.style.opacity = screenAnchor.visible ? '1' : '0';
      }
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => cancelAnimationFrame(raf);
  }, []);

  let title: string;
  let detail: string;
  let pill: React.ReactNode = null;
  if (id === 'gate') {
    title = 'Launch gate (nose light)';
    detail = f ? `gate_status ${f.gate_status}${f.is_abort ? ' · ABORT' : ''}` : 'no telemetry';
  } else {
    const r = deriveChecks(f)[id];
    title = CHECK_DEFS[id].name;
    detail = `${r.note} · rule: ${CHECK_DEFS[id].rule}`;
    pill = <StatePill state={r.state} />;
  }

  return (
    <div ref={box} className="fixed left-0 top-0 z-50 max-w-[260px] rounded-lg border border-line bg-panel/95 px-2.5 py-2 shadow-xl" role="tooltip">
      <div className="flex items-center gap-2">
        <span className={`text-[12.5px] font-medium ${id === 'gate' ? 'text-ink' : STATE_TEXT[deriveChecks(f)[id].state]}`}>{title}</span>
        {pill}
      </div>
      <div className="mt-0.5 text-[11px] leading-snug text-muted">{detail}</div>
    </div>
  );
}

/**
 * Links 2D and 3D: a leader line from the hovered check card to its part
 * (projected by the scene each frame), plus a tooltip when the hover started in 3D.
 */
export function LinkOverlay() {
  const hovered = useMission((s) => s.hovered);
  const hoverFrom = useMission((s) => s.hoverFrom);
  const path = useRef<SVGPathElement>(null);
  const dot = useRef<SVGCircleElement>(null);

  useEffect(() => {
    if (!hovered) return;
    let raf = 0;
    const loop = () => {
      const card = document.querySelector<HTMLElement>(`[data-check-card="${hovered}"]`);
      const r = card?.getBoundingClientRect();
      const ok = screenAnchor.visible && r && r.width > 0;
      if (path.current && dot.current) {
        if (ok && r) {
          const fromRight = r.right < screenAnchor.x;
          const x0 = fromRight ? r.right : r.left;
          const y0 = r.top + r.height / 2;
          const xm = x0 + (fromRight ? 24 : -24);
          path.current.setAttribute('d', `M${x0},${y0} L${xm},${y0} L${screenAnchor.x},${screenAnchor.y}`);
          path.current.style.opacity = '1';
        } else {
          path.current.style.opacity = '0';
        }
        dot.current.setAttribute('cx', String(screenAnchor.x));
        dot.current.setAttribute('cy', String(screenAnchor.y));
        dot.current.style.opacity = screenAnchor.visible ? '1' : '0';
      }
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => cancelAnimationFrame(raf);
  }, [hovered]);

  if (!hovered) return null;
  return (
    <>
      <svg className="pointer-events-none fixed inset-0 z-40 h-full w-full" aria-hidden>
        <path ref={path} className="fill-none stroke-info" strokeWidth={1.25} strokeDasharray="3 3" style={{ opacity: 0 }} />
        <circle ref={dot} r={4} className="fill-none stroke-info" strokeWidth={1.5} style={{ opacity: 0 }} />
      </svg>
      {hoverFrom === 'scene' && <PartTooltip id={hovered} />}
    </>
  );
}
