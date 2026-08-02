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
from market commentary into scored tilts and conviction briefs (v0.3).

## Milestones

| Version | Name | Phases | Status | Completed |
|---------|------|--------|--------|-----------|
| v0.1 | Accounting & attribution engine | 1-3 | ✅ Shipped | 2026-07-26 |
| v0.2 | Live execution against IBKR | 4-8 | 🚧 In Progress | - |
| v0.3 | Research half | 9 | 📋 Planned | - |

## Current Milestone

**v0.2 Live execution against IBKR** (v0.2.0)
Status: In progress
Phases: 4 of 5 complete

Progress: [████████░░] 80%

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with [INSERTED])

| Phase | Name | Plans | Status | Completed |
|-------|------|-------|--------|-----------|
| 1 | P&L accounting engine | 1 | Complete | 2026-08-01 |
| 2 | Rebalancing as realised P&L | 1 | Complete | 2026-07-25 |
| 3 | Return attribution consolidation | 1 | Complete | 2026-07-26 |
| 4 | IBKR read path | 4 | Complete | 2026-08-02 |
| 5 | Live multi-currency rebalancer | 1 | Complete | 2026-07-26 |
| 6 | Order verification & recovery | 2 | Complete | 2026-07-26 |
| 7 | Staged live verification runbook | 2 | In progress | - |
| 8 | Documentation hub | 1 | Complete | 2026-08-01 |
| 9 | Research half — themes to tilts | TBD | Not started | - |

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

---
*Roadmap created: 2026-08-01 — migrated from 12 pre-existing plans in `.claude/plans/`*
*Last updated: 2026-08-01*
