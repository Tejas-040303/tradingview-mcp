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
| **Dashboard** | React app — Status, Analytics, Bot and Research tabs, one URL |
| **Launcher** | `npm run start:all` — one command, builds on demand |
| **CI** | Lint, Python + Node suites, dashboard build verification |
| **Journal** | `journal_service.py` on 8766 — signals, decisions, screenshots, retention. The only writable service |
| **Strategy engine** | Strategy-as-data, detectors, bar replay, parameter sweep with walk-forward, paper view, reconcile |
| **Strategy 1** | Multi-timeframe liquidity sweep — detection, backtest, walk-forward, served over the bridge |
| **Execution** | `execution_service.py` on 8767 — disarmed, dry-run, demo-only by default. Started only with `--exec` |
| **Trade chart capture** | `capture_trade` — a trade marked on the chart, filed against its `position_id`, in a frame that stops at the entry bar |

---

## Journalling

MT5 stores **what happened**. It has no idea **why**. That gap is why several
panels on the dashboard are locked, and why skipped setups are invisible.

Ordered so the stages needing nothing from you come first. Stages 1, 3 and 4 are
done; 2 and 5 are built but unfinished in the ways marked below.

### ~~1. Post-trade excursion~~ — done

MAE/MFE, capture ratio, and where price went 5/15/60 minutes after each exit.
Needs no discipline and applies retroactively to every existing trade.

**Run this on real data before designing anything around stop distance.** The
figure that matters is `by_exit_reason.stop_loss.post_300.reached_entry_pct` —
the share of stop-outs where price returned to entry within five minutes. It
settles the question the exit distribution cannot.

### 2. Recover stop distance → R-multiples — *half done*

`history_deals_get` carries no SL/TP, but `history_orders_get` does. If the
opening order's stop is recoverable, this unlocks:

- **R-multiple per trade** — turns "−5.20" into "−1R", comparable across sizes
- Risk % and risk utilisation
- **Expectancy in R**, which is the number that transfers to a backtest
- MAE/MFE expressed in R rather than price

**The verification exists**: `stops.py` and `/diagnose/stops` sample the orders
that opened positions and answer, per account, whether the stop is actually
populated. `stopsize.py` separately recovers the typical stop distance from the
trades that hit one, since a stop-loss exit happens *at* the stop.

**What is not done is the wiring.** Real trades still carry no R-multiple — only
simulated ones do, out of `simulate.py`. The dashboard's risk tab says so rather
than showing a number. Running `/diagnose/stops` against the real account is the
next step, and it decides whether this is a join or a journalling problem.

### ~~3. Journal store and write service~~ — done

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

### ~~4. TradingView chart capture~~ — done

`capture_trade` (and `tv capture-trade`): detect the trade from MT5, mark it on
the TradingView chart, screenshot it, bind to `position_id`.

**The thing that would have ruined it** was the entry frame containing the
answer. It turned out to have four openings, not one, and the last two only
became visible while building it:

1. The **visible range** — ends at the entry bar, as planned above.
2. **Exit markup** — not drawn at all on an entry capture.
3. The **timeframe**. Choosing it from the trade's duration is right for a
   review, but on an entry frame it tells the reader how long the trade lasted
   before they have looked at a candle. Duration is an outcome.
4. **Levels inferred from the exit fill.** Stage 2's own observation — a
   stop-loss exit happens *at* the stop — recovers a level MT5 never stored,
   and is inadmissible here for exactly the reason it works: it only yields a
   level for trades that were stopped out, so the presence of a stop rectangle
   would itself announce the outcome.

`planCapture()` is pure and returns the frame as a value, so all four are
properties a test can assert rather than things that have to be true of a
screenshot. Each guard was verified by reintroducing the bug.

The three caveats all held:

- No long/short position widget in `draw_shape` — risk and reward are
  rectangles, and the markup is removed by entity id afterwards so the user's
  own drawings survive
- Symbol mapping is a maintained table (`symbol-map.json` /
  `TV_SYMBOL_MAP`) that **refuses to guess**: an unmapped symbol is an error
  naming what to add, because a wrong mapping is a plausible chart of a
  different instrument filed against a real position
- Every capture is labelled with whose prices it is showing

**Storage is a cache, not an archive**, and retention already exists in the
journal's `POST /prune` — files deleted, rows kept. What is *not* built is the
**Regenerate** button in the dashboard drawer; re-running the tool is the
regeneration, so this is a UI affordance rather than a mechanism.

### 5. Signals — the setups you skipped — *mechanism done, discipline not*

A skipped setup leaves **zero trace** in MT5. If the problem is hesitation
rather than over-trading, every analysis so far is structurally blind to it.

Answers: hit rate on setups taken vs skipped, and whether the skipped ones would
have won.

The plumbing exists — the journal's `POST /signals` and `POST /decision`, and
`/reconcile`, which sorts signals against fills into followed, missed and
discretionary. What does not exist is a run of real data through it. Until
something records decisions at the time, `skip_reasons_claimed` stays empty and
the question stays unanswered.

---

## ~~Then: strategy and backtest~~ — built

A strategy definition that is data rather than branches, detectors carrying
`knowable_at`, a bar-replay engine emitting the `pair_trades()` shape so
backtest and live numbers go through one set of analytics, a parameter sweep
judged out of sample, and strategy 1 on top of it with its own walk-forward
split by time rather than index.

The thing it was built to answer — **the same entries with a wider stop** — is
a `/sweep` axis now, and has still not been run against the real account.

## ~~Later: execution~~ — built, deliberately hard to reach

`execution_service.py`, started only with `--exec`. Disarmed, dry run, demo
only, expiring arm window, symbol allowlist, stop required, and no MCP tools at
all: placing an order should be an explicit human act, not something reachable
from a conversation.

The gate that mattered — *only after a strategy shows positive expectancy in R,
out of sample* — is about arming it, not about the code existing.

---

## What is actually next

Everything below needs the real account rather than more code. The system can
describe, simulate and picture a trade; almost nothing in it has been checked
against the account it was built for.

1. **Run `/diagnose/stops` against the real account.** It decides whether
   R-multiples for real trades are a join or a journalling problem — see
   stage 2. Nothing else unlocks as much.
2. **Verify the insight rules on real data.** They have only ever run on
   synthetic input and render as confident cards.
3. **Record decisions for a month**, so stage 5 has something to join against.
4. **Run the sweep's wider-stop axis** on real bars.

## Smaller things

- **ESLint for `dashboard-app/`** — `npm run lint` covers `src/` only. Needs a
  second flat config and a pass over ~2,500 lines of JSX
- **Excursion panels in the dashboard** — the data is exposed but not yet drawn
- **Journal coverage indicator** — every derived panel needs to show what
  fraction of trades were journalled. A journal at 20% coverage produces
  selection-biased analytics that look authoritative, because people journal
  memorable trades
- **Capture drawer in the dashboard** — trade captures exist and are filed
  against `position_id`, but nothing displays them. The **Regenerate** button
  belongs here; re-running `capture_trade` is already the regeneration
- **Symbol table coverage** — the shipped broker→TradingView table covers FX,
  metals, oil, the major indices and BTC/ETH. Everything else is an error with
  a hint, which is the correct failure but still a gap for anyone trading
  outside it
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
