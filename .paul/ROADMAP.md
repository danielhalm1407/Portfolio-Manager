---
description: "Portfolio-Manager — milestone and phase structure"
type: Roadmap
about: "Portfolio-Manager"
---

# Roadmap: Portfolio-Manager

## Overview

The project moves in three arcs. First an accounting and attribution engine capable of saying what a
book actually earned and why (v0.1). Then the execution half — reading an IBKR account, valuing it in
base currency, turning target weights into acknowledged orders, and being able to say precisely what
happened to each one (v0.2). Finally the research half that feeds the allocator: theme extraction
from market commentary into scored tilts and conviction briefs (v0.3). Phase 10 was appended to
v0.2 on 2026-08-06: the execution half, once trustworthy, gets a web front end.

**v0.4 is the meat.** Everything before it builds machinery; v0.4 uses that machinery to answer the
question the project exists for: *does portfolio insurance actually pay, and how much of it does a
retail investor really need?* Phase 2 showed rebalancing crystallises realised P&L over one year and
one regime. v0.4 has to show something far stronger — that a hedge sleeve improved risk-adjusted
returns across MANY historical points and regimes; that parameters fitted on a backward window kept
working on the forward window that followed; and that the conclusion survives plausible scenarios
history has not yet produced.

## Milestones

| Version | Name | Phases | Status | Completed |
|---------|------|--------|--------|-----------|
| v0.1 | Accounting & attribution engine | 1-3 | ✅ Shipped | 2026-07-26 |
| v0.2 | Live execution against IBKR | 4-8, 10 | 🚧 In Progress | - |
| v0.3 | Research half | 9 | 📋 Planned | - |
| v0.4 | Does hedging actually work? | 11-15 | 📋 Planned | - |
| v0.5 | Strategy architecture consolidation | 16 | 📋 Planned | - |

> Phase 2 was reopened on 2026-08-23 for plans 02-02/02-03 (trade rationale). v0.1 stays shipped —
> the reopening adds reasoning on top of shipped accounting, it does not reverse it.

## Current Milestone

**v0.2 Live execution against IBKR** (v0.2.0)
Status: In progress
Phases: 3 of 6 complete (5, 6, 8) — 4 and 7 are reopened/in progress, 10 not started

Progress: [█████░░░░░] 50%

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with [INSERTED])

| Phase | Name | Plans | Status | Completed |
|-------|------|-------|--------|-----------|
| 1 | P&L accounting engine | 1 | Complete | 2026-08-01 |
| 2 | Rebalancing as realised P&L | 3 | In progress (reopened 2026-08-23) | - |
| 3 | Return attribution consolidation | 1 | Complete | 2026-07-26 |
| 4 | IBKR read path | 5 | In progress (reopened 2026-08-03) | - |
| 5 | Live multi-currency rebalancer | 1 | Complete | 2026-07-26 |
| 6 | Order verification & recovery | 2 | Complete | 2026-07-26 |
| 7 | Staged live verification runbook | 2 | In progress | - |
| 8 | Documentation hub | 1 | Complete | 2026-08-01 |
| 9 | Research half — themes to tilts | TBD | Not started | - |
| 10 | Deployment — web app | 2 | Not started | - |
| 11 | Long-history data foundation | 2 | Planning | - |
| 12 | Option overlay engine | TBD | Not started | - |
| 13 | Walk-forward validation harness | TBD | Not started | - |
| 14 | Regime labelling and scenario guardrails | TBD | Not started | - |
| 15 | Conviction write-up — how much hedging is enough | TBD | Not started | - |
| 16 | Strategy architecture consolidation | TBD | Scoping | - |

## Phase Details

### Phase 1: P&L accounting engine

**Goal:** A reusable, multi-symbol, GUI-free accounting engine in `src/portutils/portfolio/`, with
`orders/kts.py` migrated to delegate to it.
**Depends on:** Nothing (first phase)
**Research:** Unlikely (internal refactor)

**Scope:**
- `fills.py`, `book.py`, `execution.py`, `ledger.py`, `rules.py`, `simulator.py`
- kts.py `sim_*` become property shims over `Book`
- Golden-fixture parity against `orders/replay_state.json`

**Plans:**
- [x] 01-01: Extract P&L accounting out of kts.py into `portutils.portfolio`

### Phase 2: Rebalancing as realised P&L

**Goal:** Treat rebalancing as the natural source of realised P&L, with a configurable asset
universe and a reproducible study.
**Depends on:** Phase 1 (the `Book` and `Fill` primitives)
**Research:** Unlikely

**Scope:**
- `ConstantMixRule`; `config/asset_universe.yaml` with roles and named portfolios
- Turnover extras, `performance.py` §7a fix
- `rebalance_study.py` + figures; `research/rebalance_realisation.py`

**Plans:**
- [x] 02-01: Rebalancing as the natural source of realised P&L
- [ ] 02-02: Trade rationale originates at the rule, rides `ctx` into an `Order`, exports as JSON
- [ ] 02-03: Hoverable trade plot, `run_books` call chain documented, `rebalance_study.md` written

**Reopened 2026-08-23.** 02-01 shipped the accounting; the offline path records no *reason* for any
trade. `PortfolioSimulator` calls `execute` with no `ctx` and `on_order=None`, so the `Order` ledger
and its `dummy_orders.json` serialization — both complete in `fills.py` — are reached only by
`orders/kts.py`. 02-02 connects them; 02-03 renders and documents the result.

### Phase 3: Return attribution consolidation

**Goal:** Move return attribution and return/weight definitions out of `viz/` into `analysis/`.
**Depends on:** Phase 2 (turnover and weight plumbing)
**Research:** Unlikely

**Scope:**
- Wide-matrix helpers to `returns.py`; `simulate_weights` and `ReturnAttribution` to `performance.py`
- `panel.attribution` reduced to a shim preserving the 5-tuple
- Comments merged and retained

**Plans:**
- [x] 03-01: Move return attribution out of `viz/` into `analysis/`

### Phase 4: IBKR read path

**Goal:** Connect the accounting engine to Interactive Brokers for everything that only reads.
**Depends on:** Phase 1 (accounting engine to sync into)
**Research:** Likely (IB contract resolution semantics)

**Scope:**
- `get_executions_data`, `ibkr_sync.py`, `backcast.py`, `log_positions.py`
- Non-US contract resolution: `contract_specs_from_portfolio`, partial success, `what_to_show`
- Backcast render gating with per-symbol verdicts

**Plans:**
- [x] 04-01: Connect the accounting engine to Interactive Brokers
- [x] 04-02: Fix `get_equity_data` hangs + rejects non-US holdings
- [x] 04-03: Render backcast cells regardless of verdict, marked per symbol
- [x] 04-04: UTC dash `ExecutionFilter.time`; the midnight-today executions ceiling documented; terminal cell runner
- [ ] 04-05: Executions client-id scoping, verified `ts` timezone semantics, transparent terminal debug path — scoped in `CONTEXT.md`, not yet planned

### Phase 5: Live multi-currency rebalancer

**Goal:** Target weights become real orders, valued in base currency, dry-run first.
**Depends on:** Phase 4 (contract specs and account snapshot)
**Research:** Unlikely

**Scope:**
- `con_id` threaded through order submission; `contract_specs` through `submit_rebalance_orders`
- Base-currency valuation via `marketValue`; universe = weights ∪ held
- Full diagnostic table

**Plans:**
- [x] 05-01: Multi-currency live rebalancer — dry-run first, then live

### Phase 6: Order verification & recovery

**Goal:** Be able to say precisely what happened to every submitted order, and recover from a bad
submission without reaching for the tool that caused it.
**Depends on:** Phase 5 (orders to verify)
**Research:** Unlikely

**Scope:**
- `wait_for_order_ack`, `cancel_order` / `cancel_all_orders`, `cancel_orders.py` pipeline
- Per-order verdicts, full message capture, values in the order table
- SMART routing via `primaryExchange`; untradeable-universe handling
- `orders/rebalance_live_debug.py` behind an `ARM_LIVE` gate

**Plans:**
- [x] 06-01: Orders vanish on submit — wait for acknowledgement, and be able to cancel
- [x] 06-02: SMART routing, held orders, and an untradeable universe

### Phase 7: Staged live verification runbook

**Goal:** Walk the whole stack against live TWS in a fixed order, each stage gated on the previous.
**Depends on:** Phase 6 (verdicts must be trustworthy before a live run)
**Research:** Unlikely

**Scope:**
- Attribution figures reviewed; `rebalance_realisation` §3–4 vs §7
- `log_positions.py` and `check_existing_port.py` run against TWS
- `rebalance_live --dry-run`, then `--live` on explicit approval of the dry-run table
- Post-trade `log_positions.py` re-run

**Plans:**
- [x] 07-01: Cell-label banners in `check_existing_port.py`
- [ ] 07-02: Staged verification runbook — attribution → IBKR read → order submission

### Phase 8: Documentation hub

**Goal:** A root README that is a hub, and one doc per area beneath it.
**Depends on:** Phases 1-6 (documents what they built)
**Research:** Unlikely

**Scope:**
- README hub with corrected tree and a "where do I look for X" table
- `EXECUTION_STACK.md`; 10 folder-level READMEs
- Merge plans folded in, then archived and gitignored

**Plans:**
- [x] 08-01: A README that is a hub, and one doc per area beneath it

### Phase 9: Research half — themes to tilts

**Goal:** Turn market commentary into scored theme tilts and conviction briefs that feed the
allocator.
**Depends on:** Phase 2 (the allocator consumes weights)
**Research:** Likely (scoring methodology, source selection)
**Research topics:** theme scoring rubric, confidence and horizon calibration, commodity sleeve depth

**Scope:**
- Theme briefs in `research/themes/` — source, narrative, scored tilt, confidence, horizon
- Conviction write-ups in `research/conviction/`
- Wire scored tilts into the allocator

**Plans:**
- [ ] 09-01: To be defined during `/paul:plan`

### Phase 10: Deployment — web app

**Goal:** A browser front end over the execution stack: a dashboard of positions, P&L and attribution,
plus a gated control for the live rebalancer.
**Research:** Likely for Track B (secret handling, how the approval gate re-expresses in a browser).
Unlikely for Track A — the hosting question is answered below.

**Split into two tracks, 2026-08-31.** The two halves have opposite hosting constraints and
opposite dependencies, and bundling them gated a zero-risk showcase behind a live-trading
runbook it has nothing to do with.

*Track A — static showcase. No secrets, no TWS, no server. Depends on nothing.*
- [ ] 10-01: Pre-rendered Plotly figures published as a static site

**Scope:**
- A pipeline under `src/pipelines/` that renders the existing figure builders to HTML and
  writes the result to a published directory
- `include_plotlyjs="directory"` so `plotly.min.js` is emitted ONCE beside the pages and every
  figure references that single copy, rather than inlining ~3MB per figure
- Figures sourced from the EXISTING builders — `PanelBuilder` (`viz/panel.py`) and
  `_build_level_figure` (`viz/dash_timeseries_app.py:637`). Neither needs rewriting: the
  figure layer is already separate from the Dash layer, which only wraps it
  (`LevelDashApp`, `dash_timeseries_app.py:1041`)
- Server-free interactivity kept deliberately: hover, zoom, pan, legend toggle, range sliders,
  `updatemenus` dropdowns and animation frames are all client-side. `PanelBuilder`'s
  `animation_indices` / `add_in_frames` / `play_button` survive unchanged, and
  `_build_level_figure_with_plotly_mode_buttons` (`dash_timeseries_app.py:898`) is already the
  `updatemenus` pattern that replaces most simple callbacks

*Track B — gated live control. Server, secrets, TWS reachability.*
**Depends on:** Phase 7 (the runbook must prove the stack before a browser is allowed to drive it)
- [ ] 10-02: Live-rebalancer control behind an explicit approval gate

**Scope:**
- Dashboard over LIVE positions, P&L split and attribution — as opposed to Track A's rendered snapshot
- Live-rebalancer control behind an explicit approval gate — the dry-run table re-derived for a browser
- Hosting deliberately undecided, but static hosting IS ruled out here: no secrets, no TWS
  reachability. `gunicorn` is already a declared dependency

**Hosting, settled for Track A (2026-08-31).** Static hosting was previously written off for
the whole phase. That is correct for Track B and wrong for Track A. Dash itself has no static
export and will not get one — it is Flask, and its callbacks are HTTP POSTs to
`/_dash-update-component`, so no server means no callbacks. But a Dash app is not the only way
to publish a Plotly figure: `fig.write_html` produces a fully interactive page with no runtime
at all. What Track A cannot do is anything requiring Python at request time — live IBKR reads,
secrets, the rebalancer control. That is exactly the Track B list.

**Naming footgun:** HoloViz **Panel** does have a Pyodide static export; **Dash** does not.
This repo's `viz/panel.py` is neither — it is a local `PanelBuilder` class. Do not conflate the
three when researching 10-01.

**Numbering note:** appended as 10 rather than 9 because 9 was already claimed by the v0.3 research
phase. v0.2 is therefore a non-contiguous range (4-8, 10) by choice — see the 2026-08-06 decision.
The Track A/B split adds a plan, not a phase, so the v0.2 denominator is unchanged.

## Milestone v0.4: Does hedging actually work?

**The claim to be defended:** a small set of hedge and diversification structures sustainably improve
risk-adjusted returns — demonstrably in the past, across multiple regimes; demonstrably out-of-sample,
in that parameters fitted on a backward window kept working on the forward window that followed; and
plausibly forward, under scenarios that have not yet occurred but are economically coherent.

The mechanism under test is the one Phase 2 established: a hedge leg that rallies into an equity
drawdown is SOLD into that rally by the rebalance, generating cash that is redeployed into the fallen
growth leg near the trough. v0.4 asks whether that mechanic survives contact with history.

**Two findings from the 2026-08-24 survey shape the phase order:**

1. **The data cannot support the claim yet.** Every cached panel is 250 rows spanning
   2025-07-28 to 2026-07-24 — one year, one regime. The thesis needs 2000, 2008, 2020 and 2022 at
   minimum. This blocks everything, hence Phase 11 first.
2. **The codebase has no options.** `strike`, `secType="OPT"`, `right` and `expiry` appear nowhere in
   `src/portutils/`. The rolling collar of the Obsidian hedge catalogue is a new subsystem, not an
   extension of the weight-based rules.

Reusable as-is: `QuantileRiskControlGrid` already runs Cartesian parameter sweeps, and
`PerformanceSummary` / `PerformanceCompare` already compute risk-adjusted metrics. The walk-forward
harness wraps these rather than replacing them.

### Phase 11: Long-history data foundation

**Goal:** Decades of index, hedge-proxy and volatility history, cached reproducibly with documented
provenance and known gaps.
**Depends on:** Nothing new (extends the Phase 4 read path)
**Research:** Likely (how far back IBKR actually serves, and what free sources fill the gap)

**Scope:**
- Multi-request pagination past IBKR's per-request duration ceiling
- A `yfinance` fallback path (already a declared dependency, currently unused) for history IBKR
  will not serve, with provenance recorded per series
- A long-history cache under `data/processed/`, plus a coverage report naming every gap
- Whether an option-implied vol surface is obtainable at all, or whether Phase 12 must synthesise
  from realised/VIX vol — answered with evidence, not assumed
- **Ragged-history loading in `PanelBuilder` AND in `cache_prices.py`** — see the blocker below
- **An IBKR message reference** — the codes to expect, grouped, keyed by code

**Starting position, measured 2026-08-31** (full inventory in
`.paul/phases/11-long-history-data/CONTEXT.md`): three parquets and 27 CSVs, none reaching
beyond one year. There is **no SPX series at all** — S&P exposure exists only as SPY, whose
earliest bar anywhere in `data/` is 2025-04-14 — and **no NDX or QQQ series of any kind**.
`data/reference/` is empty. The index leg the milestone rests on is therefore ~1 year deep,
and the second index leg does not exist yet.

**Plans:**
- [ ] 11-01: Reach back decades — paginated IBKR history, free-source fallback, coverage report,
  ragged-history loading and the rebase anchor policy (Task 4)
- [ ] 11-02: IBKR message reference — the error and notice codes to expect, grouped and keyed by code

**BLOCKER found 2026-08-31, and it belongs in 11-01 rather than a later viz phase.**
`PanelBuilder._load` (`viz/panel.py:165-167`) ends with:

```python
df_all = df_all[df_all.index >= self.start_date].copy()
df_all.ffill(inplace=True)
df_all.dropna(inplace=True)
```

An outer join followed by `ffill` then `dropna` truncates the WHOLE panel to the latest
first-bar across all tickers. Load a 1990 VIX series beside a 2006 KMLM series and 1990-2006
is silently dropped for every symbol — the only `print` in `_load` fires on a load exception,
not on truncation. This directly defeats the phase goal and would let `COVERAGE.md` claim 1990
while every plot starts in 2006.

**TWO SITES, not one — found 2026-08-31 during the Phase 11 survey.** `_tidy`
(`src/pipelines/cache_prices.py:45`) ends at `:55` with

```python
out = out.astype(float).ffill().dropna(how="any")
```

which is the identical defect one layer EARLIER, in the code that writes the parquet. A
SPY-from-1993 series cached beside a QQQ-from-1999 series is truncated to 1999 **in the file
itself**, before `PanelBuilder` is constructed and before `COVERAGE.md` is generated — so
fixing only the viz loader bakes the loss into the very file the coverage report describes.
`_tidy`'s own comment is correct about why it exists ("the sim must never mark a leg at a
made-up price"): right for a simulator frame, wrong for a coverage frame. The two are not the
same frame.

The compatibility argument differs by site. `panel.py` is read by existing figures, so the
opt-in default below is required there. `cache_prices.py` writes NEW files under NEW tags, so
a long-history tag has no existing reader to regress and ragged retention can simply be the
behaviour for new panels — subject to re-running an existing tag still reproducing its bytes.

**Fix, decided 2026-08-31 — written up as 11-01 Task 4, with AC-6: keep the current behaviour
as the default, add an opt-in mode.**
The existing `ffill` + `dropna` stays the default so no current caller changes behaviour and
no existing figure moves. A new opt-in loading mode retains series with non-overlapping
windows, leaving each series NaN outside its own coverage rather than truncating the panel.
It must work for BOTH price levels and returns series, since the derived-frame wrappers
(`_normalise`, `_pct_returns`, `_daily_returns`, `_log_returns`, `panel.py:187-211`) all run
off the same frame — a rebase or a first-difference on a ragged frame must anchor to each
series' own first observation, not to the panel's.

**Visualisation utilities — the two that exist, and which one owns this.** `PanelBuilder`
(`viz/panel.py`) and `LevelDashApp` (`viz/dash_timeseries_app.py`). Ragged-history inspection
belongs in `PanelBuilder`, since it needs no Dash process — `make_panel_subplots`
(`panel.py:276`) is the entry point. No third utility is needed; the gap was the loader, not
the chart.

**IBKR message reference (11-02).** No such document exists anywhere in the repo today. It
should record the taxonomy the code ALREADY implements rather than invent a parallel one:
`_ADVISORY_ORDER_CODES = {399}` (`ingestion/ibkr_requests.py:445`), the connection-noise filter
`(2104, 2106, 2158, 2176)` (`:820`), the `errorCode >= 2100` advisory threshold (`:859`), and
the three-way `req_messages` / `req_notices` / `req_errors` split. That is an
**advisory-versus-terminal** axis. The grouping requested — account permissions including
geography, IBKR or subscription limits, instrument-level limits (no data at all / not beyond a
date / not at that frequency / not tradeable), and orders versus data — is a **cause** axis.
Both are useful and orthogonal, so the document is keyed by code and carries both as columns.
STATE.md's accumulated context already seeds it: 10314, 2174, and the PRIIPs/KID
client-eligibility rejection. 11-01 Task 1 already captures the verbatim ceiling error text;
11-02 turns that one-off probe output into a standing reference.

### Phase 12: Option overlay engine

**Goal:** The hedge structures of the Obsidian catalogue as executable rules: strike selection,
pricing, theta, and the roll calendar.
**Depends on:** Phase 11 (nothing can be priced without vol history)
**Research:** Likely (skew modelling, roll conventions)

**Scope:**
- Protective put, put spread, rolling collar — the catalogue's three costed structures
- 63-day reset with strikes re-struck to period start, per `How to roll a put hedge`
- Rules that plug into the existing `RebalanceRule.propose` contract so the Phase 2 simulator
  accounts them unchanged

**Plans:**
- [ ] 12-01: Option overlay engine — OptionLeg pricing, roll calendar, ProtectivePut / PutSpread / RollingCollar rules

**Superseded 2026-08-26.** 12-01 is now the option **economics specification**, not a build
plan. Phase 16 groups code by stage, which splits its content three ways: `OptionLeg` into
`strategies/instruments/options.py` (16-02), `RollCalendar` into `strategies/schedule.py`
(16-02), and the three rules into `strategies/rules/options.py` (16-03). A synthetic
contract is a kind of instrument, not a kind of option strategy.

Every economics decision in 12-01 — European pricing with the discounted intrinsic floor,
the bar-based roll, collar redeployment, strike-dependent IV, the floor 0.90 / cap 1.28
parameters — is carried across verbatim. Phase 12 is complete when 16-02 and 16-03 land.

**Partially discharged 2026-08-31.** The vol surface and the European put — the two pieces of
12-01 that were only ever inline code blocks in the plan — are now real modules under
`src/portutils/strategies/instruments/`, verified against 12-01's own quoted numbers by
`research/option_overlay_probe.py`. That probe also produced the first hard cost figure for
the milestone: an ATM three-month put is 3.53% of spot, ≈14% of notional a year rolled
quarterly. Phase 12 is still complete only when 16-02 and 16-03 land in full.

### Phase 13: Walk-forward validation harness

**Goal:** Rolling-origin train/validate: fit parameters on a backward window, score on the forward
window that followed, repeat across many origins.
**Depends on:** Phases 11 and 12
**Research:** Unlikely (mechanics are standard; the wrapping is bespoke)

**Scope:**
- Rolling-origin splitter over the long history
- In-sample parameter search reusing `QuantileRiskControlGrid`'s sweep pattern
- Out-of-sample scoring through `PerformanceSummary`, with in-sample-versus-out-of-sample degradation
  reported as a first-class output, not a footnote

**Plans:**
- [ ] 13-01: To be defined during `/paul:plan`

### Phase 14: Regime labelling and scenario guardrails

**Goal:** Name the environments the results are claimed across, and stress the conclusion against
coherent scenarios history has not produced.
**Depends on:** Phase 13
**Research:** Likely (regime taxonomy, scenario construction)

**Scope:**
- Regime labels over the long history (drawdown depth, rate direction, inflation, correlation state)
- Results reported per regime, so "worked on average" cannot hide a regime where it did not
- Scenario guardrails grounded in economic argument rather than resampled history

**Plans:**
- [ ] 14-01: To be defined during `/paul:plan`

### Phase 15: Conviction write-up — how much hedging is enough

**Goal:** The actual answer, written for a retail investor: which structures earn their complexity,
and how far a simple sleeve gets you.
**Depends on:** Phase 14
**Research:** Unlikely (synthesis of prior phases)

**Scope:**
- The 80/20 argument — what a retail investor can hold without a quarterly roll programme
- Every claim traced to a walk-forward result, with its regime coverage and its limitations stated
- Lands in `research/conviction/` per the repo's research conventions

**Plans:**
- [ ] 15-01: To be defined during `/paul:plan`

## Milestone v0.5: Strategy architecture consolidation

**The problem:** three systems compute portfolio decisions and none import each other —
`analysis/strategies.py` (vectorised, weights, no execution), `portfolio/rules.py`
(event-driven, units, executes into a `Book`), and `orders/kts.py` (live GUI, its own
weight ladder). Weight logic exists three times, units/turnover twice, and the richest
rationale in the codebase (`ForecastView`, kts.py:295) dies with the Tk window while
`Order.rationale` is never populated at all.

**The claim to be defended:** one rule abstraction, one sizing utility and one reflection
path can serve the offline sim, the vectorised sweep and the live GUI without any of them
losing what it currently does.

### Phase 16: Strategy architecture consolidation

**Goal:** A single `strategies/` home with rules as composable classes over a shared
observe → decide → execute → reflect lifecycle.
**Depends on:** Phase 2 (02-02, the rationale channel). Interacts with Phase 12 — see the
sequencing decision in CONTEXT.md
**Research:** Unlikely (consolidation of existing code, not new technique)

**Scope:**
- `strategies/rules/` — one class per rule, each with `propose_weights` / `propose_units` /
  `to_orders` / optional `rationale`
- One shared weight-delta → orders → notional → turnover utility in `portutils`, replacing
  three implementations
- Synthetic-instrument descriptors (option legs) as a utility rules call, not rule internals
- `run_strategies` — combines rules per timestamp across an asset scope
- Reflection through the existing `ReturnsCalculator` / `PerformanceSummary` / `Fill` paths,
  accepting both full and compressed order/fill payloads

**Scoping doc:** `.paul/phases/16-strategy-architecture/CONTEXT.md` — the full refactor is
documented there regardless of what is built when. Two of the five design frictions are now
resolved: the kts weight ladder becomes `ConstrainedWeightRule` (a rule that recommends a
weight constrained by weight floors/caps AND turnover floors/caps), and `ForecastView`
becomes that rule's rationale payload rather than competing with `Order.rationale`.

**Plans — split by RISK, not by topic. Track A is new code and cannot regress anything;
Track B migrates working code and is separately gated and individually skippable.**

*Track A — new code, built into the stage structure from day one:*
- [ ] 16-01: Stage skeleton — `observe` / `constraints` / `sizing` / `orders` / `schedule` /
  `targets` / `instruments` / `rules`. Stage functions seeded from `weights_to_units` by
  COPY, leaving the original in place
- [~] 16-02: Option instruments — `instruments/options.py` (`OptionLeg`) and
  `schedule.py` (`RollCalendar`). Economics spec: 12-01 AC-1, AC-2.
  **Part-landed 2026-08-31, ahead of 16-01:** `instruments/vol.py` (`synthetic_iv_surface`)
  and `instruments/pricing.py` (`black_scholes_put`) exist and reproduce all four of 12-01's
  anchor numbers. Still owed: `SyntheticContract`, `OptionLeg` (delta, theta, a `price()`
  wrapping the pricer), `RollCalendar`. See the "Landed ahead of the plans" section of
  `.paul/phases/16-strategy-architecture/CONTEXT.md` and
  `research/option_overlay_probe.md`
- [ ] 16-03: Option rules — `rules/options.py`, the three structures plus theta drag.
  Economics spec: 12-01 AC-3 through AC-6
- [ ] 16-04: `ConstrainedWeightRule` written fresh against stages 2-3, tested standalone.
  kts.py untouched

*Track B — migrating existing code, each independently gated:*
- [ ] 16-05: Move the four existing rules onto the stage functions; retire the duplicate.
  Gate: 126 tests green + `rebalance_study.py` byte-identical
- [ ] 16-06: Rewire kts.py to `ConstrainedWeightRule`. Gate: golden-fixture parity.
  `ARM_LIVE = True` is committed — never bundle this with another plan
- [ ] 16-07: Vectorised runner and the `analysis/strategies.py` disposition

---
*Roadmap created: 2026-08-01 — migrated from 12 pre-existing plans in `.claude/plans/`*
*Last updated: 2026-08-31 — Phase 10 split into Track A (10-01 static) / Track B (10-02 gated live); Phase 11 gains 11-02 (IBKR message reference), the ragged-history blocker (now TWO sites: `panel.py` and `cache_prices.py`) and its measured starting position; 12-01 vol surface staged v1/v2*
