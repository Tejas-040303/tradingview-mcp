import { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, BarChart3, Bot, FlaskConical, Gauge } from 'lucide-react';
import FilterBar from '@/components/FilterBar';
import HeroStats, { HeroSkeleton } from '@/components/HeroStats';
import Insights, { LockedPanels } from '@/components/Insights';
import StatusView from '@/components/StatusView';
import TradeTable from '@/components/TradeTable';
import { Card, ErrorState, Skeleton, TooltipProvider } from '@/components/ui';
import { cn, fetchHistory } from '@/lib/api';

// Charts and grids are the heaviest chunks and all sit below the fold; the
// hero row and coach paint without waiting for them.
const Charts = lazy(() => import('@/components/Charts.jsx').then(m => ({ default: m.ChartsSection })));
const Sections = lazy(() => import('@/components/Sections.jsx'));
// The Bot tab pulls three strategy routes and is not on the first paint path.
const BotView = lazy(() => import('@/components/BotView.jsx'));
const ResearchView = lazy(() => import('@/components/ResearchView.jsx'));

const DEFAULT_FILTERS = { period: '30', limit: 100, offset: 0 };

const VIEWS = [
  { id: 'status', label: 'Status', icon: Gauge, hint: 'Live account · polls every 5s' },
  { id: 'analytics', label: 'Analytics', icon: BarChart3, hint: 'Closed trade history' },
  { id: 'bot', label: 'Bot', icon: Bot, hint: 'Simulated strategy · read-only' },
  { id: 'research', label: 'Research', icon: FlaskConical, hint: 'Sweeps and reconciliation' },
];

const VIEW_IDS = VIEWS.map(v => v.id);

/**
 * The hash is the router: one URL, no navigation, but a refresh or a bookmark
 * still lands on the right view.
 *
 * Precedence is hash, then the last view used, then Status — which is what /
 * showed before the two pages merged, and the cheaper first paint besides.
 */
function useHashView() {
  const read = () => {
    const fromHash = window.location.hash.replace('#', '');
    if (VIEW_IDS.includes(fromHash)) return fromHash;
    const remembered = localStorage.getItem('mt5-view');
    return VIEW_IDS.includes(remembered) ? remembered : 'status';
  };
  const [view, setView] = useState(read);

  useEffect(() => {
    const sync = () => setView(read());
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);

  useEffect(() => { localStorage.setItem('mt5-view', view); }, [view]);

  const go = useCallback(next => {
    // Replace rather than push, so Back leaves the dashboard instead of walking
    // through every tab the user happened to click.
    window.history.replaceState(null, '', `#${next}`);
    setView(next);
  }, []);

  return [view, go];
}

function ChartFallback({ height = 320 }) {
  return <Card className="mb-5"><Skeleton className="h-4 w-32" />
    <Skeleton className="mt-4 w-full" style={{ height }} /></Card>;
}

export default function App() {
  const [view, setView] = useHashView();
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [theme, setTheme] = useState(
    () => localStorage.getItem('mt5-theme')
      || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    localStorage.setItem('mt5-theme', theme);
  }, [theme]);

  const query = useQuery({
    queryKey: ['history', filters],
    queryFn: ({ signal }) => fetchHistory(filters, signal),
    // Only fetch history while its tab is showing; the status tab polls
    // independently and should not drag a full analytics payload with it.
    enabled: view === 'analytics',
    // Keeping the previous page visible while the next loads stops every panel
    // collapsing to a skeleton on a filter tweak.
    placeholderData: previous => previous,
    retry: 1,
  });

  const { data, error, isPending, isFetching, refetch } = query;

  return (
    <TooltipProvider delayDuration={120}>
      <div className="mx-auto min-h-screen max-w-[1600px] px-4 py-4 sm:px-6">
        <header className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-accent/15">
              <Activity className="h-4 w-4 text-accent" />
            </div>
            <div>
              <h1 className="text-[15px] font-semibold tracking-tight">MT5 dashboard</h1>
              <p className="text-xs text-muted">
                Read-only · all times UTC
                {view === 'analytics' && data?.generated_at
                  && ` · updated ${data.generated_at.slice(11, 19)}`}
              </p>
            </div>
          </div>

          <nav className="flex gap-1 rounded-xl border border-line bg-surface p-1" aria-label="Views">
            {VIEWS.map(v => (
              <button key={v.id} onClick={() => setView(v.id)} title={v.hint}
                aria-current={view === v.id ? 'page' : undefined}
                className={cn('inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5',
                  'text-[13px] font-medium transition-colors',
                  view === v.id
                    ? 'bg-elevated text-ink shadow-card'
                    : 'text-muted hover:text-ink')}>
                <v.icon className="h-3.5 w-3.5" />
                {v.label}
              </button>
            ))}
          </nav>
        </header>

        {view === 'status' && <StatusView />}

        {view === 'bot' && (
          <Suspense fallback={<Card><Skeleton className="h-64 w-full" /></Card>}>
            <BotView />
          </Suspense>
        )}

        {view === 'research' && (
          <Suspense fallback={<Card><Skeleton className="h-64 w-full" /></Card>}>
            <ResearchView />
          </Suspense>
        )}

        {view === 'analytics' && (
        <>
        <FilterBar
          filters={filters} setFilters={setFilters} facets={data?.facets}
          matched={data?.page?.total ?? 0} total={data?.total_trades ?? 0}
          isFetching={isFetching} onRefresh={() => refetch()}
          theme={theme} setTheme={setTheme}
        />

        {error && !data && (
          <Card className="mb-5">
            <ErrorState error={error} onRetry={() => refetch()} />
            <p className="mt-2 text-xs text-muted">
              The page is served by bridge.py, so this usually means MetaTrader 5 is
              not connected rather than the bridge being down.
            </p>
          </Card>
        )}

        {isPending && !data ? <HeroSkeleton /> : <HeroStats data={data} />}

        {data && (
          <>
            <Insights insights={data.insights} coverage={data.coverage} />

            <Suspense fallback={<ChartFallback />}>
              <Charts data={data} />
            </Suspense>

            <Suspense fallback={<ChartFallback height={240} />}>
              <Sections
                data={data}
                excursionsEnabled={Boolean(filters.excursions)}
                onEnableExcursions={() =>
                  setFilters(prev => ({ ...prev, excursions: true }))}
                isFetching={isFetching}
              />
            </Suspense>

            <TradeTable
              trades={data.trades} page={data.page}
              onPage={offset => setFilters(prev => ({ ...prev, offset }))}
            />

            <LockedPanels coverage={data.coverage} />
          </>
        )}
        </>
        )}

        <footer className="pb-8 pt-2 text-xs text-muted">
          Read-only. This dashboard cannot place, modify or close a position.
          {view === 'analytics' && ' Statistics exclude open trades. Cells and bars '
            + 'below the reliability threshold are faded — treat them as noise, not signal.'}
        </footer>
      </div>
    </TooltipProvider>
  );
}
