"""
Is the stop-loss distance recoverable from order history?

Deal history carries no SL, which is why risk %, R-multiples and average RR are
locked. Order history does carry `sl` — but whether it is *populated* depends on
the broker and on how the order was placed. A stop attached after the fact, or
set only on the terminal side, may never reach the order record.

That question has one answer per account and it cannot be reasoned about from
here, so this module exists to measure it: sample the orders that opened
positions, count how many carry a usable stop, and say plainly whether the
R-multiple work is viable.

Pure functions over the order shape mt5_client.orders_history() emits.
"""

# Below this many sampled orders the verdict is withheld: a coverage figure from
# three orders is not a fact about the account.
MIN_SAMPLE = 20

# MetaTrader 5 reports "no stop set" as 0.0 rather than null.
UNSET = 0.0


def is_entry_order(order):
    """
    Did this order *open* a position?

    A position's id is the ticket of the order that opened it — that identity is
    what separates an entry from the close that follows it. Both rows carry the
    same position_id and both are ordinary buys or sells, so without this test
    every position is counted twice. It showed up on a real account as 1079
    "entry orders" against 534 closed trades.

    Orders with no ticket fall back to the weaker test, which at least excludes
    close-by rows.
    """
    position_id = order.get('position_id')
    if not position_id:
        return False
    if str(order.get('type')) not in ('buy', 'sell', 'buy_limit', 'sell_limit',
                                      'buy_stop', 'sell_stop'):
        return False
    if order.get('position_by_id'):
        return False

    ticket = order.get('ticket')
    return ticket == position_id if ticket is not None else True


def _usable(value):
    return value is not None and float(value) != UNSET


def implied_risk(order):
    """
    Distance from entry price to stop, in price units.

    None when either side is missing — this is the number R-multiples divide by,
    and a zero denominator invented here would surface as an infinite R.
    """
    price = order.get('price_open')
    stop = order.get('sl')
    if price is None or not _usable(stop):
        return None
    distance = abs(float(price) - float(stop))
    return round(distance, 5) if distance > 0 else None


def coverage(orders, trades=None, sample_limit=200):
    """
    How much of the history carries a recoverable stop.

    `trades` is optional. When given, coverage is also reported against the
    trades that actually matter — the closed ones the analytics run on — because
    "60% of orders have a stop" and "60% of my trades can be scored in R" are
    different claims and only the second one is useful.
    """
    entries = [o for o in (orders or []) if is_entry_order(o)]
    sampled = entries[-sample_limit:] if sample_limit else entries

    with_sl = [o for o in sampled if _usable(o.get('sl'))]
    with_tp = [o for o in sampled if _usable(o.get('tp'))]
    risks = [r for r in (implied_risk(o) for o in with_sl) if r is not None]

    out = {
        'sampled_orders': len(sampled),
        'total_entry_orders': len(entries),
        'with_stop': len(with_sl),
        'with_target': len(with_tp),
        'stop_coverage_pct': (round(len(with_sl) / len(sampled) * 100, 1)
                              if sampled else None),
        'target_coverage_pct': (round(len(with_tp) / len(sampled) * 100, 1)
                                if sampled else None),
        'median_risk_distance': (sorted(risks)[len(risks) // 2] if risks else None),
        'examples': [{
            'position_id': o.get('position_id'),
            'symbol': o.get('symbol'),
            'type': o.get('type'),
            'price_open': o.get('price_open'),
            'sl': o.get('sl'),
            'tp': o.get('tp'),
            'risk_distance': implied_risk(o),
        } for o in sampled[-5:]],
    }

    if trades:
        # Only closed trades are scoreable, so they are the honest denominator.
        #
        # Matched against *every* entry order, not the sample: the sample exists
        # to bound the coverage percentage's cost, and reusing it here would cap
        # trade coverage at sample_limit/len(trades) and report missing stops
        # that are simply outside the window this function chose to look at.
        closed = [t for t in trades if not t.get('open')]
        with_stop_ids = {o['position_id'] for o in entries
                         if _usable(o.get('sl')) and o.get('position_id')}
        matched = [t for t in closed if t.get('position_id') in with_stop_ids]
        out['closed_trades'] = len(closed)
        out['trades_with_recoverable_stop'] = len(matched)
        out['trade_coverage_pct'] = (round(len(matched) / len(closed) * 100, 1)
                                     if closed else None)

    out.update(_verdict(out))
    return out


def _verdict(stats):
    """A sentence a human can act on, plus a machine-readable outcome."""
    sampled = stats['sampled_orders']
    if sampled < MIN_SAMPLE:
        return {
            'viable': None,
            'verdict': (f'Only {sampled} entry orders in this window — too few to '
                        f'judge. Widen the window (try from=0) and re-run.'),
        }

    pct = stats['stop_coverage_pct'] or 0
    scope = (f'{stats["with_stop"]} of {sampled} sampled entry orders '
             f'({pct:.0f}%) carry a stop-loss')

    if pct >= 80:
        return {
            'viable': True,
            'verdict': (f'{scope}. R-multiples are viable — the stop distance can '
                        f'be recovered without any manual journalling.'),
        }
    if pct >= 30:
        return {
            'viable': 'partial',
            'verdict': (f'{scope}. R-multiples would cover only part of the '
                        f'history. Worth building, but every R-based statistic '
                        f'must state its coverage or it will look like a claim '
                        f'about all your trades.'),
        }
    if pct > 0:
        return {
            'viable': False,
            'verdict': (f'{scope}. Too sparse to build on — the stop was most '
                        f'likely attached after the order was placed, which the '
                        f'order record does not capture. Journal it manually.'),
        }
    return {
        'viable': False,
        'verdict': (f'None of the {sampled} sampled entry orders carry a stop. '
                    f'This broker does not report it in order history, so the '
                    f'planned stop has to be journalled by hand.'),
    }
