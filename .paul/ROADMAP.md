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
| 10 | Deployment — web app | TBD | Not started | - |
| 11 | Long-history data foundation | 1 | Planning | - |
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
**Depends on:** Phase 7 (the runbook must prove the stack before a browser is allowed to drive it)
**Research:** Likely (hosting, secret handling, how the approval gate re-expresses in a browser)

**Scope:**
- Dashboard: positions, P&L split, attribution figures
- Live-rebalancer control behind an explicit approval gate — the dry-run table re-derived for a browser
- Hosting deliberately undecided; static hosting is largely ruled out (no secrets, no TWS reachability)

**Plans:**
- [ ] 10-01: To be defined during `/paul:plan`

**Numbering note:** appended as 10 rather than 9 because 9 was already claimed by the v0.3 research
phase. v0.2 is therefore a non-contiguous range (4-8, 10) by choice — see the 2026-08-06 decision.

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

**Plans:**
- [ ] 11-01: Reach back decades — paginated IBKR history, free-source fallback, coverage report

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

**Sequencing changed 2026-08-26.** 12-01's Task 1 now executes as **16-02**, writing into
`strategies/rules/options.py` rather than creating `portfolio/options.py`. No option code
exists yet, so building it in the old layout would mean building it twice; the option rules
are also the case that most stresses the Phase 16 interface, since contracts are natively
units and have no weight representation. 12-01's option ECONOMICS — European pricing, the
discounted intrinsic floor, the bar-based roll, collar redeployment, strike-dependent IV —
are unaffected and stand as amended. `depends_on` is now `["11-01", "02-02", "16-01"]`.

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

**Plans:**
- [ ] 16-01: Foundation and clean migrations — rule interface, shared sizing utility, the
  four existing rules moved, `ConstrainedWeightRule` extracted and tested standalone,
  synthetic-instrument descriptors
- [ ] 16-02: Option rules written directly into `strategies/rules/options.py` (absorbs
  12-01 Task 1 — see Phase 12)
- [ ] 16-03: Rewire kts.py to call `ConstrainedWeightRule`, golden-fixture parity against
  the current ladder. Deliberately separate from 16-01: `ARM_LIVE = True` is committed
- [ ] 16-04: Vectorised runner and the `analysis/strategies.py` disposition

---
*Roadmap created: 2026-08-01 — migrated from 12 pre-existing plans in `.claude/plans/`*
*Last updated: 2026-08-26 — v0.5 milestone added (phase 16, strategy architecture); 12-01 amended*
