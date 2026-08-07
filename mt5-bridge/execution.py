"""
Execution guards: everything that decides whether an order may be placed.

Pure. No MetaTrader5, no network, no filesystem — the whole point is that the
rules deciding whether real money moves are testable on a laptop with no
terminal attached. `execution_service.py` is the thin layer that actually calls
`order_send`, and it contains no policy at all.

**This module contains no strategy.** It never decides *what* to trade. It is
handed an explicit order and answers one question: is this allowed right now.
Keeping the decision outside means a bug in the detectors cannot become a bug
that places orders.

The guards, and why each exists:

  * **Disarmed by default, and arming expires.** An arm-once flag is one
    nobody remembers to clear; every armed window has a deadline.
  * **Arming names the account.** The terminal's login must match what you
    armed for, so a session pointed at a different account than you thought
    refuses rather than trades.
  * **Demo unless told otherwise.** A real account is refused unless
    `allow_live` was explicitly set at arm time. This was written for someone
    who had just blown a live account and moved to demo; the default should
    not quietly follow them back.
  * **Dry run unless told otherwise.** Armed is not the same as firing.
  * **Every limit is checked here, not by the caller.** A client-side limit is
    a suggestion.

Every refusal returns *all* the reasons, not the first — a caller fixing one
violation at a time learns about the next one only by trying again, which with
orders means trying again for real.
"""
import time

# Deliberately small. These are the numbers a tired person at 2am is protected
# by, so they are conservative and must be raised on purpose.
DEFAULT_LIMITS = {
    'max_lot': 0.10,
    'max_risk_pct': 2.0,
    'max_open_positions': 2,
    # Stop trading for the day after this much of the starting balance is
    # gone. The single most useful limit there is: it ends the session that
    # would otherwise be spent trying to win it back.
    'max_daily_loss_pct': 5.0,
    'symbols': [],            # empty means "none" — an allowlist, never a blocklist
    'allow_live': False,
    'max_arm_minutes': 240,
}

SIDES = ('buy', 'sell')


def default_state():
    return {'armed_until': None, 'account': None, 'limits': dict(DEFAULT_LIMITS),
            'dry_run': True, 'day': None, 'realised_today': 0.0,
            'orders': 0, 'last_client_id': None, 'seen_client_ids': []}


def arm(state, minutes, account, limits=None, dry_run=True, now=None):
    """
    Open an armed window.

    `account` is required and is checked against the terminal at order time.
    Arming "whatever is connected" is how an order meant for demo reaches a
    live account, and that mistake is not recoverable by an apology.
    """
    now = int(now if now is not None else time.time())
    merged = {**DEFAULT_LIMITS, **(limits or {})}

    problems = []
    if not account:
        problems.append('account is required — arm for the account you mean, not '
                        'for whatever happens to be connected')
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        minutes = -1
    if minutes <= 0:
        problems.append('minutes must be positive')
    elif minutes > merged['max_arm_minutes']:
        problems.append(f'minutes ({minutes:g}) is above max_arm_minutes '
                        f'({merged["max_arm_minutes"]})')
    if not merged['symbols']:
        problems.append('symbols is empty, so no order could be placed. Name the '
                        'instruments explicitly — this is an allowlist')
    if merged['max_lot'] <= 0:
        problems.append('max_lot must be positive')
    if problems:
        return {'ok': False, 'problems': problems, 'state': state}

    return {'ok': True, 'problems': [], 'state': {
        **state,
        'armed_until': now + int(minutes * 60),
        'account': account,
        'limits': merged,
        'dry_run': bool(dry_run),
    }}


def disarm(state, now=None):
    """The kill switch. Always succeeds, always available, never argues."""
    return {**state, 'armed_until': None, 'dry_run': True}


def is_armed(state, now=None):
    now = int(now if now is not None else time.time())
    return bool(state.get('armed_until')) and state['armed_until'] > now


def _day(now):
    return time.strftime('%Y-%m-%d', time.gmtime(now))


def roll_day(state, now=None):
    """
    Reset the daily loss counter when the UTC date changes.

    UTC rather than the broker clock: the rest of this project joins on UTC,
    and a daily limit that resets on a different boundary from every other
    figure is a limit nobody can reconcile.
    """
    now = int(now if now is not None else time.time())
    today = _day(now)
    if state.get('day') == today:
        return state
    return {**state, 'day': today, 'realised_today': 0.0}


def check(state, order, account=None, positions=None, now=None, blackout=None):
    """
    May this order be placed? Returns every reason it may not.

    `account` is the terminal's own report of what it is connected to — not
    what the caller believes. `positions` is what is currently open. Both are
    passed in rather than fetched, so this stays pure and so the caller cannot
    accidentally check against stale values it forgot to refresh.
    """
    now = int(now if now is not None else time.time())
    state = roll_day(state, now)
    limits = state.get('limits') or DEFAULT_LIMITS
    reasons = []

    if not is_armed(state, now):
        reasons.append('not armed' if not state.get('armed_until')
                       else 'the armed window has expired')

    side = str(order.get('side', '')).lower()
    if side not in SIDES:
        reasons.append(f'side must be one of {", ".join(SIDES)}, got {order.get("side")!r}')

    symbol = order.get('symbol')
    if not symbol:
        reasons.append('symbol is required')
    elif symbol not in (limits.get('symbols') or []):
        reasons.append(f'{symbol} is not in the armed symbol list '
                       f'({", ".join(limits.get("symbols") or []) or "empty"})')

    lot = order.get('lot')
    if not isinstance(lot, (int, float)) or lot <= 0:
        reasons.append('lot must be a positive number')
    elif lot > limits['max_lot']:
        reasons.append(f'lot {lot} is above the armed max_lot {limits["max_lot"]}')

    # A market order with no stop is the position that becomes an account
    # balance of zero. There is no configuration to allow it.
    if order.get('stop') in (None, ''):
        reasons.append('stop is required — an order with no stop is refused '
                       'regardless of configuration')

    if account is not None:
        login = account.get('login')
        if state.get('account') is not None and login is not None \
                and str(login) != str(state['account']):
            reasons.append(f'the terminal is connected to account {login}, but '
                           f'arming was for {state["account"]}')
        if not limits.get('allow_live') and account.get('trade_mode') == 'real':
            reasons.append('this is a live account and allow_live was not set when '
                           'arming')

        balance = account.get('balance')
        risk = order.get('risk')
        if balance and risk:
            pct = risk / balance * 100
            if pct > limits['max_risk_pct']:
                reasons.append(f'risk {risk:.2f} is {pct:.1f}% of a {balance:.2f} '
                               f'balance, above the {limits["max_risk_pct"]}% cap')
        if balance:
            lost = -min(0.0, state.get('realised_today', 0.0))
            if lost and lost / balance * 100 >= limits['max_daily_loss_pct']:
                reasons.append(f'down {lost:.2f} today, at or past the '
                               f'{limits["max_daily_loss_pct"]}% daily loss limit — '
                               f'no further orders until tomorrow (UTC)')

    if positions is not None and len(positions) >= limits['max_open_positions']:
        reasons.append(f'{len(positions)} positions already open, at the limit of '
                       f'{limits["max_open_positions"]}')

    if blackout and blackout.get('in_blackout'):
        events = ', '.join(e.get('event', '?') for e in (blackout.get('events') or [])[:3])
        reasons.append(f'inside a news blackout{f" ({events})" if events else ""}')

    client_id = order.get('client_id')
    if client_id and client_id in (state.get('seen_client_ids') or []):
        # Not a rejection of the order so much as of the *repeat*. A retried
        # request that fills twice is the worst failure mode available here.
        reasons.append(f'client_id {client_id!r} has already been submitted; '
                       f'refusing to place it twice')

    return {
        'allow': not reasons,
        'reasons': reasons,
        'dry_run': bool(state.get('dry_run', True)),
        'armed_until': state.get('armed_until'),
        'would_place': ({'symbol': symbol, 'side': side, 'lot': lot,
                         'stop': order.get('stop'), 'target': order.get('target')}
                        if not reasons else None),
    }


def record_submission(state, client_id, now=None):
    """Remember a client_id so a retry cannot fill twice."""
    if not client_id:
        return state
    seen = list(state.get('seen_client_ids') or [])
    seen.append(client_id)
    # Bounded: this is a duplicate guard for retries, not an audit log. The
    # journal is the audit log.
    return {**state, 'seen_client_ids': seen[-500:], 'last_client_id': client_id,
            'orders': state.get('orders', 0) + 1}


def record_result(state, net, now=None):
    """
    Fold a realised result into today's running total.

    Only realised P&L counts toward the daily limit. Floating loss on an open
    position is not yet a loss, and treating it as one halts trading on noise.
    """
    state = roll_day(state, now)
    if net is None:
        return state
    return {**state, 'realised_today': round(state.get('realised_today', 0.0) + net, 2)}


def describe(state, now=None):
    """Human-readable status, including how long the arming has left."""
    now = int(now if now is not None else time.time())
    state = roll_day(state, now)
    armed = is_armed(state, now)
    return {
        'armed': armed,
        'dry_run': bool(state.get('dry_run', True)),
        'armed_until': state.get('armed_until'),
        'seconds_remaining': max(0, (state.get('armed_until') or 0) - now) if armed else 0,
        'account': state.get('account'),
        'limits': state.get('limits') or DEFAULT_LIMITS,
        'day': state.get('day'),
        'realised_today': state.get('realised_today', 0.0),
        'orders_submitted': state.get('orders', 0),
        'note': ('disarmed — no order can be placed' if not armed else
                 'armed in dry-run mode — orders are validated, not sent'
                 if state.get('dry_run', True) else
                 'ARMED AND LIVE — orders will be sent to the terminal'),
    }
