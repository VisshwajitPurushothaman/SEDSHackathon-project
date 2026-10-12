import { Video, VideoOff } from 'lucide-react';
import { derivePhase, type Phase } from '../lib/checks';
import { ascentRateAt } from '../lib/playback';
import { selectAtEnd, useCurrentFrame, useMission } from '../store/useMission';
import { InterpMark } from './ui';

const PHASE_TONE: Record<Phase, string> = {
  Standby: 'text-muted',
  Pad: 'text-ink',
  Ascent: 'text-info',
  'Release band': 'text-pass',
  Armed: 'text-armed',
  Aborted: 'text-fail',
  Terminated: 'text-muted',
};

/** Small heads-up overlay in the corner of the 3D view. */
export function Hud() {
  const f = useCurrentFrame();
  const frames = useMission((s) => s.frames);
  const playhead = useMission((s) => s.uiPlayhead);
  const atEnd = useMission(selectAtEnd);
  const verdict = useMission((s) => s.summary?.verdict ?? null);
  const committed = useMission((s) => s.commit.status === 'committed');
  const freeCamera = useMission((s) => s.freeCamera);
  const setFreeCamera = useMission((s) => s.setFreeCamera);
  const phase = derivePhase(f, atEnd, verdict);
  const rate = f ? ascentRateAt(frames, playhead) : null;

  return (
    <>
      <div className="pointer-events-none absolute left-3 top-3 z-30 rounded-lg border border-line/80 bg-bg/60 px-3 py-2 backdrop-blur-panel">
        <div className="text-[10px] uppercase tracking-[0.16em] text-muted">Phase</div>
        <div className={`font-display text-[15px] font-semibold ${committed && phase === 'Armed' ? 'text-armed' : PHASE_TONE[phase]}`} aria-live="polite">
          {committed && phase === 'Armed' ? 'Ignition (simulation)' : phase}
        </div>
        <dl className="mt-1 grid grid-cols-[auto_auto] gap-x-3 text-[11.5px]">
          <dt className="text-muted">Altitude</dt>
          <dd className="num text-right text-ink">
            {f ? (f.altitude_m / 1000).toFixed(2) : '--'} <span className="unit">km</span>
            {f && !f.exact && <InterpMark />}
          </dd>
          <dt className="text-muted" title="Derived from the bracketing backend frames">
            Ascent
          </dt>
          <dd className="num text-right text-ink" title="Derived from the bracketing backend frames">
            {rate === null ? '--' : rate.toFixed(0)} <span className="unit">m/s</span>
          </dd>
        </dl>
      </div>

      <button
        type="button"
        onClick={() => setFreeCamera(!freeCamera)}
        aria-pressed={freeCamera}
        className={`btn absolute right-3 top-3 z-30 bg-bg/60 text-xs backdrop-blur-panel ${freeCamera ? 'border-info/60 text-info' : ''}`}
        title={freeCamera ? 'Free camera on: guidance off, orbit freely' : 'Guided camera: eases to each part as its check changes. Click for free camera.'}
      >
        {freeCamera ? <VideoOff size={14} aria-hidden /> : <Video size={14} aria-hidden />}
        Free camera {freeCamera ? 'on' : 'off'}
      </button>
    </>
  );
}
