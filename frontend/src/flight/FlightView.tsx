import { lazy, Suspense, useEffect } from 'react';
import { AlertsPanel, EventLogPanel } from './AlertsPanel';
import { FlightDock } from './FlightDock';
import { GatesPanel } from './GatesPanel';
import { PhaseTimeline } from './PhaseTimeline';
import { selectFrame, useFlight } from './store';
import { TelemetryGroups } from './TelemetryGroups';
import { fmtT } from './ui';

const MapPanel = lazy(() => import('./MapPanel'));

const Loading = ({ what }: { what: string }) => <div className="grid h-full place-items-center text-xs uppercase tracking-[0.18em] text-faint">Loading {what}…</div>;

/** Flight window: phase timeline, map, gates and alerts, telemetry, mission control. */
export default function FlightView() {
  const frame = useFlight(selectFrame);
  const loadConfig = useFlight((s) => s.loadConfig);
  const config = useFlight((s) => s.config);
  const missionId = useFlight((s) => s.missionId);
  const summary = useFlight((s) => s.summary);
  const live = useFlight((s) => s.live);
  const frames = useFlight((s) => s.frames.length);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2">
        <PhaseTimeline frame={frame} />
        <div className="ml-auto flex items-center gap-3 text-[11px] text-muted">
          {missionId ? (
            <>
              <span>
                mission <span className="num text-ink">{summary?.name ?? missionId}</span>
              </span>
              <span className="num">{frames} frames</span>
              <span className="num text-ink">{frame ? fmtT(frame.t) : '--'}</span>
              {!live && <span className="text-hold">replay</span>}
            </>
          ) : (
            <span>No mission attached: select a received mission.</span>
          )}
        </div>
      </div>

      {!config && (
        <div className="m-3 rounded-panel border border-hold/50 bg-hold/10 p-3 text-sm text-hold">
          The flight window needs the backend (GET /api/config failed). Start it with <span className="num">python backend.py</span>.
        </div>
      )}

      <main className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[340px_minmax(0,1fr)_380px] lg:overflow-hidden">
        <div className="order-2 flex min-h-0 flex-col gap-3 lg:order-1">
          <div className="flex min-h-0 flex-[3] flex-col">
            <GatesPanel frame={frame} />
          </div>
          <div className="flex min-h-0 flex-[2] flex-col">
            <AlertsPanel frame={frame} />
          </div>
        </div>

        <div className="order-1 flex min-h-[560px] flex-col gap-3 lg:order-2 lg:min-h-0">
          <section className="relative min-h-[420px] flex-1" aria-label="Map">
            <Suspense fallback={<Loading what="map" />}>
              <MapPanel frame={frame} />
            </Suspense>
          </section>
        </div>

        <section className="panel order-3 flex min-h-0 flex-col" aria-labelledby="flight-telemetry-title">
          <div className="border-b border-line px-3 py-2.5">
            <h2 id="flight-telemetry-title" className="panel-title">
              Flight telemetry
            </h2>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
            <TelemetryGroups frame={frame} />
            <EventLogPanel />
          </div>
        </section>
      </main>
      <div className="sticky bottom-0 z-30">
        <FlightDock />
      </div>
    </div>
  );
}
