---
phase: 02-rebalance-realisation
plan: 01
subsystem: portfolio
tags: [rebalancing, constant-mix, turnover, yaml-config, plotly]
requires:
  - phase: 01-pnl-accounting
    provides: Book, Fill, RebalanceRule ABC
provides:
  - ConstantMixRule
  - config/asset_universe.yaml with roles and named portfolios
  - rebalance_study.py and the rebalance_realisation research notebook
affects: [03-return-attribution, 05-live-rebalancer, 09-research-half]
tech-stack:
  added: []
  patterns: ["asset universe as YAML config with roles, not hardcoded tickers"]
key-files:
  created: [src/portutils/portfolio/rules.py, config/asset_universe.yaml, src/pipelines/rebalance_study.py, research/rebalance_realisation.py]
  modified: [src/portutils/utils/config.py, src/portutils/analysis/performance.py, src/pipelines/cache_prices.py]
key-decisions:
  - "Asset universe lives in YAML with roles, so pipelines select by role not by ticker literal"
  - "Plan conventions codified in CLAUDE.md — location and relative-link rules"
patterns-established:
  - "Ledger turnover must equal blotter sum of |notional| — checked as an invariant"
duration: unrecorded
started: 2026-07-24
completed: 2026-07-25
description: "Rebalancing wired as the natural source of realised P&L, with a role-based asset universe"
type: Summary
about: "Portfolio-Manager"
---

# Phase 2 Plan 01: Rebalancing as Realised P&L Summary

**Rebalancing became the mechanism that generates realised P&L, driven by a role-based asset universe
in YAML, with a reproducible study across three books.**

> Migrated from [`.claude/plans/rebalance-realisation.md`](../../../.claude/plans/rebalance-realisation.md)
> (archive). Reconstructed at PAUL adoption.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-0: `## Plans` conventions in `CLAUDE.md` | Pass | Location, link rules, corrected verification one-liner |
| AC-1: `ConstantMixRule` | Pass | `src/portutils/portfolio/rules.py`, exported from the package |
| AC-2: `config/asset_universe.yaml` + config accessors | Pass | 14 tickers with roles, 2 named portfolios; `ASSET_UNIVERSE` / `asset_role` / `tickers_by_role` / `portfolio_weights` |
| AC-3: Turnover extras + `performance.py` §7a fix | Pass | 22 tests green; simple-vs-log totals agree to exactly 0.0; ledger turnover equals blotter Σ\|notional\| on all 3 books |
| AC-4: `cache_prices.py` reads YAML; parquet built | Pass | `prices_spy_kmlm.parquet`, 250 bars, 2025-07-28→2026-07-24; `drawdown_rotation_sim.py` migrated to roles and reproduces its old numbers exactly |
| AC-5: `rebalance_study.py` + figures + real run | Pass | 3 books, 10 HTML figures, 6 CSVs in `outputs/scenarios/`; all invariants verified |
| AC-6: `research/rebalance_realisation.py` | Pass | 13 cells, executes end-to-end, 11 figures build |

## Accomplishments

- Turnover accounting that reconciles exactly against the blotter — not approximately
- A configurable universe: pipelines ask for a role, not a ticker literal
- The drawdown-rotation scenario reproduces its pre-migration numbers exactly

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/portfolio/rules.py` | Modified | ConstantMixRule added and exported from the package |
| `config/asset_universe.yaml` | Created | 14 tickers with roles and 2 named portfolios |
| `src/pipelines/rebalance_study.py` | Created | Three-book study producing 10 HTML figures and 6 CSVs |
| `research/rebalance_realisation.py` | Created | 13-cell narrative run building 11 figures end to end |
| `src/portutils/utils/config.py` | Modified | ASSET_UNIVERSE, asset_role, tickers_by_role, portfolio_weights accessors |
| `src/portutils/analysis/performance.py` | Modified | §7a fix; simple-vs-log totals now agree to exactly 0.0 |
| `src/pipelines/cache_prices.py` | Modified | Reads the YAML universe and writes prices_*.parquet |

## Next Phase Readiness

**Ready:** Weight and turnover plumbing is what Phase 3 attribution decomposes.
**Concerns:** None recorded.
**Blockers:** None.

---
*Phase: 02-rebalance-realisation, Plan: 01*
*Completed: 2026-07-25*
