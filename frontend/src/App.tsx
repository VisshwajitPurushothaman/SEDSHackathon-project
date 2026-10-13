import FlightPage from './ground/FlightPage';
import PreflightPage from './ground/PreflightPage';
import PostflightPage from './ground/PostflightPage';
import TrajectoryPage from './ground/TrajectoryPage';
import { OverviewPage } from './ground/OverviewPage';
import { EventsPage } from './ground/OtherPages';
import { Shell, usePage } from './ground/Shell';

export default function App() {
  const page = usePage();
  return (
    <Shell page={page}>
      {page === 'overview' && <OverviewPage />}
      {page === 'preflight' && <PreflightPage />}
      {page === 'flight' && <FlightPage />}
      {page === 'trajectory' && <TrajectoryPage />}
      {page === 'postflight' && <PostflightPage />}
      {page === 'events' && <EventsPage />}
    </Shell>
  );
}
