---
phase: 01-pnl-accounting
plan: 01
subsystem: portfolio
tags: [pnl, accounting, refactor, pytest, tkinter]
requires: []
provides:
  - portutils.portfolio package (Fill, Order, Position, Book, SimExecutionBackend, StateLedger, PortfolioSimulator)
  - multi-symbol accounting replacing three single-symbol scalars
affects: [02-rebalance-realisation, 04-ibkr-read-path]
tech-stack:
  added: []
  patterns: ["property shims over an extracted engine", "golden-fixture parity tests"]
key-files:
  created: [src/portutils/portfolio/book.py, src/portutils/portfolio/fills.py, src/portutils/portfolio/execution.py, src/portutils/portfolio/ledger.py, src/portutils/portfolio/rules.py, src/portutils/portfolio/simulator.py, tests/test_book_parity.py]
  modified: [orders/kts.py]
key-decisions:
  - "Extract and migrate rather than duplicate — kts.py delegates, single source of truth for the §4b rules"
  - "Scenario fills driven by both scripted rules and hand-specified trade lists"
patterns-established:
  - "sim_* attributes become property shims over Book"
  - "Parity pinned against orders/replay_state.json before any behaviour change"
duration: unrecorded
started: 2026-07-25
completed: 2026-08-01
description: "Multi-symbol GUI-free P&L engine extracted from kts.py, parity-pinned to 1e-9"
type: Summary
about: "Portfolio-Manager"
---

# Phase 1 Plan 01: P&L Accounting Extraction Summary

**The realised/unrealised P&L engine moved out of the Tk app into `portutils.portfolio`, generalised
from three single-symbol scalars to N symbols, with kts.py delegating to it and parity pinned to 1e-9.**

> Migrated from [`.claude/plans/pnl-accounting-extraction.md`](../../../.claude/plans/pnl-accounting-extraction.md)
> (archive). This SUMMARY was reconstructed at PAUL adoption, not produced by a UNIFY cycle.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: `fills.py` + `book.py` + unit tests | Pass | `tests/test_book.py` — 12 tests: grow/reduce/close/flip/short-symmetry, multi-symbol independence, TOTAL aggregate, default-symbol compat |
| AC-2: Parity vs `orders/replay_state.json` | Pass | `tests/test_book_parity.py` — 535 bars, 10 orders, fill-by-fill and bar-by-bar, matches to 1e-9. Fixture carries long and short legs and 222 bars of non-zero realised |
| AC-3: `execution.py`, `ledger.py` | Pass | `SimExecutionBackend(book, on_order=…, slippage_k=0.0)`; `StateLedger` splits accounting fields from strategy `extra` |
| AC-4: kts.py migration (properties → `Book`) | Pass (GUI smoke outstanding) | `tests/test_kts_migration.py` drives a headless `KalmanTradingApp` through the full replay, matching the original export to 1e-9 including the §7.5 order ledger |
| AC-5: `rules.py`, `simulator.py`, pipelines, scenario run | Pass | `tests/test_simulator.py` (6 tests) plus a real run of `src/pipelines/drawdown_rotation_sim.py` |

## Accomplishments

- An importable, GUI-free accounting engine — using it no longer requires instantiating a Tk window
- Multi-symbol book: a hedge sleeve and a high-beta basket can be represented simultaneously
- Golden-fixture parity means the extraction is provably behaviour-preserving

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/portfolio/book.py` | Created | Position and Book — the §4b accounting rules generalised to N symbols |
| `src/portutils/portfolio/fills.py` | Created | Fill, Order and _iso, moved verbatim out of kts.py |
| `src/portutils/portfolio/execution.py` | Created | SimExecutionBackend, de-app-ified from kts.py |
| `src/portutils/portfolio/ledger.py` | Created | StateLedger — splits accounting fields from strategy extras |
| `src/portutils/portfolio/rules.py` | Created | RebalanceRule ABC plus DrawdownRotationRule and TradeListRule |
| `src/portutils/portfolio/simulator.py` | Created | PortfolioSimulator — price series to rules to fills to state |
| `tests/test_book_parity.py` | Created | Golden-fixture parity against orders/replay_state.json to 1e-9 |
| `orders/kts.py` | Modified | sim_* scalars became property shims over Book; ~30 call sites rebound |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Extract + migrate kts.py, not duplicate | One source of truth for the §4b accounting rules | ~30 call sites rebound to property shims |
| Prices from cached parquet in `data/processed/` | Pulled once via `get_equity_data`; keeps scenario runs offline and reproducible | `cache_prices.py` pipeline added |

## Next Phase Readiness

**Ready:** `Book`, `Fill` and the rule ABC are the primitives Phase 2 rebalancing builds on.
**Concerns:** Live Tk smoke test and replay scrub were never done by hand — headless parity only.
**Blockers:** None.

---
*Phase: 01-pnl-accounting, Plan: 01*
*Completed: 2026-08-01*
