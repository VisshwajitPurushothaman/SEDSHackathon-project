import { lazy, Suspense, useEffect, useState } from 'react';

const FlightView = lazy(() => import('./flight/FlightView'));
const PostflightView = lazy(() => import('./postflight/PostflightView'));

type View = 'flight' | 'postflight';
const viewFromHash = (): View => (location.hash.startsWith('#/postflight') ? 'postflight' : 'flight');

function useView(): View {
  const [view, setView] = useState<View>(viewFromHash);
  useEffect(() => {
    const on = () => setView(viewFromHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return view;
}

const TABS: { id: View; label: string; href: string }[] = [
  { id: 'flight', label: 'Flight', href: '#/flight' },
  { id: 'postflight', label: 'Post-flight', href: '#/postflight' },
];

export default function App() {
  const view = useView();
  return (
    <div className="flex min-h-full flex-col lg:h-full">
      <header className="flex items-center gap-4 border-b border-line px-4 py-2">
        <span className="font-display text-sm font-semibold tracking-wide text-ink">AERO-FIRE Ground Station</span>
        <nav className="seg" aria-label="Windows">
          {TABS.map((t) => (
            <a key={t.id} href={t.href} aria-current={view === t.id ? 'page' : undefined} aria-pressed={view === t.id}>
              {t.label}
            </a>
          ))}
        </nav>
      </header>
      <Suspense fallback={<div className="grid flex-1 place-items-center text-xs text-faint">Loading…</div>}>
        {view === 'flight' ? <FlightView /> : <PostflightView />}
      </Suspense>
    </div>
  );
}
