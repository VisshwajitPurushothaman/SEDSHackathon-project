import FlightPage from './ground/FlightPage';
import PreflightPage from './ground/PreflightPage';
import TrajectoryPage from './ground/TrajectoryPage';
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
      {page === 'trajectory' && <TrajectoryPage />}
      {page === 'postflight' && (
        <Planned title="Post-flight" step="Step 6" items={['recovery of LOS frames from Laptop A', 'live / reconstructed / truth histories', 'limit-violation, prediction and requirement reports', 'exports and closure checklist']} />
      )}
    </Shell>
  );
}
