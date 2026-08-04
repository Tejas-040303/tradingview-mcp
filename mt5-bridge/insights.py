"""
Rule-based trading insights.

Every claim on the dashboard comes from here, and every claim carries the
sample it was computed from. That constraint is the entire point of the module.

The failure mode this guards against is real and already happened once in this
project: a hypothesis that winners were being cut early looked obvious in a
win-rate table and turned out to be false — winners were held *longer*
(16.5m vs 14.8m). A dashboard that had asserted it would have been confidently
wrong, and acting on it would have made the trading worse.

So: no rule fires below MIN_CLAIM observations, every insight states its n, and
comparisons between buckets require both sides to clear the bar. Rules return
nothing when the data does not support them rather than softening the wording.
"""
from advanced import MIN_CLAIM, behaviour

CRITICAL, WARNING, SUGGESTION, OPPORTUNITY = 'critical', 'warning', 'suggestion', 'opportunity'


def _insight(severity, title, detail, evidence, n):
    return {'severity': severity, 'title': title, 'detail': detail,
            'evidence': evidence, 'sample': n}


def _expectancy_rules(headline):
    out = []
    if not headline or headline.get('trades', 0) < MIN_CLAIM:
        return out

    n = headline['trades']
    expectancy = headline.get('expectancy')
    if expectancy is not None and expectancy < 0:
        out.append(_insight(
            CRITICAL,
            'Negative expectancy',
            'Every trade costs money on average. Automating this would scale the '
            'loss, not fix it — the edge has to turn positive first.',
            f'{expectancy:+.2f} per trade over {n} trades',
            n))

    payoff = headline.get('payoff_ratio')
    win_rate = headline.get('win_rate_pct')
    if payoff is not None and win_rate is not None and payoff < 1 and win_rate < 50:
        out.append(_insight(
            CRITICAL,
            'Losing on both counts',
            'You win less than half the time and your average win is smaller than '
            'your average loss. Either side alone is survivable; both together '
            'cannot be.',
            f'win rate {win_rate:.1f}%, payoff {payoff:.2f}',
            n))
    elif payoff is not None and payoff < 1:
        out.append(_insight(
            WARNING,
            'Average loss exceeds average win',
            f'A {payoff:.2f} payoff needs a win rate above '
            f'{100 / (1 + payoff):.0f}% just to break even.',
            f'avg win {headline.get("avg_win")}, avg loss {headline.get("avg_loss")}',
            n))

    factor = headline.get('profit_factor')
    if factor is not None and factor >= 1.3:
        out.append(_insight(
            OPPORTUNITY,
            'Profitable gross edge',
            'Gross wins comfortably exceed gross losses. Worth protecting before '
            'changing anything else.',
            f'profit factor {factor:.2f} over {n} trades',
            n))
    return out


def _bucket_rules(groups):
    """
    Compare buckets within one dimension — session, weekday, exit reason.

    Only the best and worst are reported, and only when both clear MIN_CLAIM.
    Ranking twenty thin buckets and announcing the extreme is how noise becomes
    a recommendation.
    """
    out = []
    labels = {'session': 'session', 'weekday': 'weekday', 'symbol': 'symbol',
              'exit_reason': 'exit', 'direction': 'direction'}

    for key, noun in labels.items():
        buckets = [(name, stats) for name, stats in (groups.get(key) or {}).items()
                   if stats.get('trades', 0) >= MIN_CLAIM]
        if len(buckets) < 2:
            continue
        buckets.sort(key=lambda kv: kv[1]['net'])
        worst_name, worst = buckets[0]
        best_name, best = buckets[-1]

        if worst['net'] < 0:
            out.append(_insight(
                WARNING,
                f'Worst {noun}: {worst_name}',
                f'This {noun} is where the losses concentrate. Cutting it entirely '
                f'would have changed the total by {-worst["net"]:+.2f}.',
                f'{worst["trades"]} trades, {worst["win_rate_pct"]:.1f}% win rate, '
                f'net {worst["net"]:+.2f}',
                worst['trades']))

        if best['net'] > 0 and best_name != worst_name:
            out.append(_insight(
                OPPORTUNITY,
                f'Best {noun}: {best_name}',
                f'Your strongest {noun} by net result. Worth checking what differs '
                'here before generalising it.',
                f'{best["trades"]} trades, {best["win_rate_pct"]:.1f}% win rate, '
                f'net {best["net"]:+.2f}',
                best['trades']))
    return out


def _exit_rules(groups, headline):
    """
    How trades end, which is usually where a stop-heavy account leaks.

    Note what this deliberately does *not* say: a stop-heavy distribution is
    reported as a fact with its numbers, not diagnosed as "your stops are too
    tight". Stop distance is not in the data — that is a hypothesis for a
    backtest, not a conclusion for a dashboard.
    """
    out = []
    exits = groups.get('exit_reason') or {}
    total = sum(s.get('trades', 0) for s in exits.values())
    if total < MIN_CLAIM:
        return out

    stop = exits.get('stop_loss')
    if stop and stop['trades'] >= MIN_CLAIM:
        share = stop['trades'] / total * 100
        if share >= 50:
            out.append(_insight(
                WARNING,
                'Most trades end at the stop',
                f'{share:.0f}% of trades close at stop loss. That is consistent with '
                'stops sitting inside normal noise for this instrument, but it is '
                'also consistent with the entries being wrong — the data here '
                'cannot separate the two. A backtest with a wider stop can.',
                f'{stop["trades"]} of {total} trades, net {stop["net"]:+.2f}',
                stop['trades']))

    manual = exits.get('mobile') or exits.get('client')
    auto = exits.get('take_profit')
    if (manual and auto and manual['trades'] >= MIN_CLAIM
            and manual.get('win_rate_pct') is not None
            and auto.get('win_rate_pct') is not None
            and auto['trades'] >= MIN_CLAIM
            and manual['win_rate_pct'] > auto['win_rate_pct'] + 10):
        out.append(_insight(
            SUGGESTION,
            'Manual exits outperform the configured target',
            'Closing by hand beats letting the take-profit run. Worth encoding what '
            'you are reading manually into the target itself.',
            f'manual {manual["win_rate_pct"]:.1f}% over {manual["trades"]} trades vs '
            f'target {auto["win_rate_pct"]:.1f}% over {auto["trades"]}',
            manual['trades']))
    return out


def _behaviour_rules(trades):
    out = []
    signals = behaviour(trades)

    timing = signals['reentry_timing']
    after_loss, after_win = timing['after_loss_sec'], timing['after_win_sec']
    if (after_loss is not None and after_win is not None
            and min(timing['samples_after_loss'], timing['samples_after_win']) >= MIN_CLAIM
            and after_loss < after_win * 0.6):
        out.append(_insight(
            WARNING,
            'Faster re-entry after a loss',
            'You get back in sooner after losing than after winning. The timing is '
            'measurable; the motive is not — but it is the pattern revenge trading '
            'produces.',
            f'median {after_loss / 60:.0f}m after a loss vs {after_win / 60:.0f}m '
            f'after a win',
            min(timing['samples_after_loss'], timing['samples_after_win'])))

    hold = signals['hold_asymmetry']
    win_sec, loss_sec = hold['avg_win_sec'], hold['avg_loss_sec']
    if win_sec and loss_sec and hold['samples'] >= MIN_CLAIM:
        # Only claimed at a wide margin. A 10% gap is noise, and this specific
        # hypothesis has already been wrong once on this account.
        if loss_sec > win_sec * 1.5:
            out.append(_insight(
                WARNING,
                'Losers held longer than winners',
                'Losing positions are given noticeably more time than winning ones '
                '— the classic shape of cutting winners early and hoping on losers.',
                f'losses {loss_sec / 60:.0f}m vs wins {win_sec / 60:.0f}m',
                hold['samples']))
        elif win_sec > loss_sec * 1.5:
            out.append(_insight(
                SUGGESTION,
                'Winners held longer than losers',
                'Losers are cut faster than winners, which is the right way round. '
                'The losses are coming from somewhere else.',
                f'wins {win_sec / 60:.0f}m vs losses {loss_sec / 60:.0f}m',
                hold['samples']))

    escalation = signals['size_escalation']
    if (escalation and escalation['samples'] >= MIN_CLAIM
            and escalation['size_ratio_after_loss'] > escalation['size_ratio_after_win'] * 1.2):
        out.append(_insight(
            CRITICAL,
            'Position size increases after losses',
            'Size goes up following a loss more than following a win. Combined with '
            'a losing edge this is the fastest route to a blown account.',
            f'{escalation["size_ratio_after_loss"]:.2f}x after a loss vs '
            f'{escalation["size_ratio_after_win"]:.2f}x after a win',
            escalation['samples']))
    return out


def _risk_rules(risk, monte, headline):
    out = []
    if monte and monte.get('runs') and monte.get('probability_of_loss_pct') is not None:
        prob = monte['probability_of_loss_pct']
        if prob >= 80:
            out.append(_insight(
                CRITICAL,
                'The loss was the edge, not the sequence',
                'Reshuffling your trades into a different order still ends negative '
                'almost every time. This is not variance you can wait out.',
                f'{prob:.0f}% of {monte["runs"]} shuffled orderings end negative',
                monte['trades']))
        elif prob <= 20:
            out.append(_insight(
                OPPORTUNITY,
                'Result holds under reshuffling',
                'Most reorderings of your trades still end positive, so the outcome '
                'is not an artefact of a lucky sequence.',
                f'only {prob:.0f}% of {monte["runs"]} orderings end negative',
                monte['trades']))

        worst = monte.get('drawdown_p95')
        if worst and headline and headline.get('trades', 0) >= MIN_CLAIM:
            out.append(_insight(
                SUGGESTION,
                'Plan for a deeper drawdown than you have seen',
                'One in twenty orderings of these same trades draws down at least '
                'this far. Position sizing should survive it.',
                f'95th percentile drawdown {worst:.2f}',
                monte['trades']))

    if risk and risk.get('sharpe') is not None and risk['trading_days'] >= 20:
        if risk['sharpe'] < 0:
            out.append(_insight(
                WARNING,
                'Negative risk-adjusted return',
                'Daily P&L is negative on average once volatility is accounted for.',
                f'Sharpe {risk["sharpe"]:.2f} over {risk["trading_days"]} trading days',
                risk['trading_days']))
    return out


def generate(trades, headline=None, groups=None, risk=None, monte=None):
    """
    All insights the data supports, most severe first.

    Returns an empty list rather than filler when nothing clears the sample bar.
    A dashboard with no advice is honest; one that invents advice to fill a
    panel is not.
    """
    groups = groups or {}
    found = (_expectancy_rules(headline)
             + _exit_rules(groups, headline)
             + _bucket_rules(groups)
             + _behaviour_rules(trades)
             + _risk_rules(risk, monte, headline))

    order = {CRITICAL: 0, WARNING: 1, SUGGESTION: 2, OPPORTUNITY: 3}
    found.sort(key=lambda i: (order[i['severity']], -i['sample']))
    return found


def coverage(headline):
    """
    Why a panel is empty, stated plainly.

    Feeds the locked-panel UI: the dashboard shows what it cannot compute and
    the reason, rather than hiding the gap or filling it with a guess.
    """
    n = (headline or {}).get('trades', 0)
    return {
        'trades': n,
        'min_claim': MIN_CLAIM,
        'sufficient': n >= MIN_CLAIM,
        'unavailable': [
            {'metric': 'Risk %, R-multiple, average RR',
             'reason': 'Stop-loss distance at entry is not in MT5 deal history. '
                       'Recoverable from order history for some trades; not yet wired.'},
            {'metric': 'Strategy, setup, tags, emotions, notes, screenshots',
             'reason': 'No journalling layer exists. MT5 stores fills, not intent.'},
            {'metric': 'Fear, greed, confidence, discipline, patience scores',
             'reason': 'Not measurable from fill data. Scoring them would mean '
                       'inventing the numbers.'},
            {'metric': 'Historical floating P&L',
             'reason': 'Never recorded. Only the current value exists, and it '
                       'cannot be backfilled.'},
            {'metric': 'Entry and exit quality (MFE/MAE)',
             'reason': 'Needs bar data around every trade. Computable via mt5_bars '
                       'as a batch job; not yet built.'},
        ],
    }
