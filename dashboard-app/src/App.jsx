import { Suspense, lazy, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, ExternalLink } from 'lucide-react';
import FilterBar from '@/components/FilterBar';
import HeroStats, { HeroSkeleton } from '@/components/HeroStats';
import Insights, { LockedPanels } from '@/components/Insights';
import TradeTable from '@/components/TradeTable';
import { Card, ErrorState, Skeleton, TooltipProvider } from '@/components/ui';
import { fetchHistory } from '@/lib/api';

// Charts and grids are the heaviest chunks and all sit below the fold; the
// hero row and coach paint without waiting for them.
const Charts = lazy(() => import('@/components/Charts.jsx').then(m => ({ default: m.ChartsSection })));
const Sections = lazy(() => import('@/components/Sections.jsx'));

const DEFAULT_FILTERS = { period: '30', limit: 100, offset: 0 };

function ChartFallback({ height = 320 }) {
  return <Card className="mb-5"><Skeleton className="h-4 w-32" />
    <Skeleton className="mt-4 w-full" style={{ height }} /></Card>;
}

export default function App() {
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
              <h1 className="text-[15px] font-semibold tracking-tight">Trading analytics</h1>
              <p className="text-xs text-muted">
                Read-only · all times UTC
                {data?.generated_at && ` · updated ${data.generated_at.slice(11, 19)}`}
              </p>
            </div>
          </div>
          <a href="/" className="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink">
            Live status <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </header>

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
              <Sections data={data} />
            </Suspense>

            <TradeTable
              trades={data.trades} page={data.page}
              onPage={offset => setFilters(prev => ({ ...prev, offset }))}
            />

            <LockedPanels coverage={data.coverage} />

            <footer className="pb-8 pt-2 text-xs text-muted">
              Read-only. This page cannot place, modify or close a position.
              Statistics exclude open trades. Cells and bars below the reliability
              threshold are faded — treat them as noise, not signal.
            </footer>
          </>
        )}
      </div>
    </TooltipProvider>
  );
}
