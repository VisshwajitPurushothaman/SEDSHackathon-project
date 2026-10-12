import { Check } from 'lucide-react';
import type { GroundFrame } from './api';

export const PHASES = ['Pad', 'Ascent', 'Float', 'Gates', 'Commit', 'Rocket burn', 'Coast', 'Cut-down', 'Descent', 'Landed'];

/** Mission phases across the top: finished phases tick, the current one glows. */
export function PhaseTimeline({ frame }: { frame: GroundFrame | null }) {
  const idx = frame ? frame.phase_index : -1;
  return (
    <ol className="flex items-center gap-1 overflow-x-auto" aria-label="Mission phase">
      {PHASES.map((p, i) => {
        const done = i < idx;
        const current = i === idx;
        return (
          <li key={p} className="flex items-center gap-1">
            <span
              aria-current={current ? 'step' : undefined}
              className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11.5px] font-medium transition-colors duration-200 ${
                current
                  ? 'border-info/70 bg-info/15 text-ink shadow-[0_0_14px_rgba(106,168,255,0.35)]'
                  : done
                    ? 'border-pass/30 bg-pass/5 text-pass'
                    : 'border-line text-faint'
              }`}
            >
              {done && <Check size={11} aria-hidden />}
              {p}
              {current && <span className="sr-only">(current)</span>}
            </span>
            {i < PHASES.length - 1 && <span className={`h-px w-3 ${done ? 'bg-pass/40' : 'bg-line'}`} aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}
