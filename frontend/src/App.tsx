import { MotionConfig } from 'framer-motion';
import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { ChecklistPanel } from './components/ChecklistPanel';
import { GatePanel } from './components/GatePanel';
import { Hud } from './components/Hud';
import { LinkOverlay } from './components/LinkOverlay';
import { TelemetryPanel } from './components/TelemetryPanel';
import { TimelineDock } from './components/TimelineDock';
import { TopBar, type View } from './components/TopBar';
import { useHealthPoll, useKeyboardShortcuts, usePlaybackLoop } from './hooks/useMissionRuntime';

// three.js is the heaviest dependency; load it after the 2D console has painted.
const SceneRoot = lazy(() => import('./scene/SceneRoot'));
const FlightView = lazy(() => import('./flight/FlightView'));

const viewFromHash = (): View => (location.hash.startsWith('#/flight') ? 'flight' : location.hash.startsWith('#/postflight') ? 'postflight' : 'preflight');

function useView(): View {
  const [view, setView] = useState<View>(viewFromHash);
  useEffect(() => {
    const on = () => setView(viewFromHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return view;
}

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

function PreflightShortcuts() {
  useKeyboardShortcuts();
  return null;
}

export default function App() {
  usePlaybackLoop();
  useHealthPoll();
  const view = useView();

  if (view !== 'preflight') {
    return (
      <MotionConfig reducedMotion="user">
        <div className="flex min-h-full flex-col lg:h-full">
          <TopBar view={view} />
          <Suspense fallback={<SceneFallback />}>
            <FlightView />
          </Suspense>
        </div>
      </MotionConfig>
    );
  }

  return (
    <MotionConfig reducedMotion="user">
      <PreflightShortcuts />
      <div className="flex min-h-full flex-col lg:h-full">
        <TopBar view={view} />
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
