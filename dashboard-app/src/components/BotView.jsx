/**
 * The Bot tab: what the strategy would be doing, and what it saw.
 *
 * Three panels over three routes. The order is the argument: the position is
 * what you check first, the setups list is what decides whether any of it is
 * trustworthy, and the backtest sits last because a P&L figure read before the
 * setups have been eyeballed is a number about the wrong strategy.
 *
 * Nothing here can place an order. The bridge exposes no route that could.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Clock, Crosshair, Lock, Target, TrendingDown, TrendingUp } from 'lucide-react';
import { CONDITION_LABELS, fetchBacktest, fetchPaper, fetchSetups } from '@/lib/api';
import { DASH, money, num, pct, stamp, tone } from '@/lib/format';
import { Badge, Button, Card, CardTitle, Empty, ErrorState, Input, Reveal, Select, Skeleton, Tip } from '@/components/ui';

const TIMEFRAMES = [
  { value: '1', label: '1 min' }, { value: '5', label: '5 min' },
  { value: '15', label: '15 min' }, { value: '30', label: '30 min' },
  { value: '60', label: '1 hour' }, { value: '240', label: '4 hour' },
];

const DEFAULT_CFG = { symbol: 'GOLD.i#', timeframe: '5', count: 1000, balance: 1000 };

/** A field that is genuinely absent renders as a dash, never as zero. */
function Stat({ label, value, hint, toneClass }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-0.5 text-[15px] font-semibold tabular-nums ${toneClass || 'text-ink'}`}>
        {value ?? DASH}
      </p>
      {hint && <p className="mt-0.5 text-[11px] text-muted">{hint}</p>}
    </div>
  );
}

function Conditions({ names }) {
  if (!names?.length) return <span className="text-muted">{DASH}</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {names.map(n => (
        <Badge key={n} tone="info">{CONDITION_LABELS[n] || n}</Badge>
      ))}
    </span>
  );
}

function PositionPanel({ data }) {
  const position = data?.position;
  const pending = data?.pending;

  if (!position && !pending) {
    return (
      <Card>
        <CardTitle hint="Reconstructed from closed bars on every load">Position</CardTitle>
        <Empty icon={Crosshair}>Flat — no position and no confirmation waiting.</Empty>
      </Card>
    );
  }

  if (!position && pending) {
    return (
      <Card>
        <CardTitle hint="A confirmation has fired; the entry bar has not opened yet">
          Pending confirmation
        </CardTitle>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Direction"
            value={pending.direction === 'long' ? 'Long' : 'Short'}
            toneClass={pending.direction === 'long' ? 'text-up' : 'text-down'} />
          <Stat label="Stop" value={num(pending.stop, 2)} />
          <Stat label="Entry" value={DASH} hint="next bar open" />
          <Stat label="Confirmed" value={stamp(pending.confirmed_at)} />
        </div>
        <div className="mt-3 flex items-start gap-2 rounded-lg bg-elevated p-2.5">
          <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
          <p className="text-xs text-muted">{pending.entry_note}</p>
        </div>
        <div className="mt-3 text-xs text-muted">
          Conditions <Conditions names={pending.conditions} />
        </div>
      </Card>
    );
  }

  const long = position.direction === 'long';
  const moved = position.stop_kind === 'breakeven';

  return (
    <Card>
      <CardTitle
        hint={`Opened ${stamp(position.opened)}`}
        right={
          <Badge tone={long ? 'up' : 'down'}>
            {long ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
            {long ? 'Long' : 'Short'}
          </Badge>
        }>
        Open position
      </CardTitle>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Entry" value={num(position.entry_price, 2)} />
        <Stat label="Stop" value={num(position.stop, 2)}
          hint={moved ? 'moved to breakeven' : 'initial'}
          toneClass={moved ? 'text-info' : 'text-ink'} />
        <Stat label="Target" value={num(position.target, 2)}
          hint={`${position.management?.target_r}R`} />
        <Stat label="Lot" value={num(position.lot, 2)}
          hint={position.risk != null ? `risking ${money(position.risk, { sign: false })}` : null} />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-3 text-xs text-muted">
        <span>Conditions <Conditions names={position.conditions} /></span>
        {position.partial_taken && <Badge tone="neutral">partial taken</Badge>}
        {moved && (
          <Tip content="The stop is at entry. This is the parameter under test — trailing early is the suspected cause of the early exits.">
            <span><Badge tone="info"><Lock className="h-3 w-3" />stop at breakeven</Badge></span>
          </Tip>
        )}
      </div>
    </Card>
  );
}

/**
 * The setups table.
 *
 * Carries no P&L on purpose. This is the panel that decides whether the rest
 * of the tab is measuring the intended strategy or a different one that
 * superficially resembles it, and a profit column here would invite reading it
 * as a result rather than as a list to check.
 */
function SetupsPanel({ cfg }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['setups', cfg],
    queryFn: ({ signal }) => fetchSetups({ ...cfg, limit: 50 }, signal),
    staleTime: 30_000,
  });

  if (isLoading) return <Card><Skeleton className="h-56 w-full" /></Card>;
  if (error) return <Card><ErrorState error={error} onRetry={refetch} /></Card>;

  const rows = data?.rows || [];

  return (
    <Card>
      <CardTitle
        hint="No profit or loss attached — this is the list to check against a chart"
        right={<Badge tone="neutral">{data?.setups ?? 0} found</Badge>}>
        Detected setups
      </CardTitle>

      {rows.length === 0 ? (
        <Empty icon={Crosshair}>No setups in this window.</Empty>
      ) : (
        <div className="-mx-1 overflow-x-auto">
          <table className="w-full min-w-[560px] text-[13px]">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-muted">
                <th className="px-1 pb-2 font-medium">Time</th>
                <th className="px-1 pb-2 font-medium">Side</th>
                <th className="px-1 pb-2 font-medium">Conditions</th>
                <th className="px-1 pb-2 text-right font-medium">Zone</th>
                <th className="px-1 pb-2 text-right font-medium">Confirm close</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map(row => {
                const zone = Object.values(row.zones || {})[0];
                return (
                  <tr key={`${row.index}-${row.direction}`} className="hover:bg-elevated/60">
                    <td className="px-1 py-1.5 tabular-nums text-muted">{stamp(row.time)}</td>
                    <td className="px-1 py-1.5">
                      <span className={row.direction === 'long' ? 'text-up' : 'text-down'}>
                        {row.direction === 'long' ? 'Long' : 'Short'}
                      </span>
                    </td>
                    <td className="px-1 py-1.5"><Conditions names={row.conditions} /></td>
                    <td className="px-1 py-1.5 text-right tabular-nums text-muted">
                      {zone ? `${num(zone.low, 2)} – ${num(zone.high, 2)}` : DASH}
                    </td>
                    <td className="px-1 py-1.5 text-right tabular-nums">
                      {num(row.confirmation_close, 2)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 border-t border-line pt-3 text-xs text-muted">
        Scanned {data?.bars_scanned ?? DASH} bars
        {data?.scanned_from && <> from {stamp(data.scanned_from)} to {stamp(data.scanned_to)}</>}.
        If these are not the setups you would take, the backtest below is measuring
        a different strategy.
      </p>
    </Card>
  );
}

function BacktestPanel({ cfg }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['backtest', cfg],
    queryFn: ({ signal }) => fetchBacktest({ ...cfg, skipped: true }, signal),
    staleTime: 30_000,
  });

  if (isLoading) return <Card><Skeleton className="h-40 w-full" /></Card>;
  if (error) return <Card><ErrorState error={error} onRetry={refetch} /></Card>;

  const s = data?.summary || {};
  const unreliable = s.reliable === false;

  return (
    <Card>
      <CardTitle
        hint="Entry fills at the next bar open; a bar holding both stop and target counts as the stop"
        right={s.closed != null && <Badge tone="neutral">{s.closed} closed</Badge>}>
        Backtest
      </CardTitle>

      {unreliable && (
        <div className="mb-3 flex items-start gap-2 rounded-lg bg-warn/10 p-2.5 ring-1 ring-inset ring-warn/30">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />
          <p className="text-xs text-warn">{s.note}</p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        <Stat label="Win rate" value={pct(s.win_rate_pct)} />
        <Stat label="Avg R" value={num(s.avg_r, 2)} toneClass={tone(s.avg_r)} />
        <Stat label="Expectancy" value={money(s.expectancy)} toneClass={tone(s.expectancy)} />
        <Stat label="Net" value={money(s.net)} toneClass={tone(s.net)} />
        <Stat label="Profit factor" value={num(s.profit_factor, 2)}
          hint={s.profit_factor == null ? 'no losing trade' : null} />
      </div>

      {s.stop_kinds && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="mb-1.5 text-[11px] uppercase tracking-wide text-muted">Stops that filled</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(s.stop_kinds).map(([kind, n]) => (
              <Badge key={kind} tone={kind === 'breakeven' ? 'info' : 'neutral'}>
                {kind === 'breakeven' ? 'moved to breakeven' : 'initial'} · {n}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {s.skipped_reasons && Object.keys(s.skipped_reasons).length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="mb-1.5 text-[11px] uppercase tracking-wide text-muted">
            Signals not traded ({s.skipped})
          </p>
          <ul className="space-y-1">
            {Object.entries(s.skipped_reasons).map(([reason, n]) => (
              <li key={reason} className="flex gap-2 text-xs text-muted">
                <span className="tabular-nums text-ink">{n}</span>
                <span>{reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function ConfigBar({ draft, setDraft, onApply, dirty }) {
  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-muted">Symbol</span>
        <Input value={draft.symbol} className="w-36"
          onChange={e => setDraft({ ...draft, symbol: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-muted">Timeframe</span>
        <Select value={draft.timeframe} options={TIMEFRAMES}
          onValueChange={v => setDraft({ ...draft, timeframe: v || '5' })} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-muted">Bars</span>
        <Input type="number" value={draft.count} className="w-24"
          onChange={e => setDraft({ ...draft, count: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-muted">Balance</span>
        <Input type="number" value={draft.balance} className="w-24"
          onChange={e => setDraft({ ...draft, balance: e.target.value })} />
      </label>
      <Button variant={dirty ? 'accent' : 'default'} onClick={onApply} disabled={!dirty}>
        {dirty ? 'Apply' : 'Applied'}
      </Button>
    </div>
  );
}

export default function BotView() {
  const [cfg, setCfg] = useState(DEFAULT_CFG);
  const [draft, setDraft] = useState(DEFAULT_CFG);
  const dirty = JSON.stringify(cfg) !== JSON.stringify(draft);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['paper', cfg],
    queryFn: ({ signal }) => fetchPaper({ ...cfg, recent: 8 }, signal),
    // Live enough to be useful, slow enough not to hammer the terminal. The
    // answer only changes when a bar closes anyway.
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  return (
    <div className="space-y-4">
      <ConfigBar draft={draft} setDraft={setDraft} dirty={dirty}
        onApply={() => setCfg({ ...draft, count: Number(draft.count) || 1000,
          balance: Number(draft.balance) || 1000 })} />

      {error ? (
        <Card><ErrorState error={error} onRetry={refetch} /></Card>
      ) : isLoading ? (
        <Card><Skeleton className="h-32 w-full" /></Card>
      ) : (
        <>
          <Reveal><PositionPanel data={data} /></Reveal>
          {data?.bars_dropped_as_forming > 0 && (
            <p className="flex items-center gap-1.5 text-xs text-muted">
              <Clock className="h-3 w-3" />
              {data.bars_dropped_as_forming} bar still forming, excluded — acting on an
              unclosed candle is the live equivalent of lookahead.
            </p>
          )}
        </>
      )}

      <Reveal delay={0.05}><SetupsPanel cfg={cfg} /></Reveal>
      <Reveal delay={0.1}><BacktestPanel cfg={cfg} /></Reveal>

      <p className="flex items-start gap-2 text-xs text-muted">
        <Target className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        Everything on this tab is simulated and read-only. No route behind it can
        place, modify or cancel an order.
      </p>
    </div>
  );
}
