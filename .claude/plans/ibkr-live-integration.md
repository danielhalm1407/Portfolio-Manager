# Connect the accounting engine to Interactive Brokers

> **This is the canonical plan file.** Repo `.claude/plans/`, alongside `pnl-accounting-extraction.md` and `rebalance-realisation.md`. Living document — keep the Progress Log at the bottom current.

## Context

Phases 1–2 are done: the P&L engine lives in `portutils.portfolio`, and the rebalancing study proved daily constant-mix rebalancing produces a continuous realised-P&L stream (29,098 over 250 bars on SPY/KMLM; 86% of the hedge's realised booked on the 67 bars where SPY fell and KMLM rose), while the discretionary drawdown overlay traded 13.1× capital to end up *worse*. Log: [rebalance-realisation.md](rebalance-realisation.md).

All of that ran on **simulated fills over cached prices**. This phase connects it to the real paper account, in two directions:

1. **Read** — break down realised/unrealised P&L for every holding actually in the paper account, from IBKR data rather than synthetic fills.
2. **Write** — run the constant-mix policy live on SPY + KMLM, sending real orders.

Plus a markdown explainer per script.

### What IBKR can and cannot give us

Verified in [ibkr_requests.py](../../src/portutils/ingestion/ibkr_requests.py):

- `updatePortfolio` ([:797](../../src/portutils/ingestion/ibkr_requests.py#L797)) returns `position`, `averageCost`, `unrealizedPnL`, `realizedPnL`, `marketPrice`, `marketValue` per holding — mapping **exactly** onto `Position.restore(qty, avg_entry, realised)`.
- `execDetails` ([:892](../../src/portutils/ingestion/ibkr_requests.py#L892)) collects fills into `app.executions` — but **nothing ever calls `reqExecutions`**. No `get_executions_data`, no `ExecutionFilter`, no `commissionReport` handler. The receiver is wired; the request is missing.
- **The hard limit:** `reqExecutions` serves roughly the **last 7 days**. A year of trade history needs Flex Queries and a token from Account Management.

**Decisions taken with the user:** source history **both** ways (reconstruct from the current book, and overlay ~7 days of real fills); live rebalancer defaults to dry-run with an explicit `--live` flag whose default is configurable; it lives at `src/pipelines/rebalance_live.py`.

---

## 1. Fill the gap in the IBKR client

In [ibkr_requests.py](../../src/portutils/ingestion/ibkr_requests.py), following the existing `get_positions_data` / `get_account_updates` shape (clear the store → subscribe → `_wait_for` an event → return a DataFrame):

- **`commissionReport` callback** — IB delivers commissions on a *separate* callback from `execDetails`, keyed by `execId`. Without it every fill looks free. Store into `app.commissions[execId]`.
- **`execDetailsEnd`** — the completion signal `_wait_for` needs; currently absent.
- **`get_executions_data(app, days_back=7, symbols=None, timeout=15)`** — build an `ExecutionFilter`, call `reqExecutions`, wait, join commissions on `execId`, return a DataFrame. **Docstring must state the ~7-day ceiling** so an empty result is never mistaken for "no trades".
- Enrich the stored execution dict with `execId`, `secType`, `currency`, `cumQty`, `avgPrice` — currently dropped, and `execId` is required to join commissions at all.

## 2. Broker → Book bridge — `src/portutils/portfolio/ibkr_sync.py`

Pure functions (no network, no side effects — library rules):

- **`book_from_portfolio(portfolio_df, base_equity)`** → a `Book` seeded via `Position.restore`.
- **`fills_from_executions(exec_df)`** → `list[Fill]`, mapping IB's `"BOT"`/`"SLD"` to ±1. Lets real IBKR fills run through the *same* `apply_fill` as simulated ones — the point of §7.3's backend-agnostic design.
- **`reconcile(book, portfolio_df)`** → per-symbol comparison of our position/avg-entry/realised against IBKR's, with a `match` flag. The "✓ matches IBKR / ⚠ drift" check `orders/plan.md` §7.3 specified and never got.

**Two accounting caveats to document, not paper over:**
- IB's `averageCost` is per-share **including commissions** for stocks (per-contract including multiplier for futures); our `avg_entry` is a plain per-unit price. Fine for STK, wrong for futures without the multiplier.
- IB's `realizedPnL` in `updatePortfolio` is generally **session-scoped**; our `Position.realised` is cumulative since inception. Label the seeded figure as IBKR's.

## 2b. How far back we can actually see

Stated in the plan, the module docstring, and the script's printed output. **Beyond ~7 days we do not reconstruct actual history.** Four distinct things, never blended:

| method | what it yields | what it assumes | status |
|---|---|---|---|
| **Roll fills backwards** | q(T−7d) exactly | nothing — real data | **history** |
| **Hold-constant backcast** | a weight path with q frozen | position held unchanged | **counterfactual**, labelled |
| **Policy-anchored backcast** (§2c) | full position + realised/unrealised path | a *named* policy, anchored on real q(T−7d) | **reconstruction under a falsifiable assumption** |
| **`avgCost` inversion** | a *band* of plausible accumulation dates | price history only | **diagnostic** |

Realised P&L before the window is **not recoverable from positions alone**: an average cost describes only the *surviving* units. A positions-only view says "unknown before <date>" rather than a zero that reads as "nothing was realised".

## 2c. Policy-anchored backcast — `src/portutils/portfolio/backcast.py`

A policy assumption is not a guess when it is **anchored on real data at one end** and **checkable against real data at the other**.

**Step 1 — anchor.** Roll 7 days of real fills backwards off today's positions → q(T−7d) exactly. This end is real.

**Step 2 — choose a start.** Default **3 months** before the anchor; overridable globally (`--backcast-months`) and **per symbol** (`held_since: {SPY: 2025-08-01}`).

**Step 3 — walk positions backwards under a named policy.**
- `buy_and_hold` — units **constant** going back. Not an approximation: if no trades happened this is exactly what the book was. Step 5 tests the no-trade premise.
- `constant_mix` — weights at target every bar, so equity steps back by the exact recursion `E(t−1) = E(t) / (1 + Σ wᵢ·rᵢ(t))`, units follow as `uᵢ(t) = wᵢ·E(t) / pᵢ(t)`. Deterministic, no iteration.

**Step 4 — replay forward through a real `Book`.** Turn implied position changes into `Fill`s and run them start→anchor through the same `apply_fill`. This is what yields a **realised/unrealised split for the pre-window period**, from the one accounting engine rather than a parallel calculation.

**Step 5 — falsify it.** The replay ends at a predicted position and average entry cost; compare both against what IBKR reports today. Match → the policy is consistent with the account. Mismatch → **the assumption is wrong and the script says so**, reporting the gap rather than presenting the path as fact.

Known limits: a symbol without price history cannot be backcast; and `avgCost` agreement is a *consistency* test, not a proof — offsetting trades could in principle land on the same average.

Output is always **labelled by provenance** (`real` / `backcast (buy_and_hold)` / `backcast (constant_mix)`), and charts shade the backcast segment.

**The real fix, and the thing that stops the problem growing:**
- **Flex Query / downloaded activity statement** — the only genuine full history. Hook: `--statement <path>` replaces the backcast with real fills when supplied.
- **`src/pipelines/log_positions.py`** — snapshot positions + NetLiq to `data/raw/ibkr/positions_YYYY-MM-DD.parquet`. Every day it is not running loses a day of history for good.

## 3. `research/check_existing_port.py` — the read side

Cell script in the [rebalance_realisation.py](../../research/rebalance_realisation.py) idiom (`# %%` cells, reload block, `PanelBuilder` → `make_level_figure`, explicit colour/label maps):

1. Connect (`client_id` distinct from other scripts), `get_account_updates` → holdings, `get_account_data` → NetLiq.
2. `book_from_portfolio` → a `Book` mirroring the account; print IBKR's figures beside ours, `reconcile` proves agreement.
3. `get_executions_data(days_back=7)` → real fills, replayed through a second `Book`. **Print the window explicitly** so the 7-day limit is visible in the output.
4. Price history via `get_equity_data`, cached (`cache_prices.py` pattern).
5. §2c backcast: `--backcast-policy {buy_and_hold,constant_mix,none}`, `--backcast-months 3`, optional per-symbol `held_since`. Print the falsification check **before** any resulting numbers.
6. Figures reusing `rebalance_study.py` functions: realised-vs-unrealised per holding, realised by ticker, weight vs target, role roll-ups via `cfg.asset_role`. Backcast segment visually distinguished; figure titles name the assumed policy. Unknown tickers show `unclassified` rather than breaking the run.

## 4. `src/pipelines/rebalance_live.py` — the write side

Live counterpart of the `mix` book, **reusing `ConstantMixRule` itself** so the policy trading real money is literally the backtested code.

Flow: connect → `get_account_updates` → `book_from_portfolio` → prices from `marketPrice` → `ConstantMixRule(weights).propose(...)` → signed deltas → round to whole shares → min-turnover gate → print order table → submit via `submit_rebalance_orders` (reused from the basic script).

### How live-vs-dry is decided

`--live` is a CLI argument. Default is **configurable** in `config/settings.yaml`:

```yaml
live_trading:
  enabled: false          # true makes --live the default; no flag needed thereafter
  account: "DUP102412"
  require_paper: true     # refuse accounts not starting with "DU"
  max_order_value: 50000  # per-order notional ceiling
  min_turnover: 0.02
```

Resolution order: **1.** `--dry-run` → always dry, whatever config says (deliberately not configurable away). **2.** `--live` → live. **3.** `live_trading.enabled` → the standing default.

Setting `enabled: true` means every run trades for real with no flag. That is the requested behaviour and it does remove a guard — hence the remaining ones stay. (Unrelated to Claude Code's own permission prompts, which this file cannot affect.)

Guards surviving `enabled: true`: full order table printed before transmission on every run; `require_paper` asserts a `DU` account (a live account additionally needs `--i-know-this-is-real`); `max_order_value` rejects oversized orders from a bad tick; refuses to run if resolved weights differ from `config.portfolio_weights("spy_kmlm")`; every submitted order written to `outputs/live/orders_<timestamp>.csv`.

Scope is SPY + KMLM, read from the YAML.

## 5. Rename + documentation

- `orders/rebalance_port.py` → **`orders/rebalance_port_basic.py`** (git mv, content unchanged). Grep importers first — `drawdown_rotation_sim.py` was bitten by exactly this last phase.
- One markdown explainer beside each script — what it does, what it reads/writes, how to run it, accounting caveats, and what it deliberately does not do. Written **before** the live dry-run step, so the order-sending script is documented before it is exercised.
  - `research/rebalance_realisation.md`
  - `research/check_existing_port.md`
  - `src/pipelines/rebalance_live.md` — includes the live/dry resolution order in full
  - `orders/rebalance_port_basic.md`

---

## Verification

> **TWS is currently down** (workstation will not start on this machine). The offline block proceeds now; the live block is **BLOCKED** until TWS runs again. Nothing offline is weakened to compensate — a broker-dependent step that cannot run is reported as not run.

**Offline:**
1. Existing 22 tests stay green.
2. `book_from_portfolio` / `fills_from_executions` / `reconcile` on a **synthetic** `updatePortfolio`-shaped frame: seeded book reproduces positions exactly; `reconcile` flags a deliberately corrupted row.
3. `rebalance_live.py --dry-run` against a **stubbed** portfolio frame: correct signed deltas, orders restoring 60/40, `max_order_value` rejects an oversized line, and `--dry-run` still wins when `enabled: true`.
4. 7-day-boundary logic on synthetic fills: rolling a known fill sequence backwards recovers the known starting position exactly; provenance labelling correct.
5. **Backcast round-trip** — run a known `ConstantMixRule` book forward on synthetic prices, keep only final positions + last 7 days of fills, backcast, confirm it recovers the original path and realised P&L. Then backcast with a **deliberately wrong** policy and confirm step 5 **fails loudly**. A falsification test that never fails is worthless.

**Blocked on TWS** (paper account `DUP102412`):
6. `check_existing_port.py` — realised/unrealised must equal IBKR's per holding (`reconcile` all-`match`); mismatches diagnosed, not rounded away. Backcast verdict reported as-is, including a negative one.
7. `rebalance_live.py` dry-run against the real account — order table confirmed to move weights toward 60/40.
8. `rebalance_live.py --live` — submit, re-run `check_existing_port.py`, confirm new positions and fills appear and the book still reconciles **after** real trades. That round trip is the real proof.

## Order of work

1. `commissionReport` + `execDetailsEnd` + `get_executions_data`.
2. `ibkr_sync.py` + fill-rollback + offline checks (2, 4).
3. `backcast.py` + falsification check + offline check (5).
4. `orders/rebalance_port_basic.py` rename (grep importers first).
5. `research/check_existing_port.py`.
6. `src/pipelines/log_positions.py` — early on purpose: it can start accumulating history the moment TWS returns.
7. Four markdown explainers.
8. `src/pipelines/rebalance_live.py` + `live_trading` config; dry-run verified offline.
9. Live round trip once TWS is available; update the Progress Log.

---

## Progress Log

Status key: `TODO` / `WIP` / `DONE` (with evidence) / `BLOCKED`.

| # | Step | Status | Notes / evidence |
|---|------|--------|------------------|
| 1 | `get_executions_data` + commission/end callbacks | DONE | callbacks driven with fake `execDetails` / `commissionReport` / `execDetailsEnd` objects: fill captured, commission stored by `execId`, end event set. `reqExecutions` itself **untested** — needs TWS |
| 2 | `ibkr_sync.py` | DONE | 11 tests in `tests/test_ibkr_sync.py` |
| 3 | `backcast.py` | DONE | round-trip recovers a known constant-mix book bar-for-bar; wrong-policy check fires; `separation()` added — see Finding 1 |
| 4 | `rebalance_port_basic.py` rename | DONE | `git mv`, tracked as a rename; grep found zero importers repo-wide |
| 5 | `check_existing_port.py` | DONE (unrun) | compiles; every imported name resolves. Cannot execute without TWS |
| 6 | `log_positions.py` | DONE | append/replace-by-date tested offline: a same-day re-run replaces rather than duplicating |
| 7 | Markdown explainers | DONE | 5 written (4 planned + `log_positions.md`) |
| 8 | `rebalance_live.py` + config | DONE | `live_trading` block in `config/settings.yaml`; 12 tests in `tests/test_rebalance_live.py` |
| 8b | End-to-end stubbed dry run of `main()` | DONE | all 3 modes correct; **caught Finding 4** |
| 9 | Live round trip | BLOCKED | TWS will not start on this machine |

**52 tests green** (22 pre-existing + 30 new).

Stubbed dry-run results (no TWS, broker replaced wholesale):

| scenario | expected | actual |
|---|---|---|
| no flags, config disabled | DRY RUN | DRY RUN ✓ |
| `--dry-run` + `enabled: true` | DRY RUN (override) | DRY RUN, reason "overrides config" ✓ |
| `enabled: true`, no flags | LIVE | `*** LIVE ***`, genuinely called `submit_market_order` ✓ |

Order table sized correctly off 1,000,000 (SPY 1000→858, KMLM 10,000→13,333), confirming Finding 2's fix end to end. Audit CSV written on dry runs too.

### Finding 1: the falsification check has a blind spot, and now reports it

The wrong-policy test initially **passed when it should have failed**. Investigated rather
than retuned: the check compares cost bases, and two policies build different cost bases only
to the extent prices actually moved. Measured on synthetic paths — **~1.2% separation at 0.8%
daily vol versus ~5–8% at 2%**. The 1.2% sits inside the 2% tolerance IB's
commission-inclusive `averageCost` forces us to allow, so on a quiet window the wrong policy
is indistinguishable from the right one.

Tightening the tolerance would have made the test green and the tool wrong (false alarms on
real data). Instead `separation()` was added — it runs the backcast under each candidate
policy and measures how far apart their predicted cost bases are — and `run()` now returns a
third verdict:

- `consistent` — fits, **and** the data could have rejected it
- `INCONSISTENT` — does not fit; do not believe the reconstruction
- `indeterminate` — fits, but nothing here could have rejected it, so it is not evidence

Both pinned: `test_backcast_wrong_policy_is_caught` (volatile window, must fail) and
`test_backcast_cannot_discriminate_on_a_quiet_window` (quiet window, must report
`indeterminate`).

### Finding 2: NetLiquidation was double-counted (would have over-traded live)

`Book.equity()` is `base_equity + realised + unrealised`, correct when `base_equity` means
STARTING capital (the simulator's case). A broker's NetLiquidation is the opposite: CURRENT
equity, which **already contains** the unrealised P&L of every open position. Passing it
through raw counted that P&L twice.

A 1,000,000 account holding 150,000 of unrealised gains reported `equity() == 1,150,000`, and
`ConstantMixRule` sized every target off that — **a systematic 15% over-trade on every live
rebalance**. Surfaced by a unit test whose expected BUY came back blank because the inflated
order tripped `max_order_value`.

Fixed in `book_from_portfolio`, which back-solves the internal base as
`net_liq − unrealised − realised` so `book.equity(marks) == net_liq` exactly. Pinned by
`test_equity_equals_net_liquidation_not_double_counted`.

### Finding 3: `config.py` read YAML without an encoding

`open(path)` uses the platform encoding — cp1252 here — so any non-Latin-1 character in a
config file raised `UnicodeDecodeError` at import time, taking down every module that imports
`config`. Triggered by a box-drawing character in the new `settings.yaml`. Both loads now
pass `encoding="utf-8"`.

### Finding 4: the live pipeline imported a cell script that trades on import ⚠

**The most serious bug of the phase, and only the end-to-end dry run could have found it.**

`rebalance_live.py` did `from rebalance_port_basic import submit_rebalance_orders`. But
`orders/rebalance_port_basic.py` is a cell script: its module-level code calls
`ib_app.start(client_id=125)`, and its bottom cells call
`submit_rebalance_orders(..., dry_run=False)` and
`order_app.submit_market_order('5mvl', 'BUY', 10, ...)`.

So with TWS running, `python src/pipelines/rebalance_live.py` — **the dry-run default** —
would have opened a second connection and transmitted real orders as an import side effect.
Every guard in the pipeline sits downstream of that import and would never have been
consulted. Unit-testing `build_orders` and `resolve_mode` in isolation could not have caught
it; only running `main()` did.

Fixed by moving `submit_rebalance_orders` into `portutils.ingestion.ibkr_requests` (beside
`OrderApp`, comments preserved verbatim). `rebalance_port_basic.py` is untouched and keeps
its own now-frozen copy. Pinned by `test_does_not_import_the_cell_script`, which checks the
source text rather than importing — importing to test would trigger the very behaviour being
guarded against.

### Decisions log
- 2026-07-25 — History sourced **both** ways: reconstruct from the current book *and* overlay the ~7 days of real fills `reqExecutions` allows.
- 2026-07-25 — Policy-anchored backcast (§2c) added: anchored on real q(T−7d), extended back a configurable horizon (default 3 months) under a named policy, and **falsified** against IBKR's actual position/avgCost. Provenance labelled everywhere; a failed check is reported, not hidden.
- 2026-07-25 — Live trading default is configurable via `live_trading.enabled`, granting standing authorization without a per-run flag. `--dry-run` remains an override that config cannot disable.
- 2026-07-25 — New live rebalancer at `src/pipelines/rebalance_live.py` (repo rule: side-effecting scripts live in `pipelines/`); the old cell script becomes `orders/rebalance_port_basic.py`.
- 2026-07-25 — TWS unavailable on this machine, so all broker-dependent verification is blocked; offline work proceeds and is not weakened to compensate.
