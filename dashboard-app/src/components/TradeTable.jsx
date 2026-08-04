import { useMemo, useRef, useState } from 'react';
import {
  flexRender, getCoreRowModel, getSortedRowModel, useReactTable,
} from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import * as Dialog from '@radix-ui/react-dialog';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Search, X } from 'lucide-react';
import { Badge, Button, Card, CardTitle, Empty, Input } from '@/components/ui';
import { hold, money, num, pct, stamp, tone } from '@/lib/format';
import { cn, downloadFile, toCsv } from '@/lib/api';

const COLUMNS = [
  { accessorKey: 'opened', header: 'Opened (UTC)', cell: v => stamp(v), align: 'left' },
  { accessorKey: 'symbol', header: 'Symbol', align: 'left' },
  { accessorKey: 'direction', header: 'Side', align: 'left',
    cell: v => v ? <Badge tone={v === 'long' ? 'up' : 'down'}>{v}</Badge> : '—' },
  { accessorKey: 'volume', header: 'Lots', cell: v => num(v) },
  { accessorKey: 'entry_price', header: 'Entry', cell: (v, row) =>
    row.entry_missing ? <span className="text-muted">before window</span> : num(v, 5) },
  { accessorKey: 'exit_price', header: 'Exit', cell: (v, row) =>
    row.open ? <span className="text-muted">open</span> : num(v, 5) },
  { accessorKey: 'duration_sec', header: 'Held', cell: v => hold(v) },
  { accessorKey: 'exit_reason', header: 'Exit', align: 'left',
    cell: (v, row) => (
      <span>{v || '—'}{row.partial_closes
        ? <span className="ml-1 text-muted">+{row.partial_closes} partial</span> : ''}</span>
    ) },
  { accessorKey: 'net', header: 'Net', cell: (v, row) =>
    row.open ? '—' : <span className={tone(v)}>{money(v)}</span> },
];

/**
 * Trade explorer.
 *
 * Rows are virtualised so a few thousand trades scroll without the DOM growing
 * to match. Sorting and search are client-side because they operate on the page
 * already fetched; the filters that change *which* trades are in scope stay
 * server-side, where the statistics are computed.
 */
export default function TradeTable({ trades, page, onPage, onExport }) {
  const [search, setSearch] = useState('');
  const [sorting, setSorting] = useState([{ id: 'opened', desc: true }]);
  const [selected, setSelected] = useState(null);
  const scrollRef = useRef(null);

  const data = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return trades || [];
    return (trades || []).filter(t =>
      [t.symbol, t.direction, t.exit_reason, t.opened, String(t.position_id)]
        .some(v => String(v ?? '').toLowerCase().includes(needle)));
  }, [trades, search]);

  const columns = useMemo(() => COLUMNS.map(col => ({
    accessorKey: col.accessorKey,
    header: col.header,
    meta: { align: col.align || 'right' },
    cell: info => (col.cell
      ? col.cell(info.getValue(), info.row.original)
      : (info.getValue() ?? '—')),
  })), []);

  const table = useReactTable({
    data, columns, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  });

  const rows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 36,
    overscan: 12,
  });
  const items = virtualizer.getVirtualItems();
  const padTop = items[0]?.start ?? 0;
  const padBottom = virtualizer.getTotalSize() - (items[items.length - 1]?.end ?? 0);

  return (
    <Card className="mb-5">
      <CardTitle hint={`${page?.total ?? 0} trades match. Open trades are listed but excluded from every statistic.`}
        right={
          <div className="flex items-center gap-1.5">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
              <Input value={search} onChange={e => setSearch(e.target.value)}
                placeholder="Search rows" className="w-40 pl-7" aria-label="Search trades" />
            </div>
            <Button variant="ghost" size="sm" onClick={() => downloadFile(
              'trades.csv', 'text/csv',
              toCsv(data, ['position_id', 'symbol', 'direction', 'opened', 'closed',
                'volume', 'entry_price', 'exit_price', 'duration_sec', 'exit_reason', 'net']))}>
              CSV
            </Button>
            <Button variant="ghost" size="sm" onClick={() => downloadFile(
              'trades.json', 'application/json', JSON.stringify(data, null, 2))}>
              JSON
            </Button>
          </div>
        }>
        Trades
      </CardTitle>

      {!rows.length ? <Empty>No trades match these filters.</Empty> : (
        <>
          <div ref={scrollRef} className="max-h-[520px] overflow-auto rounded-xl border border-line">
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 z-10 bg-elevated">
                {table.getHeaderGroups().map(group => (
                  <tr key={group.id}>
                    {group.headers.map(header => (
                      <th key={header.id}
                        onClick={header.column.getToggleSortingHandler()}
                        className={cn('cursor-pointer select-none whitespace-nowrap px-3 py-2',
                          'font-medium text-muted hover:text-ink',
                          header.column.columnDef.meta.align === 'left' ? 'text-left' : 'text-right')}>
                        <span className="inline-flex items-center gap-1">
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {{ asc: <ArrowUp className="h-3 w-3" />,
                            desc: <ArrowDown className="h-3 w-3" /> }[header.column.getIsSorted()] ?? null}
                        </span>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody className="tabular">
                {padTop > 0 && <tr><td style={{ height: padTop }} colSpan={columns.length} /></tr>}
                {items.map(item => {
                  const row = rows[item.index];
                  return (
                    <tr key={row.id} onClick={() => setSelected(row.original)}
                      className={cn('cursor-pointer border-t border-line/60 hover:bg-elevated',
                        row.original.open && 'italic opacity-70')}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id}
                          className={cn('whitespace-nowrap px-3 py-2',
                            cell.column.columnDef.meta.align === 'left' ? 'text-left' : 'text-right')}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  );
                })}
                {padBottom > 0 && <tr><td style={{ height: padBottom }} colSpan={columns.length} /></tr>}
              </tbody>
            </table>
          </div>

          {page && page.total > page.limit && (
            <div className="mt-3 flex items-center gap-3">
              <Button size="sm" disabled={page.offset <= 0}
                onClick={() => onPage(Math.max(0, page.offset - page.limit))}>
                <ChevronLeft className="h-3.5 w-3.5" /> Newer
              </Button>
              <span className="tabular text-xs text-muted">
                {page.offset + 1}–{Math.min(page.offset + page.limit, page.total)} of {page.total}
              </span>
              <Button size="sm" disabled={page.offset + page.limit >= page.total}
                onClick={() => onPage(page.offset + page.limit)}>
                Older <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
        </>
      )}

      <TradeDrawer trade={selected} onClose={() => setSelected(null)} />
    </Card>
  );
}

/** Detail drawer. Tabs the data supports; the rest are named as unavailable. */
function TradeDrawer({ trade, onClose }) {
  const [tab, setTab] = useState('overview');
  if (!trade) return null;

  const rows = {
    overview: [
      ['Position ID', trade.position_id],
      ['Symbol', trade.symbol],
      ['Direction', trade.direction],
      ['Volume', num(trade.volume)],
      ['Net', money(trade.net), tone(trade.net)],
      ['Status', trade.open ? 'open' : 'closed'],
    ],
    execution: [
      ['Opened', stamp(trade.opened)],
      ['Closed', trade.open ? '—' : stamp(trade.closed)],
      ['Held', hold(trade.duration_sec)],
      ['Entry price', trade.entry_missing ? 'before window' : num(trade.entry_price, 5)],
      ['Exit price', trade.open ? 'open' : num(trade.exit_price, 5)],
      ['Exit reason', trade.exit_reason || '—'],
      ['Fills', trade.deals],
      ['Partial closes', trade.partial_closes],
    ],
  };

  const TABS = [
    { id: 'overview', label: 'Overview' },
    { id: 'execution', label: 'Execution' },
    { id: 'risk', label: 'Risk' },
    { id: 'notes', label: 'Notes' },
  ];

  return (
    <Dialog.Root open onOpenChange={open => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" />
        <Dialog.Content
          className="fixed right-0 top-0 z-50 h-full w-full max-w-md overflow-y-auto
                     border-l border-line bg-surface p-5 shadow-lift focus:outline-none">
          <div className="flex items-start justify-between">
            <div>
              <Dialog.Title className="text-sm font-semibold text-ink">
                {trade.symbol} · {trade.direction}
              </Dialog.Title>
              <Dialog.Description className="text-xs text-muted">
                {stamp(trade.opened)} · position {trade.position_id}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button variant="ghost" size="sm" aria-label="Close"><X className="h-4 w-4" /></Button>
            </Dialog.Close>
          </div>

          <div className={cn('tabular mt-4 text-3xl font-semibold', tone(trade.net))}>
            {trade.open ? '—' : money(trade.net)}
          </div>

          <div className="mt-4 flex gap-1 border-b border-line">
            {TABS.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={cn('-mb-px border-b-2 px-3 py-1.5 text-[13px] transition-colors',
                  tab === t.id
                    ? 'border-accent text-ink'
                    : 'border-transparent text-muted hover:text-ink')}>
                {t.label}
              </button>
            ))}
          </div>

          <div className="mt-4">
            {rows[tab] ? (
              <dl className="space-y-2 text-[13px]">
                {rows[tab].map(([k, v, cls]) => (
                  <div key={k} className="flex justify-between gap-4 border-b border-line/50 pb-1.5">
                    <dt className="text-muted">{k}</dt>
                    <dd className={cn('tabular text-right', cls)}>{v ?? '—'}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <div className="rounded-xl border border-dashed border-line bg-elevated/40 p-4">
                <p className="text-[13px] font-medium text-muted">
                  {tab === 'risk' ? 'Risk data unavailable' : 'No journal entry'}
                </p>
                <p className="mt-1.5 text-xs leading-relaxed text-muted/80">
                  {tab === 'risk'
                    ? 'R-multiple and risk % need the stop-loss distance at entry, which MetaTrader 5 does not include in deal history. Recoverable from order history for some trades; not yet wired.'
                    : 'Notes, tags, setups and screenshots need a journalling layer. MetaTrader 5 stores fills, not intent.'}
                </p>
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
