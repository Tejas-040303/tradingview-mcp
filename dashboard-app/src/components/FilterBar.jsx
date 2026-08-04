import { RotateCcw, RefreshCw, Sun, Moon } from 'lucide-react';
import { Button, Input, Select } from '@/components/ui';
import { PERIODS, cn } from '@/lib/api';

/**
 * Sticky filter bar.
 *
 * Every control writes into one filter object which becomes the query key, so
 * a change refetches and every panel below moves together. Nothing is filtered
 * client-side — see lib/api.js for why.
 */
export default function FilterBar({ filters, setFilters, facets, matched, total,
  isFetching, onRefresh, theme, setTheme }) {
  const set = patch => setFilters(prev => ({ ...prev, ...patch, offset: 0 }));

  const symbolOptions = [{ value: '', label: 'All symbols' },
    ...(facets?.symbols || []).map(s => ({ value: s, label: s }))];
  const exitOptions = [{ value: '', label: 'All exits' },
    ...(facets?.exit_reasons || []).map(s => ({ value: s, label: s }))];

  return (
    <div className="sticky top-0 z-40 -mx-4 mb-5 border-b border-line glass px-4 py-3 sm:-mx-6 sm:px-6">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={filters.period} onValueChange={v => set({ period: v })}
          options={PERIODS} className="w-[150px]" />

        {filters.period === 'custom' && (
          <>
            <Input type="date" value={filters.from || ''}
              onChange={e => set({ from: e.target.value })} aria-label="From date" />
            <Input type="date" value={filters.to || ''}
              onChange={e => set({ to: e.target.value })} aria-label="To date" />
          </>
        )}

        <Select value={filters.filter_symbol} onValueChange={v => set({ filter_symbol: v })}
          options={symbolOptions} placeholder="All symbols" className="w-[140px]" />
        <Select value={filters.direction} onValueChange={v => set({ direction: v })}
          options={[{ value: '', label: 'Both sides' },
            { value: 'long', label: 'Long' }, { value: 'short', label: 'Short' }]}
          placeholder="Both sides" className="w-[120px]" />
        <Select value={filters.exit_reason} onValueChange={v => set({ exit_reason: v })}
          options={exitOptions} placeholder="All exits" className="w-[140px]" />

        <Input type="number" step="0.01" placeholder="Net ≥" value={filters.min_net || ''}
          onChange={e => set({ min_net: e.target.value })} className="w-[92px]" aria-label="Minimum net" />
        <Input type="number" step="0.01" placeholder="Net ≤" value={filters.max_net || ''}
          onChange={e => set({ max_net: e.target.value })} className="w-[92px]" aria-label="Maximum net" />
        <Input type="number" step="0.01" placeholder="Start balance"
          value={filters.starting_balance || ''}
          onChange={e => set({ starting_balance: e.target.value })}
          className="w-[128px]" aria-label="Starting balance, unlocks drawdown percentages" />

        <label className="inline-flex select-none items-center gap-1.5 text-[13px] text-muted">
          <input type="checkbox" checked={Boolean(filters.closed_only)}
            onChange={e => set({ closed_only: e.target.checked })}
            className="h-3.5 w-3.5 rounded border-line accent-[rgb(var(--accent))]" />
          Closed only
        </label>

        <div className="ml-auto flex items-center gap-2">
          <span className="tabular text-xs text-muted">
            {matched === total ? `${total} trades` : `${matched} of ${total} trades`}
          </span>
          <Button variant="ghost" size="sm"
            onClick={() => setFilters({ period: '30', limit: filters.limit, offset: 0 })}
            title="Reset filters">
            <RotateCcw className="h-3.5 w-3.5" /> Reset
          </Button>
          <Button variant="ghost" size="sm" onClick={onRefresh} title="Refresh">
            <RefreshCw className={cn('h-3.5 w-3.5', isFetching && 'animate-spin')} />
          </Button>
          <Button variant="ghost" size="sm" title="Toggle theme"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
            {theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </Button>
        </div>
      </div>
    </div>
  );
}
