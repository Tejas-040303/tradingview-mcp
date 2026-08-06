/**
 * The Research tab: parameter sweeps and signal-versus-fill reconciliation.
 *
 * The sweep panel is built around a tension worth stating. A table of thirty
 * configurations sorted by return *is* a leaderboard, and a leaderboard always
 * has a winner — thirty coin-flipping strategies produce a convincing one too.
 * So the verdict leads, the table follows, and when the bridge declines to
 * endorse a result the table renders subdued with the reason above it. Sorting
 * rows by profit and letting the eye land on row one is the exact failure this
 * layout exists to prevent.
 *
 * Read-only. Neither route can place an order.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, FlaskConical, GitCompare, Info, Play } from 'lucide-react';
import { CONDITION_LABELS, fetchReconcile, fetchSweep } from '@/lib/api';
import { DASH, money, num, pct, stamp, tone } from '@/lib/format';
import { Badge, Button, Card, CardTitle, Empty, ErrorState, Input, Reveal, Select, Skeleton, Tip } from '@/components/ui';

const TIMEFRAMES = [
  { value: '1', label: '1 min' }, { value: '5', label: '5 min' },
  { value: '15', label: '15 min' }, { value: '30', label: '30 min' },
  { value: '60', label: '1 hour' }, { value: '240', label: '4 hour' },
];

const DEFAULT_CFG = { symbol: 'GOLD.i#', timeframe: '5', count: 2000, balance: 1000 };

/** Axis keys are dotted config paths; these are what they mean. */
const AXIS_LABELS = {
  'manage.trail_to_be_at_r': 'Trail to BE',
  'manage.partial_pct': 'Partial %',
  'target.r': 'Target R',
  'entry.mode': 'Mode',
  'stop.buffer_pips': 'Buffer',
};

const axisLabel = key => AXIS_LABELS[key] || key.split('.').pop().replace(/_/g, ' ');

/** null is "no trail", which is the control case — never render it as 0. */
const axisValue = v => (v === null ? 'off' : String(v));

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

function Verdict({ summary }) {
  const trustworthy = summary?.trustworthy === true;
  const text = summary?.reading || summary?.note;

  return (
    <div className={`mb-4 flex items-start gap-2.5 rounded-xl p-3 ring-1 ring-inset ${
      trustworthy ? 'bg-up/5 ring-up/30' : 'bg-warn/5 ring-warn/30'}`}>
      {trustworthy
        ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-up" />
        : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" />}
      <div className="space-y-1.5">
        <p className={`text-[13px] font-medium ${trustworthy ? 'text-up' : 'text-warn'}`}>
          {trustworthy ? 'Result worth acting on' : 'Not enough to claim an edge'}
        </p>
        {text && <p className="text-xs leading-relaxed text-muted">{text}</p>}
        {summary?.caveat && <p className="text-xs leading-relaxed text-muted">{summary.caveat}</p>}
      </div>
    </div>
  );
}

function SweepTable({ rows, trustworthy }) {
  if (!rows?.length) return <Empty icon={FlaskConical}>No configurations produced trades.</Empty>;

  const axes = Object.keys(rows[0].params || {});

  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full min-w-[680px] text-[13px]">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-muted">
            {axes.map(a => <th key={a} className="px-1 pb-2 font-medium">{axisLabel(a)}</th>)}
            <th className="px-1 pb-2 text-right font-medium">In-sample R</th>
            <th className="px-1 pb-2 text-right font-medium">Out-of-sample R</th>
            <th className="px-1 pb-2 text-right font-medium">Trades</th>
            <th className="px-1 pb-2 text-right font-medium">Win %</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((row, i) => (
            <tr key={i}
              className={`hover:bg-elevated/60 ${row.ranked ? '' : 'opacity-45'}`}>
              {axes.map(a => (
                <td key={a} className="px-1 py-1.5 tabular-nums">{axisValue(row.params[a])}</td>
              ))}
              <td className="px-1 py-1.5 text-right tabular-nums text-muted">
                {num(row.in_sample?.avg_r, 2)}
              </td>
              {/* Only the out-of-sample column gets colour. The in-sample one
                  chose the ranking, so colouring it invites reading a fitted
                  number as a result. */}
              <td className={`px-1 py-1.5 text-right font-medium tabular-nums ${
                trustworthy ? tone(row.out_of_sample?.avg_r) : 'text-ink'}`}>
                {num(row.out_of_sample?.avg_r, 2)}
              </td>
              <td className="px-1 py-1.5 text-right tabular-nums text-muted">
                {row.out_of_sample?.trades ?? DASH}
              </td>
              <td className="px-1 py-1.5 text-right tabular-nums text-muted">
                {pct(row.out_of_sample?.win_rate_pct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 px-1 text-xs text-muted">
        Faded rows had too few closed trades in one half to be ranked. Sorted by
        out-of-sample average R — the only column the ranking is entitled to
        claim anything about.
      </p>
    </div>
  );
}

function SweepPanel({ cfg }) {
  // Opt-in: thirty configurations replayed over the whole window is the
  // slowest thing the bridge does, and it must not be a silent cost of
  // opening a tab.
  const [run, setRun] = useState(false);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['sweep', cfg],
    queryFn: ({ signal }) => fetchSweep(cfg, signal),
    enabled: run,
    staleTime: 5 * 60_000,
    retry: false,
  });

  if (!run) {
    return (
      <Card>
        <CardTitle hint="Every parameter combination, judged on bars it was not chosen on">
          Parameter sweep
        </CardTitle>
        <div className="flex flex-col items-start gap-3 py-6">
          <p className="max-w-prose text-[13px] text-muted">
            Replays thirty configurations across a chronological split — the first
            70% of bars chooses, the last 30% judges. The default grid varies the
            breakeven trail (including <span className="text-ink">off</span>, the
            control case), the partial size and the target.
          </p>
          <p className="max-w-prose text-xs text-muted">
            This is the slowest route on the bridge. It is not run automatically.
          </p>
          <Button variant="accent" onClick={() => setRun(true)}>
            <Play className="h-3.5 w-3.5" />Run sweep
          </Button>
        </div>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <Card>
        <CardTitle hint="Replaying every configuration across both halves">
          Parameter sweep
        </CardTitle>
        <Skeleton className="h-64 w-full" />
      </Card>
    );
  }
  if (error) return <Card><ErrorState error={error} onRetry={refetch} /></Card>;

  const s = data?.summary || {};
  const trustworthy = s.trustworthy === true;

  return (
    <Card>
      <CardTitle
        hint={`${data?.configurations ?? DASH} configurations · ${data?.bars ?? DASH} bars`}
        right={
          <Button size="sm" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? 'Running…' : 'Re-run'}
          </Button>
        }>
        Parameter sweep
      </CardTitle>

      <Verdict summary={s} />

      {s.ranked > 0 && (
        <div className="mb-4 grid grid-cols-2 gap-4 border-b border-line pb-4 sm:grid-cols-5">
          <Stat label="Best out-of-sample" value={num(s.best_out_of_sample_avg_r, 2)}
            toneClass={trustworthy ? tone(s.best_out_of_sample_avg_r) : 'text-ink'}
            hint="average R" />
          <Stat label="Median config" value={num(s.median_out_of_sample_avg_r, 2)}
            hint="the pack" />
          <Stat label="Margin over median" value={num(s.margin_over_median, 2)}
            hint="how clear the winner is" />
          <Stat label="Rank correlation" value={num(s.rank_correlation, 2)}
            hint="does the order survive?" />
          <Stat label="Overfit gap" value={num(s.overfit_gap, 2)}
            hint="in-sample minus out" />
        </div>
      )}

      {s.best && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted">
          <span>Best configuration</span>
          {Object.entries(s.best).map(([k, v]) => (
            <Badge key={k} tone={trustworthy ? 'up' : 'neutral'}>
              {axisLabel(k)} {axisValue(v)}
            </Badge>
          ))}
        </div>
      )}

      <SweepTable rows={data?.rows} trustworthy={trustworthy} />

      {data?.split && (
        <p className="mt-3 border-t border-line pt-3 text-xs text-muted">
          Chose on {data.split.in_sample_bars} bars up to{' '}
          {stamp(new Date(data.split.in_sample_to * 1000).toISOString())}, judged on{' '}
          {data.split.out_of_sample_bars} after it. Never shuffled — that would leak
          the future into the past.
        </p>
      )}
    </Card>
  );
}

/** A bucket average, or the reason there isn't one. Never a fabricated zero. */
function Bucket({ label, bucket, moneyValue = true }) {
  const value = bucket?.avg;
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-0.5 text-[15px] font-semibold tabular-nums ${
        value == null ? 'text-muted' : tone(value)}`}>
        {value == null ? DASH : (moneyValue ? money(value) : num(value, 2))}
      </p>
      {bucket?.note
        ? <p className="mt-0.5 text-[11px] text-muted">{bucket.note}</p>
        : <p className="mt-0.5 text-[11px] text-muted">{bucket?.n ?? 0} trades</p>}
    </div>
  );
}

function ReconcilePanel({ cfg }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['reconcile', cfg],
    queryFn: ({ signal }) => fetchReconcile(cfg, signal),
    staleTime: 60_000,
    retry: 1,
  });

  if (isLoading) return <Card><Skeleton className="h-56 w-full" /></Card>;
  if (error) return <Card><ErrorState error={error} onRetry={refetch} /></Card>;

  const sim = data?.simulated || {};
  const real = data?.real || {};

  return (
    <Card>
      <CardTitle hint="MT5 records only trades that were taken — this recovers the ones that were not">
        Signals versus fills
      </CardTitle>

      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Signals" value={data?.signals ?? DASH} />
        <Stat label="Followed" value={data?.followed ?? DASH}
          hint={data?.follow_rate_pct != null ? `${pct(data.follow_rate_pct)} of signals` : null} />
        <Stat label="Missed" value={data?.missed ?? DASH} hint="signalled, not traded" />
        <Stat label="Discretionary" value={data?.discretionary ?? DASH} hint="traded, no signal" />
      </div>

      {data?.findings?.length > 0 && (
        <ul className="mb-4 space-y-2">
          {data.findings.map((finding, i) => (
            <li key={i} className="flex items-start gap-2 rounded-lg bg-elevated p-2.5">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info" />
              <p className="text-xs leading-relaxed text-muted">{finding}</p>
            </li>
          ))}
        </ul>
      )}

      <div className="border-t border-line pt-3">
        <p className="mb-2 flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-muted">
          Simulated on both sides
          <Tip content="Simulated fills assume no spread and no slippage; real ones do not. Comparing a simulated number against a real one would be measuring execution, not selection — so taken and skipped are both simulated here.">
            <Info className="h-3 w-3" />
          </Tip>
        </p>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Bucket label="Took — avg R" bucket={sim.followed_r} moneyValue={false} />
          <Bucket label="Skipped — avg R" bucket={sim.missed_r} moneyValue={false} />
          <Bucket label="Took — avg net" bucket={sim.followed_net} />
          <Bucket label="Skipped — avg net" bucket={sim.missed_net} />
        </div>
      </div>

      <div className="mt-4 border-t border-line pt-3">
        <p className="mb-2 text-[11px] uppercase tracking-wide text-muted">Actually recorded</p>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <Bucket label="Followed trades" bucket={real.followed_net} />
          <Bucket label="Discretionary trades" bucket={real.discretionary_net} />
          <div>
            <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-muted">
              Execution gap
              <Tip content="Real minus simulated on the same signals. Negative means the account did worse than the backtest — spread, slippage, a stop that filled through, or an exit taken by hand.">
                <Info className="h-3 w-3" />
              </Tip>
            </p>
            <p className={`mt-0.5 text-[15px] font-semibold tabular-nums ${
              data?.execution_gap?.avg == null ? 'text-muted' : tone(data.execution_gap.avg)}`}>
              {data?.execution_gap?.avg == null ? DASH : money(data.execution_gap.avg)}
            </p>
            <p className="mt-0.5 text-[11px] text-muted">
              {data?.execution_gap?.note || `${data?.execution_gap?.n ?? 0} matched`}
            </p>
          </div>
        </div>
      </div>

      {data?.window && (
        <p className="mt-3 border-t border-line pt-3 text-xs text-muted">
          {stamp(data.window.from)} to {stamp(data.window.to)} · a fill counts as the
          same trade within {Math.round((data.tolerance_sec ?? 900) / 60)} minutes of
          its signal.
        </p>
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

export default function ResearchView() {
  const [cfg, setCfg] = useState(DEFAULT_CFG);
  const [draft, setDraft] = useState(DEFAULT_CFG);
  const dirty = JSON.stringify(cfg) !== JSON.stringify(draft);

  return (
    <div className="space-y-4">
      <ConfigBar draft={draft} setDraft={setDraft} dirty={dirty}
        onApply={() => setCfg({ ...draft, count: Number(draft.count) || 2000,
          balance: Number(draft.balance) || 1000 })} />

      <Reveal><ReconcilePanel cfg={cfg} /></Reveal>
      <Reveal delay={0.05}><SweepPanel cfg={cfg} /></Reveal>

      <p className="flex items-start gap-2 text-xs text-muted">
        <GitCompare className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        Both panels are simulated and read-only. A sweep can only rank what the
        detectors find — if the setups on the Bot tab are not the ones you would
        take, a favourable result here is measuring the wrong strategy carefully.
      </p>
    </div>
  );
}
