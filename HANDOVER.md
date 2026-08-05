# Handover

Why this codebase is shaped the way it is.

`README.md` says what things do. `ROADMAP.md` says what comes next. This file
records the decisions and the mistakes, because those are what a future
contributor — human or model — cannot recover from reading the code.

If you change something here, change this file too. A stale rationale is worse
than none.

---

## The one rule everything else follows

**Never invent a number.**

When a value cannot be computed, the answer is `null` and, wherever it reaches a
person, a reason. This is not stylistic. This system exists to inform decisions
about money, and a plausible fabricated number is worse than a visible gap
because it cannot be argued with.

Concretely, all of these are deliberate:

| Case | Returns | Why not the obvious thing |
|---|---|---|
| Profit factor with no losing trades | `null` | Infinity cannot be plotted or compared |
| Drawdown % without a starting balance | `null` | A percentage needs a base; inventing one understates risk on a small account |
| `time_utc` with an unknown broker offset | `null` | A guessed timestamp silently misaligns every join |
| Sortino with no losing days | `null` | Dividing by zero downside is not "infinitely good" |
| Kelly on a losing edge | **negative, unclamped** | Clamping to zero hides exactly the case that matters |
| A heatmap cell you never traded | `null`, not `0` | A day you did not trade is not a break-even day |
| Coaching insight below 20 samples | *nothing at all* | Silence is an honest answer; filler is not |

The frontend mirrors this: `lib/format.js` renders `null` as an em-dash, never
as `0`. That is the last place the distinction could be lost.

---

## Mistakes that shaped the code

These are recorded because each one produced a guard that looks arbitrary
without the story.

### The holding-time hypothesis that was false

Early on, analytics over raw deals showed −1.80 expectancy at a 43% win rate
with a 0.82 payoff ratio. I offered the textbook explanation: **cutting winners
early and letting losers run to the stop.**

Trade pairing then made it measurable for the first time — a deal has no
duration, only a paired trade does. The answer:

```
winners held 16.5 min    losers held 14.8 min
```

Winners were held *slightly longer*. The hypothesis was wrong, and it had felt
obviously right.

**What it left behind:** `insights.py` only claims holding asymmetry at a 1.5×
margin, and `test_advanced.py` has a test that constructs almost exactly that
16.5-vs-14.8 ratio and asserts **no claim is made**. If you loosen that
threshold, that test is what should stop you.

### A metric that was true and meaningless

`excursion.py` answers "did price come back to my entry after stopping me out?"
The first real run produced:

```
mobile       n=28   back-to-entry 100.0%
stop_loss    n=38   back-to-entry  71.1%
take_profit  n=25   back-to-entry 100.0%
```

Both winning buckets at 100%, **outranking the number that meant something**. It
was vacuous: a profitable exit already sits beyond the entry, so price is
trivially at-or-past it forever after.

Anyone reading that table would have concluded manual exits get shaken out more
than stops do.

**What it left behind:** `shakeout_applicable`, which is `False` for winning
exits, and percentages computed over `shakeout_samples` rather than all trades.
Two regression tests pin it.

### Off-by-one at a bucket edge, twice

`exp(log(x))` does not round-trip. Geometric bucket edges came back as
`30.000000000000004` and `7199.999999999999`, so **the fastest and slowest
trades fell outside their own buckets** — silent data loss in the holding-time
chart and the P&L histogram.

The first fix over-corrected: dropping the lower bound on the final bucket made
it swallow everything, counting 13 of 7 trades. The same test caught both.

**What it left behind:** outer edges anchored to real min/max values, and a
half-open comparison everywhere except the final bucket. Tests assert every
trade lands in exactly one bucket.

### Three ways an aggregate lied

All three surfaced on the same real-account run, and all three produced numbers
that looked authoritative and meant nothing.

**Price distances averaged across instruments.** `avg_mae` came back as 12.69 —
an average of gold points and bitcoin dollars. The account trades eight symbols
whose stop distances span 0.00044 to 145. Percentages and ratios are
dimensionless and survive aggregation; prices do not. Cross-symbol buckets now
withhold price statistics with a note, and `by_symbol` exists so they can be
read where the instrument is fixed.

**A mean of ratios.** `capture_ratio` is `realised / mfe`, so a trade offering
0.01 that lost 5 contributes −500. The mean read −5.9; the median read 0.5. Any
ratio aggregated across trades is a median here.

**A verdict read at the wrong horizon.** The shakeout rate was hardcoded to the
five-minute figure while the account holds for fifteen. It reported 37% and
called the result "partial" when the fifteen-minute figure was 57% and the
hourly 77%. The horizon now follows the median holding time.

A fourth, related: the inconsistency finding was gated behind the survival
sample. It is a fact about the stops, needs no winners, and was being hidden
exactly when there were too few winners to say anything else.

### The broker clock

MT5 reports every timestamp on the **server clock**, not UTC. On XM that is
UTC+3.

This was fixed once for quotes, bars and deals — then the same bug was found
again in the calendar path, where it made the news blackout wrong by three
hours. A blackout check that is three hours out is worse than no blackout check.

**What it left behind:**

- Every MT5 timestamp is labelled twice: `time_server*` (no `Z`, because it is
  not UTC) and `time_utc*`
- **Always join on `time_utc`.** Mixing the two misaligns everything by the
  broker offset while looking perfectly reasonable
- `history_deals_get` and `copy_rates_range` interpret their bounds on the
  server clock, so every window is shifted *before* querying — miss this and the
  trades at each edge silently lose their data
- `blackout_status()` returns `success: false, blackout: null` rather than
  comparing mismatched clocks
- `infer_server_offset()` rejects offsets beyond ±14h, which is the stale
  weekend-tick guard

### A variable that shadowed a parameter

`deals()` had `offset = server_utc_offset()`, which clobbered the pagination
parameter of the same name. Every paginated call raised `TypeError`. It reached
the user immediately.

**What it left behind:** the local is named `clock`, and `test_mt5_client.py`
exists. The regression test was verified by reintroducing the bug and watching
it fail.

---

## Architectural decisions

### Read-only by construction

The bridge accepts `GET` and nothing else — every other verb returns 405,
`order_send` is absent from the client, and no route maps to an order operation.

This is not defence in depth for its own sake. It is what makes it safe to point
an LLM at a live brokerage account. A bug in analytics can produce a wrong
number; it cannot produce a trade.

**Decided:** journalling writes go to a **separate service on its own port**,
not to POST routes on the bridge. One fewer process is not worth turning
"GET-only" into "GET-only except…", which is a materially weaker property to
reason about. The thing that reads your account can never write; the thing that
writes only ever touches your own notes.

### Deals are not trades

MT5 reports *fills*. An entry and an exit are separate rows sharing a
`position_id`; a partial close adds more. Everything built on raw deals counts
fills — a scaled-in position closed in two parts reads as four "trades".

`pair_trades()` groups by `position_id` with volume-weighted entry and exit.
Two cases are **labelled rather than guessed**:

- Still-open positions have `exit_price: null`, not "now"
- A window starting mid-position gets `entry_missing: true`, not a fabricated
  entry — though direction is still recoverable, since a *sell* close means it
  was a long

`position_id` is per-account, so the journal's primary key must be
`(account_login, position_id)`, not `position_id` alone.

### The dashboard computes nothing

Win rate, expectancy and drawdown all come from the API. The React app draws.

The alternative — fetch once, filter in JavaScript — is snappier, but it means a
second implementation of every statistic. Those drift, and the first symptom is
a stat tile disagreeing with the table directly beneath it. On loopback the
round-trip is not worth that risk.

Only search and sort are client-side, and both operate on rows already fetched.

### Sample size is visible, not hidden

With 202 trades on a single symbol, most heatmap cells are noise. The UI says
so: cells below threshold are drained of colour and each grid states how many of
its own cells are unreliable — *"26 of 26 cells fall below 10 trades and are
shown faded."*

A deep green cell built on two trades looks identical to one built on two
hundred. That is the entire failure mode of a heatmap, and it is why
`MIN_CELL = 10` and `MIN_CLAIM = 20` exist.

### Time-based grouping uses entry, not exit

"Which session do I lose money in" is a question about when the position was
*opened*. Grouping by exit would credit a London entry that ran into New York to
the wrong bucket.

### Rules report, they do not diagnose

`insights.py` describes a stop-heavy distribution with its numbers and **both**
candidate explanations, and states plainly that the data cannot separate them.
It does not say "your stops are too tight" — stop distance is not in deal
history. That is a backtest question, not a dashboard conclusion.

`excursion.py` was built specifically to settle it with bar data instead.

---

## Layout

```
src/                    Node MCP servers — 84 TradingView tools, 13 MT5 tools
  core/                 Transport and client logic
  tools/                MCP tool definitions
mt5-bridge/             Python, stdlib only
  bridge.py             Read-only HTTP, 127.0.0.1, GET-only
  mt5_client.py         All terminal I/O; MetaTrader5 imported lazily
  normalize.py          Pure: timeframes, enums, timestamps, calendar, blackout
  analytics.py          Pure: pairing, curves, groupings, distributions
  advanced.py           Pure: Sharpe/Sortino, Monte Carlo, heatmaps, behaviour
  insights.py           Pure: coaching rules with sample guards
  excursion.py          Pure: MAE/MFE and post-exit behaviour
  strategy.py           Pure: strategy-as-data, validation, position sizing
  detectors.py          Pure: FVG / sweep / order block / fib, with knowable_at
  simulate.py           Pure: bar replay, emitting the pair_trades() shape
  dashboard/            Plain HTML fallback pages + built React bundle (app/)
dashboard-app/          React source (Vite + Tailwind + Recharts + TanStack)
scripts/start.js        One-command launcher
```

**The `_deps` convention:** modules take injectable dependencies so pure logic
is testable without a terminal. Every `mt5-bridge/*.py` module except
`mt5_client.py` imports nothing platform-specific and runs on Linux in CI.

**Test counts:** 414 Python, 22 Node MT5, plus the wider Node suite. CI runs
lint, both suites, and a dashboard build that verifies the bundle is actually
servable — a wrong `base` path builds cleanly and produces a blank page.

---

## Things that will bite you

- **`MetaTrader5` is Windows-only.** The bridge starts anyway and `/health`
  explains why it is down. Do not "fix" this by failing at startup.
- **The bridge answers 503 when MT5 is unreachable.** The launcher treats *any*
  HTTP response as "up" — an earlier version used `res.ok` and reported a
  working bridge as down, then waited for it forever.
- **`$env:` variables die with the PowerShell window.** Hence calendar
  autodiscovery rather than a required `MT5_CALENDAR_FILE`.
- **Symbol names are not guessable.** Spot gold is `GOLD.i#` on XM; `XAUUSD` may
  not exist. Use `mt5_symbol_search`. The `#` needs URL-encoding, which
  `URLSearchParams` handles.
- **Restarting the bridge is not enough after an MCP change.** Claude Code loads
  the tool list at startup, so a new tool needs Claude Code restarted too.
- **A stale `bridge.py` process is the most common "it didn't update".** The
  launcher deliberately leaves a running bridge alone. Kill it explicitly:
  `netstat -ano | findstr :8765`.
- **Excursion analysis needs M1 history downloaded** in the terminal for the
  period, or it returns thin results.

---

## Open questions

- **Is 20 the right `MIN_CLAIM`?** It is a judgement, not a derivation. Nothing
  has tested whether rules fire correctly at that threshold on real data.
- **Can stop distance be recovered from `history_orders_get`?** It carries
  `sl`/`tp`. If it works, R-multiples unlock with no journalling at all. This
  has not been verified against a live account.
- **The insight rules have never run against the real account** — only synthetic
  data. They are rendered as confident cards. Verify before trusting.
