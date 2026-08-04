import { AlertTriangle, Lightbulb, Lock, ShieldAlert, Sparkles } from 'lucide-react';
import { Badge, Card, CardTitle, Empty, Reveal } from '@/components/ui';
import { SEVERITY, cn } from '@/lib/api';

const ICONS = {
  critical: ShieldAlert,
  warning: AlertTriangle,
  suggestion: Lightbulb,
  opportunity: Sparkles,
};

/**
 * The coaching panel.
 *
 * Every card shows the sample it was computed from, because that is what makes
 * it checkable rather than authoritative. The API refuses to emit a claim below
 * 20 observations, so an empty panel here is a real answer — the data does not
 * support advice yet — and is rendered as such instead of being padded.
 */
export default function Insights({ insights, coverage }) {
  const rows = insights || [];

  return (
    <Card className="mb-5">
      <CardTitle hint="Rule-based, computed from the filtered set. Each card states its sample size."
        right={rows.length > 0 && (
          <Badge tone="neutral">{rows.length} finding{rows.length === 1 ? '' : 's'}</Badge>
        )}>
        Trading coach
      </CardTitle>

      {rows.length === 0 ? (
        <Empty icon={Lightbulb}>
          {coverage && !coverage.sufficient
            ? `Not enough trades to say anything reliable — ${coverage.trades} so far, ${coverage.min_claim} needed.`
            : 'No rule fired on this selection. Nothing here is being withheld; the data simply does not support a claim.'}
        </Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((insight, i) => {
            const meta = SEVERITY[insight.severity] || SEVERITY.suggestion;
            const Icon = ICONS[insight.severity] || Lightbulb;
            return (
              <Reveal key={`${insight.title}-${i}`} delay={i * 0.03}>
                <div className={cn('h-full rounded-xl border border-line bg-elevated p-3.5',
                  'ring-1 ring-inset transition-shadow hover:shadow-lift', meta.ring)}>
                  <div className="flex items-center gap-2">
                    <Icon className={cn('h-4 w-4 shrink-0', meta.text)} />
                    <span className={cn('text-[11px] font-semibold uppercase tracking-wider', meta.text)}>
                      {meta.label}
                    </span>
                    <span className="tabular ml-auto text-[11px] text-muted">n={insight.sample}</span>
                  </div>
                  <h3 className="mt-2 text-[13px] font-semibold text-ink">{insight.title}</h3>
                  <p className="mt-1 text-xs leading-relaxed text-muted">{insight.detail}</p>
                  <p className="tabular mt-2 rounded-md bg-surface px-2 py-1 text-[11px] text-muted">
                    {insight.evidence}
                  </p>
                </div>
              </Reveal>
            );
          })}
        </div>
      )}
    </Card>
  );
}

/**
 * Locked panels.
 *
 * The spec asked for metrics this data cannot produce. Rather than omit them
 * silently or fill them with plausible numbers, each is shown with the reason
 * it is unavailable — which also doubles as the roadmap for unlocking it.
 */
export function LockedPanels({ coverage }) {
  const rows = coverage?.unavailable || [];
  if (!rows.length) return null;

  return (
    <Card className="mb-5">
      <CardTitle hint="Shown rather than hidden. Each of these needs data MetaTrader 5 does not report, or a feature that does not exist yet.">
        Not available from this data
      </CardTitle>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {rows.map(row => (
          <div key={row.metric}
            className="rounded-xl border border-dashed border-line bg-elevated/40 p-3.5">
            <div className="flex items-center gap-2">
              <Lock className="h-3.5 w-3.5 text-muted" />
              <h3 className="text-[13px] font-medium text-muted">{row.metric}</h3>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-muted/80">{row.reason}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}
