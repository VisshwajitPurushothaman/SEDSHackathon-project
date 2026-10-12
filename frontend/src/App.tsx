import FlightPage from './ground/FlightPage';
import PreflightPage from './ground/PreflightPage';
import { EventsPage, OverviewPage, Planned } from './ground/OtherPages';
import { Shell, usePage } from './ground/Shell';

export default function App() {
  const page = usePage();
  return (
    <Shell page={page}>
      {page === 'flight' && <FlightPage />}
      {page === 'overview' && <OverviewPage />}
      {page === 'events' && <EventsPage />}
      {page === 'preflight' && <PreflightPage />}
      {page === 'trajectory' && (
        <Planned title="Trajectory" step="Step 5" items={['physics ensemble forecast and landing footprint', 'ML correction (evaluated, with fallback)', 'map with measured/reconstructed/forecast tracks and range zones']} />
      )}
      {page === 'postflight' && (
        <Planned title="Post-flight" step="Step 6" items={['recovery of LOS frames from Laptop A', 'live / reconstructed / truth histories', 'limit-violation, prediction and requirement reports', 'exports and closure checklist']} />
      )}
    </Shell>
  );
}
