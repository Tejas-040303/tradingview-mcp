import { useMemo } from 'react';
import { Card, CardTitle, Empty, Tip } from '@/components/ui';
import { money, pct, shortDate } from '@/lib/format';
import { cn } from '@/lib/api';

/**
 * Shared colour scale for every grid on the page.
 *
 * Intensity is scaled against the largest absolute value present, so a single
 * huge day cannot wash out the rest. Cells below the reliability threshold are
 * deliberately drained of colour — the entire failure mode of a heatmap is that
 * a deep green cell built on two trades looks identical to one built on two
 * hundred.
 */
function cellStyle(value, peak, reliable = true) {
  if (value === null || value === undefined) return { background: 'transparent' };
  const intensity = peak ? Math.min(1, Math.abs(value) / peak) : 0;
  const alpha = reliable ? 0.12 + intensity * 0.68 : 0.06 + intensity * 0.12;
  const colour = value >= 0 ? 'var(--up)' : 'var(--down)';
  return { background: `rgb(${colour} / ${alpha})` };
}

/** GitHub-style contribution calendar, one square per UTC trading day. */
export function CalendarHeatmap({ daily }) {
  const { weeks, peak, months } = useMemo(() => {
    if (!daily?.length) return { weeks: [], peak: 0, months: [] };

    const byDate = new Map(daily.map(d => [d.date, d]));
    const first = new Date(`${daily[0].date}T00:00:00Z`);
    const last = new Date(`${daily[daily.length - 1].date}T00:00:00Z`);
    // Start on the Monday of the first week so columns line up with weekdays.
    const start = new Date(first);
    start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));

    const cols = [];
    const labels = [];
    let cursor = new Date(start);
    let lastMonth = null;
    while (cursor <= last) {
      const col = [];
      const monthOfCol = cursor.getUTCMonth();
      for (let d = 0; d < 7; d += 1) {
        const iso = cursor.toISOString().slice(0, 10);
        col.push(cursor > last ? null : { date: iso, ...(byDate.get(iso) || null) });
        cursor.setUTCDate(cursor.getUTCDate() + 1);
      }
      if (monthOfCol !== lastMonth) {
        labels.push({ index: cols.length,
          label: new Date(Date.UTC(2020, monthOfCol, 1)).toLocaleString('en', { month: 'short' }) });
        lastMonth = monthOfCol;
      }
      cols.push(col);
    }
    return {
      weeks: cols,
      peak: Math.max(...daily.map(d => Math.abs(d.net)), 0),
      months: labels,
    };
  }, [daily]);

  if (!weeks.length) {
    return <Card className="mb-5"><CardTitle>Trading calendar</CardTitle><Empty /></Card>;
  }

  return (
    <Card className="mb-5">
      <CardTitle hint="One square per UTC day. Empty squares are days you did not trade — not break-even days.">
        Trading calendar
      </CardTitle>
      <div className="overflow-x-auto pb-1">
        <div className="inline-block min-w-full">
          <div className="mb-1 flex gap-[3px] pl-8 text-[10px] text-muted">
            {weeks.map((_, i) => {
              const label = months.find(m => m.index === i);
              return <div key={i} className="w-[13px] shrink-0">{label?.label || ''}</div>;
            })}
          </div>
          <div className="flex gap-[3px]">
            <div className="flex w-7 shrink-0 flex-col gap-[3px] text-[10px] text-muted">
              {['Mon', '', 'Wed', '', 'Fri', '', 'Sun'].map((d, i) => (
                <div key={i} className="h-[13px] leading-[13px]">{d}</div>
              ))}
            </div>
            {weeks.map((col, ci) => (
              <div key={ci} className="flex shrink-0 flex-col gap-[3px]">
                {col.map((day, di) => {
                  if (!day) return <div key={di} className="h-[13px] w-[13px]" />;
                  const traded = day.trades !== undefined;
                  const square = (
                    <div
                      className={cn('h-[13px] w-[13px] rounded-[3px] border border-line/60',
                        traded && 'cursor-pointer hover:ring-1 hover:ring-accent')}
                      style={traded ? cellStyle(day.net, peak) : undefined}
                    />
                  );
                  return traded ? (
                    <Tip key={di} content={
                      <div className="tabular space-y-0.5">
                        <div className="font-medium">{shortDate(day.date)}</div>
                        <div>{day.trades} trades · {pct(day.win_rate_pct)} win</div>
                        <div className={day.net >= 0 ? 'text-up' : 'text-down'}>{money(day.net)}</div>
                      </div>
                    }>{square}</Tip>
                  ) : <div key={di}>{square}</div>;
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

/** One two-dimensional performance grid. */
export function HeatmapGrid({ spec, grid }) {
  if (grid?.error) {
    return (
      <Card><CardTitle>{spec}</CardTitle>
        <p className="text-xs text-down">{grid.error}</p></Card>
    );
  }
  if (!grid?.row_labels?.length) {
    return <Card><CardTitle>{spec.replace(':', ' × ')}</CardTitle><Empty /></Card>;
  }

  const peak = Math.max(
    ...grid.grid.flat().filter(Boolean).map(c => Math.abs(c.net)), 0);
  const thin = grid.grid.flat().filter(c => c && !c.reliable).length;
  const total = grid.grid.flat().filter(Boolean).length;

  return (
    <Card>
      <CardTitle hint={thin
        ? `${thin} of ${total} cells fall below ${grid.min_reliable} trades and are shown faded — treat them as noise.`
        : 'Every populated cell clears the reliability threshold.'}>
        {grid.rows} × {grid.cols}
      </CardTitle>
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-[2px] text-[11px]">
          <thead>
            <tr>
              <th className="sticky left-0 bg-surface" />
              {grid.col_labels.map(c => (
                <th key={c} className="px-1 pb-1 font-medium text-muted">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.row_labels.map((r, ri) => (
              <tr key={r}>
                <td className="sticky left-0 bg-surface pr-2 text-right text-muted">{r}</td>
                {grid.grid[ri].map((cell, ci) => {
                  if (!cell) {
                    return <td key={ci}
                      className="h-8 rounded-md border border-dashed border-line/50" />;
                  }
                  return (
                    <td key={ci} className="p-0">
                      <Tip content={
                        <div className="tabular space-y-0.5">
                          <div className="font-medium">{r} · {grid.col_labels[ci]}</div>
                          <div>{cell.trades} trades · {pct(cell.win_rate_pct)} win</div>
                          <div className={cell.net >= 0 ? 'text-up' : 'text-down'}>
                            {money(cell.net)} (avg {money(cell.avg)})
                          </div>
                          {!cell.reliable && (
                            <div className="text-warn">Below {grid.min_reliable} trades — not reliable</div>
                          )}
                        </div>
                      }>
                        <div className={cn('tabular flex h-8 cursor-pointer items-center justify-center',
                          'rounded-md border border-line/40 hover:ring-1 hover:ring-accent',
                          !cell.reliable && 'italic text-muted')}
                          style={cellStyle(cell.net, peak, cell.reliable)}>
                          {money(cell.net, { sign: false, digits: 0 })}
                        </div>
                      </Tip>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function Heatmaps({ heatmaps }) {
  const entries = Object.entries(heatmaps || {});
  if (!entries.length) return null;
  return (
    <div className="mb-5 grid gap-4 xl:grid-cols-2">
      {entries.map(([spec, grid]) => <HeatmapGrid key={spec} spec={spec} grid={grid} />)}
    </div>
  );
}
