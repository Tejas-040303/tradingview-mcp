import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, CircleDot, Clock, Wifi, WifiOff } from 'lucide-react';
import { Badge, Card, CardTitle, Empty, ErrorState, Reveal, Skeleton } from '@/components/ui';
import { hold, money, num, pct, stamp, tone } from '@/lib/format';
import { cn, fetchOverview } from '@/lib/api';

/**
 * Live account status.
 *
 * Reads /overview — one request for the whole view, so the parts can never
 * disagree with each other. Every section degrades independently: a missing
 * calendar file must not blank out the account card, and MT5 being unreachable
 * must not hide the fact that the bridge itself is fine.
 *
 * This is the one view that polls. Trade history does not change while you look
 * at it; an open position does.
 */
export default function StatusView({ intervalMs = 5000 }) {
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ['overview'],
    queryFn: ({ signal }) => fetchOverview(signal),
    refetchInterval: intervalMs,
    staleTime: 0,
    retry: 1,
  });

  if (isPending && !data) {
    return (
      <div className="grid gap-4 lg:grid-cols-2">
        {Array.from({ length: 4 }, (_, i) => (
          <Card key={i}><Skeleton className="h-4 w-28" /><Skeleton className="mt-4 h-24 w-full" /></Card>
        ))}
      </div>
    );
  }

  if (error && !data) {
    return (
      <Card>
        <ErrorState error={error} onRetry={() => refetch()} />
        <p className="mt-2 text-xs text-muted">
          This page is served by bridge.py, so a failure here means the bridge itself
          stopped responding.
        </p>
      </Card>
    );
  }

  return (
    <>
      <ConnectionStrip data={data} />
      <NewsBanner blackout={data.blackout} />
      <div className="mb-5 grid gap-4 lg:grid-cols-2">
        <AccountCard account={data.account} />
        <PnlCard pnl={data.pnl} />
      </div>
      <PositionsCard positions={data.positions} />
      <OrdersCard orders={data.orders} />
    </>
  );
}

/** A failed section carries { success: false, error }. */
const sectionError = section =>
  (section && section.success === false ? section.error : null);

function Pill({ label, detail, state = 'ok', icon: Icon }) {
  const dot = { ok: 'bg-up', bad: 'bg-down', warn: 'bg-warn' }[state];
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line
                     bg-surface px-3 py-1.5 text-[13px]">
      {Icon ? <Icon className={cn('h-3.5 w-3.5',
        state === 'bad' ? 'text-down' : state === 'warn' ? 'text-warn' : 'text-up')} />
        : <span className={cn('h-2 w-2 rounded-full', dot)} />}
      <span className="font-medium text-ink">{label}</span>
      {detail && <span className="text-muted">{detail}</span>}
    </span>
  );
}

function ConnectionStrip({ data }) {
  const health = data.health || {};
  const err = sectionError(health);
  const offset = health.server_utc_offset_sec;

  return (
    <div className="mb-4">
      <div className="flex flex-wrap gap-2">
        <Pill label="Bridge" detail="reachable" icon={Wifi} />
        {err ? (
          <Pill label="MT5" detail="not connected" state="bad" icon={WifiOff} />
        ) : (
          <Pill label="MT5" state={health.connected ? 'ok' : 'bad'}
            detail={health.account
              ? `${health.account.login} · ${health.account.server}` : undefined} />
        )}
        {/* An unknown broker offset means every UTC timestamp downstream is null.
            Worth surfacing rather than letting it look like missing data. */}
        <Pill label="Clock" state={offset === null || offset === undefined ? 'warn' : 'ok'}
          icon={Clock}
          detail={offset === null || offset === undefined
            ? 'offset unknown'
            : `UTC${offset >= 0 ? '+' : ''}${offset / 3600}h · ${health.server_utc_offset_source}`} />
        <Pill label="Updated" detail={stamp(data.generated_at)?.slice(11)} />
      </div>
      {err && <p className="mt-2 text-xs text-down">{err}</p>}
    </div>
  );
}

function NewsBanner({ blackout }) {
  const err = sectionError(blackout);
  if (err || !blackout || blackout.blackout === null) {
    return (
      <Card className="mb-5">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-muted" />
          <span className="text-[13px] font-medium">News</span>
          <span className="text-[13px] text-muted">
            {err || blackout?.error || 'no calendar loaded'}
          </span>
        </div>
      </Card>
    );
  }

  if (blackout.blackout) {
    const active = (blackout.active || [])
      .map(e => `${e.event} (${hold(Math.abs(e.minutes_until) * 60)} ${e.minutes_until >= 0 ? 'away' : 'ago'})`)
      .join(', ');
    return (
      <Card className="mb-5 border-down/40 bg-down/5">
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-down" />
          <div>
            <span className="text-[13px] font-semibold text-down">News blackout</span>
            <p className="text-[13px] text-muted">{active}</p>
          </div>
        </div>
      </Card>
    );
  }

  const next = blackout.next;
  return (
    <Card className="mb-5 border-up/40 bg-up/5">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="h-4 w-4 text-up" />
        <span className="text-[13px] font-semibold text-up">Clear</span>
        <span className="text-[13px] text-muted">
          {next
            ? `next: ${next.event} in ${hold(next.minutes_until * 60)}`
            : 'no upcoming events'}
        </span>
      </div>
    </Card>
  );
}

function Rows({ items }) {
  return (
    <dl className="space-y-1.5 text-[13px]">
      {items.map(([label, value, cls]) => (
        <div key={label} className="flex justify-between gap-4 border-b border-line/50 pb-1.5 last:border-0">
          <dt className="text-muted">{label}</dt>
          <dd className={cn('tabular text-right', cls)}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function AccountCard({ account }) {
  const err = sectionError(account);
  const cur = account?.currency || '';
  // equity - balance is floating P&L on open positions; showing it explains any
  // gap between the two numbers without a second lookup.
  const floating = (account?.equity ?? 0) - (account?.balance ?? 0);

  return (
    <Card>
      <CardTitle>Account</CardTitle>
      {err ? <p className="text-xs text-down">{err}</p> : (
        <Rows items={[
          ['Balance', `${num(account.balance)} ${cur}`],
          ['Equity', `${num(account.equity)} ${cur}`],
          ['Floating', money(floating), tone(floating)],
          ['Margin', num(account.margin)],
          ['Free margin', num(account.margin_free)],
          ['Margin level', account.margin_level ? `${num(account.margin_level)}%` : '—'],
          ['Leverage', `1:${account.leverage ?? '—'}`],
        ]} />
      )}
    </Card>
  );
}

function PnlCard({ pnl }) {
  if (!pnl || pnl.error) {
    return <Card><CardTitle>Realised P&amp;L</CardTitle>
      <p className="text-xs text-down">{pnl?.error || 'unavailable'}</p></Card>;
  }
  const row = (label, entry) => (
    <Reveal key={label}>
      <div className="flex items-center justify-between border-b border-line/50 py-2 last:border-0">
        <span className="text-[13px]">
          {label} <span className="text-muted">
            {entry.trades} trade{entry.trades === 1 ? '' : 's'}</span>
        </span>
        <span className={cn('tabular text-[15px] font-semibold', tone(entry.net))}>
          {money(entry.net)}
        </span>
      </div>
    </Reveal>
  );

  return (
    <Card>
      <CardTitle hint="Realised only — floating sits in the account card. UTC periods.">
        Realised P&amp;L
      </CardTitle>
      {row('Today', pnl.today)}
      {row('This week', pnl.week)}
      {row('This month', pnl.month)}
    </Card>
  );
}

function PositionsCard({ positions }) {
  const err = sectionError(positions);
  const rows = positions?.positions || [];

  return (
    <Card className="mb-5">
      <CardTitle right={rows.length > 0 && <Badge tone="info">{rows.length} open</Badge>}>
        Open positions
      </CardTitle>
      {err ? <p className="text-xs text-down">{err}</p>
        : !rows.length ? <Empty icon={CircleDot}>No open positions.</Empty> : (
          <div className="overflow-x-auto">
            <table className="tabular w-full text-[12px]">
              <thead className="text-muted">
                <tr>
                  {['Symbol', 'Side', 'Volume', 'Open', 'Current', 'SL', 'TP', 'Profit', 'Opened (UTC)']
                    .map((h, i) => (
                      <th key={h} className={cn('whitespace-nowrap px-2 py-1.5 font-medium',
                        i === 0 || i === 1 ? 'text-left' : 'text-right')}>{h}</th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(p => (
                  <tr key={p.ticket ?? `${p.symbol}-${p.time_utc}`} className="border-t border-line/60">
                    <td className="px-2 py-1.5 text-left">{p.symbol}</td>
                    <td className="px-2 py-1.5 text-left">
                      <Badge tone={p.type === 'buy' ? 'up' : 'down'}>{p.type}</Badge>
                    </td>
                    <td className="px-2 py-1.5 text-right">{num(p.volume)}</td>
                    <td className="px-2 py-1.5 text-right">{num(p.price_open, 5)}</td>
                    <td className="px-2 py-1.5 text-right">{num(p.price_current, 5)}</td>
                    <td className="px-2 py-1.5 text-right">{p.sl ? num(p.sl, 5) : '—'}</td>
                    <td className="px-2 py-1.5 text-right">{p.tp ? num(p.tp, 5) : '—'}</td>
                    <td className={cn('px-2 py-1.5 text-right font-medium', tone(p.profit))}>
                      {money(p.profit)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-muted">
                      {stamp(p.time_utc_iso || p.time_server_iso)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </Card>
  );
}

function OrdersCard({ orders }) {
  const err = sectionError(orders);
  const rows = orders?.orders || [];

  return (
    <Card className="mb-5">
      <CardTitle right={rows.length > 0 && <Badge tone="info">{rows.length} pending</Badge>}>
        Pending orders
      </CardTitle>
      {err ? <p className="text-xs text-down">{err}</p>
        : !rows.length ? <Empty icon={CircleDot}>No pending orders.</Empty> : (
          <div className="overflow-x-auto">
            <table className="tabular w-full text-[12px]">
              <thead className="text-muted">
                <tr>
                  {['Symbol', 'Type', 'Volume', 'Price', 'SL', 'TP', 'Placed (UTC)'].map((h, i) => (
                    <th key={h} className={cn('whitespace-nowrap px-2 py-1.5 font-medium',
                      i < 2 ? 'text-left' : 'text-right')}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(o => (
                  <tr key={o.ticket ?? `${o.symbol}-${o.time_utc}`} className="border-t border-line/60">
                    <td className="px-2 py-1.5 text-left">{o.symbol}</td>
                    <td className="px-2 py-1.5 text-left">{o.type}</td>
                    <td className="px-2 py-1.5 text-right">{num(o.volume_initial ?? o.volume_current)}</td>
                    <td className="px-2 py-1.5 text-right">{num(o.price_open, 5)}</td>
                    <td className="px-2 py-1.5 text-right">{o.sl ? num(o.sl, 5) : '—'}</td>
                    <td className="px-2 py-1.5 text-right">{o.tp ? num(o.tp, 5) : '—'}</td>
                    <td className="px-2 py-1.5 text-right text-muted">
                      {stamp(o.time_utc_iso || o.time_server_iso)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </Card>
  );
}

export { pct };
