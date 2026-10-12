import { Flame } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

const HOLD_MS = 1500;
const R = 22;
const C = 2 * Math.PI * R;

/**
 * Press and hold for 1.5 s to commit. Releasing early (pointer up/leave, key
 * up, blur) cancels and the ring drains. Works with mouse, touch, Space and Enter.
 */
export function HoldToCommit({ onCommit, disabled = false }: { onCommit: () => void; disabled?: boolean }) {
  const [progress, setProgress] = useState(0);
  const holding = useRef(false);
  const start = useRef(0);
  const raf = useRef(0);
  const done = useRef(false);

  const stop = () => {
    holding.current = false;
    cancelAnimationFrame(raf.current);
    if (!done.current) setProgress(0);
  };

  const begin = () => {
    if (disabled || holding.current || done.current) return;
    holding.current = true;
    start.current = performance.now();
    const step = (now: number) => {
      if (!holding.current) return;
      const p = Math.min(1, (now - start.current) / HOLD_MS);
      setProgress(p);
      if (p >= 1) {
        holding.current = false;
        done.current = true;
        onCommit();
        return;
      }
      raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
  };

  useEffect(() => () => cancelAnimationFrame(raf.current), []);

  const secondsLeft = ((1 - progress) * HOLD_MS) / 1000;

  return (
    <button
      type="button"
      disabled={disabled}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        begin();
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
      onLostPointerCapture={stop}
      onKeyDown={(e) => {
        if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) {
          e.preventDefault();
          begin();
        }
      }}
      onKeyUp={(e) => {
        if (e.key === ' ' || e.key === 'Enter') stop();
      }}
      onBlur={stop}
      onClick={(e) => e.preventDefault()}
      onContextMenu={(e) => e.preventDefault()}
      aria-label="Hold for 1.5 seconds to commit ignition"
      className="group flex select-none items-center gap-3 rounded-xl border border-armed/50 bg-armed/10 py-2 pl-2 pr-4 text-left transition-colors duration-200 ease-out hover:bg-armed/15 disabled:opacity-40"
    >
      <span className="relative grid h-12 w-12 place-items-center">
        <svg viewBox="0 0 52 52" className="absolute inset-0 -rotate-90" aria-hidden>
          <circle cx="26" cy="26" r={R} className="fill-none stroke-line" strokeWidth={3} />
          <circle
            cx="26"
            cy="26"
            r={R}
            className="fill-none stroke-armed"
            strokeWidth={3}
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={C * (1 - progress)}
          />
        </svg>
        <Flame size={18} className="text-armed" aria-hidden />
      </span>
      <span>
        <span className="block text-[13px] font-semibold text-ink">Hold to commit</span>
        <span className="num block text-[11px] text-muted">
          {progress > 0 && progress < 1 ? `${secondsLeft.toFixed(1)} s` : 'press & hold 1.5 s'}
        </span>
      </span>
      <span className="sr-only" aria-live="assertive">
        {progress > 0 && progress < 1 ? 'Holding' : ''}
      </span>
    </button>
  );
}
