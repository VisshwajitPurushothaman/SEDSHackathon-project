import { MotionConfig } from 'framer-motion';
import { Component, lazy, Suspense, type ReactNode } from 'react';
import { ChecklistPanel } from './components/ChecklistPanel';
import { GatePanel } from './components/GatePanel';
import { Hud } from './components/Hud';
import { LinkOverlay } from './components/LinkOverlay';
import { TelemetryPanel } from './components/TelemetryPanel';
import { TimelineDock } from './components/TimelineDock';
import { TopBar } from './components/TopBar';
import { useHealthPoll, useKeyboardShortcuts, usePlaybackLoop } from './hooks/useMissionRuntime';

// three.js is the heaviest dependency; load it after the 2D console has painted.
const SceneRoot = lazy(() => import('./scene/SceneRoot'));

class SceneBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed) {
      return (
        <div className="grid h-full place-items-center p-6 text-center text-sm text-muted">
          3D view unavailable (WebGL could not start). Checks, telemetry and the gate still work.
        </div>
      );
    }
    return this.props.children;
  }
}

function SceneFallback() {
  return <div className="grid h-full place-items-center text-xs uppercase tracking-[0.18em] text-faint">Loading 3D view…</div>;
}

export default function App() {
  usePlaybackLoop();
  useHealthPoll();
  useKeyboardShortcuts();

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex min-h-full flex-col lg:h-full">
        <TopBar />
        <main className="grid flex-1 gap-3 p-3 lg:min-h-0 lg:grid-cols-[360px_minmax(0,1fr)_380px] lg:overflow-hidden">
          <div className="order-2 flex min-h-0 flex-col lg:order-1">
            <ChecklistPanel />
          </div>

          <section
            className="relative order-1 h-[58vh] min-h-[380px] overflow-hidden rounded-panel border border-line bg-bg lg:order-2 lg:h-auto lg:min-h-0"
            aria-label="Vehicle view"
          >
            <SceneBoundary>
              <Suspense fallback={<SceneFallback />}>
                <SceneRoot />
              </Suspense>
            </SceneBoundary>
            <Hud />
            <div className="absolute inset-x-3 bottom-3 z-30">
              <GatePanel />
            </div>
          </section>

          <div className="order-3 flex min-h-0 flex-col">
            <TelemetryPanel />
          </div>
        </main>
        <div className="sticky bottom-0 z-30">
          <TimelineDock />
        </div>
      </div>
      <LinkOverlay />
    </MotionConfig>
  );
}
