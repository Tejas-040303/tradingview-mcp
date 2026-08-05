# Roadmap

Where this is going, and what each step depends on.

Read `HANDOVER.md` first if you are picking this up cold — it explains why the
existing pieces are shaped the way they are, which constrains most of what
follows.

---

## Done

| | |
|---|---|
| **TradingView MCP** | 84 tools over CDP — chart reading, Pine graphics, replay, drawing, alerts |
| **MT5 bridge** | Read-only HTTP on `127.0.0.1:8765`, stdlib only, GET-only |
| **MT5 MCP** | 13 read-only tools |
| **Economic calendar** | `calendar_export.mq5` → JSON → blackout checks, self-describing offset |
| **Analytics** | Expectancy, payoff, drawdown, streaks, groupings |
| **Trade pairing** | Fills → trades by `position_id`, with holding times |
| **Advanced analytics** | Sharpe/Sortino, Monte Carlo, heatmaps, behaviour signals |
| **Insight engine** | Rule-based coaching with sample-size guards |
| **Excursion analysis** | MAE/MFE and post-exit behaviour from bar data |
| **Dashboard** | React app — Status and Analytics tabs, one URL |
| **Launcher** | `npm run start:all` — one command, builds on demand |
| **CI** | Lint, Python + Node suites, dashboard build verification |

---

## Next: journalling

MT5 stores **what happened**. It has no idea **why**. That gap is why five
panels on the dashboard are locked, and why skipped setups are invisible.

Ordered so the stages needing nothing from you come first.

### ~~1. Post-trade excursion~~ — done

MAE/MFE, capture ratio, and where price went 5/15/60 minutes after each exit.
Needs no discipline and applies retroactively to every existing trade.

**Run this on real data before designing anything around stop distance.** The
figure that matters is `by_exit_reason.stop_loss.post_300.reached_entry_pct` —
the share of stop-outs where price returned to entry within five minutes. It
settles the question the exit distribution cannot.

### 2. Recover stop distance → R-multiples

**Needs nothing from you. Highest value per unit of work.**

`history_deals_get` carries no SL/TP, but `history_orders_get` does. If the
opening order's stop is recoverable, this unlocks:

- **R-multiple per trade** — turns "−5.20" into "−1R", comparable across sizes
- Risk % and risk utilisation
- **Expectancy in R**, which is the number that transfers to a backtest
- MAE/MFE expressed in R rather than price

**First step is verification, not code:** confirm the opening order for a real
historical trade actually carries a usable `sl`. If brokers routinely report
zero, this stage dies and the stop must be journalled manually instead.

### 3. Journal store and write service

**Decided: a separate write service on its own port.**

The bridge's GET-only property is what makes it safe to point an LLM at a live
account. One fewer process is not worth weakening it to "GET-only except…".

- **Storage:** SQLite at `mt5-bridge/journal.db` via stdlib `sqlite3` — no new
  dependency, real queries, transactional, one file to back up
- **Rejected:** a JSON file (corrupts under concurrent writes, no querying) and
  anything hosted (broker data would leave the machine)
- **Key:** `(account_login, position_id)` — position IDs are per-account and can
  collide across accounts

Tables: `trade_journal`, `signals`, `attachments`, `tags`. Fields worth
capturing that nothing else can supply: `strategy`, `setup`, `planned_stop`,
`followed_plan`, `mistakes`, `emotion_before/after`, `confidence`.

`followed_plan` is likely the largest single split in a discretionary trader's
data, and it is currently invisible.

### 4. TradingView chart capture

Detect the trade from MT5, mark entry/SL/TP on the TradingView chart, screenshot
it, bind to `position_id`.

**The thing that would ruin it:** a screenshot taken after the trade closed
shows the bars that came *after* your entry. That image is worthless for
reviewing the entry decision because it contains the answer. The entry capture
**must** set `chart_set_visible_range` to end at the entry bar. Get this wrong
and you have built a machine for hindsight bias.

Three more caveats:

- No native long/short position widget in `draw_shape` — use rectangles from
  entry to TP and entry to SL
- Symbol mapping (`GOLD.i#` → `OANDA:XAUUSD`) needs a maintained table
- TradingView prices are not your broker's; label the image accordingly

**Storage is a cache, not an archive.** These are a pure function of trade data
plus chart settings, so nothing is lost by deleting one. Keep ~7 days on disk;
older trades get a **Regenerate** button in the drawer. Bounded disk, nothing
permanently lost, and a later change to the markup style regenerates old
captures rather than leaving them inconsistent.

### 5. Signals — the setups you skipped

A skipped setup leaves **zero trace** in MT5. If the problem is hesitation
rather than over-trading, every analysis so far is structurally blind to it.

Answers: hit rate on setups taken vs skipped, and whether the skipped ones would
have won.

Requires the most discipline, so it comes last.

---

## Then: strategy and backtest

**Gated on discussion before any code.**

The system can now describe what happened in considerable detail. It cannot yet
answer "would a different rule have done better", which is the question every
finding so far has ended on.

Minimum shape: a strategy definition, a bar-replay engine reusing the existing
analytics so backtest and live numbers are computed by the same code, and
walk-forward validation. The first thing to test is already known — **the same
entries with a wider stop.**

## Later: execution

Only after a strategy shows positive expectancy in R, out of sample.

Execution belongs in **its own process**, never in the read-only bridge. The
separation decided in stage 3 is deliberately the same shape.

---

## Smaller things

- **ESLint for `dashboard-app/`** — `npm run lint` covers `src/` only. Needs a
  second flat config and a pass over ~2,500 lines of JSX
- **Excursion panels in the dashboard** — the data is exposed but not yet drawn
- **Journal coverage indicator** — once journalling exists, every derived panel
  needs to show what fraction of trades were journalled. A journal at 20%
  coverage produces selection-biased analytics that look authoritative, because
  people journal memorable trades
- **Verify insight rules against the real account** — they have only ever run on
  synthetic data
- **News layer beyond the calendar** — Forex Factory or MT Newswires, discussed
  early and never built

---

## Not planned

- **Fear, greed, confidence scores.** Not measurable from fills. Scoring them
  means inventing them, which breaks the rule the rest of the system holds to.
  Self-reported emotion fields are legitimate as *your record* and must be
  labelled as such — never presented beside computed metrics as the same kind of
  number.
- **Hosted or multi-user anything.** Everything binds `127.0.0.1` because it
  serves live brokerage data.
- **Write access in the read-only bridge.** See `HANDOVER.md`.
