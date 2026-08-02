---
phase: 05-live-rebalancer
plan: 01
subsystem: pipelines
tags: [rebalancing, multi-currency, ibkr, orders, dry-run, gbx]
requires:
  - phase: 04-ibkr-read-path
    provides: contract specs, account snapshot, marketValue
provides:
  - rebalance_live.py with base-currency valuation and multi-currency order submission
  - live_book vector in config/asset_universe.yaml
affects: [06-order-verification, 07-live-verification-runbook]
tech-stack:
  added: []
  patterns: ["universe = target weights union current holdings, so exits are planned not forgotten"]
key-files:
  modified: [src/pipelines/rebalance_live.py, config/asset_universe.yaml, src/portutils/ingestion/ibkr_requests.py]
key-decisions:
  - "Valuation goes through IB's marketValue rather than price x quantity — it already carries the FX conversion"
  - "Instruments quoted in pence (GBX) while the contract currency is GBP need explicit handling"
patterns-established:
  - "Dry run first, always; live requires explicit approval of the dry-run table"
duration: unrecorded
started: 2026-07-26
completed: 2026-07-26
description: "Multi-currency live rebalancer valuing in base currency and submitting by con_id"
type: Summary
about: "Portfolio-Manager"
---

# Phase 5 Plan 01: Multi-Currency Live Rebalancer Summary

**Target weights become real multi-currency orders: valued in base currency via `marketValue`,
submitted by `con_id`, over a universe that is the union of targets and current holdings.**

> Migrated from [`.claude/plans/rebalance-live-multicurrency.md`](../../../.claude/plans/rebalance-live-multicurrency.md)
> (archive). Reconstructed at PAUL adoption. The plan's Progress Log still read "pending" at
> migration time; the code was verified present — `live_book` at `config/asset_universe.yaml:194`,
> `contract_specs` threaded through `ibkr_requests.py` and `rebalance_live.py`.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: `asset_universe.yaml` 5 ticker entries + `live_book` vector | Pass | Settings points at it; pence/GBX note recorded inline |
| AC-2: `submit_market_order` / `submit_limit_order` take `con_id` | Pass | |
| AC-3: `submit_rebalance_orders` takes `contract_specs` | Pass | |
| AC-4: Base-currency valuation via `marketValue` | Pass | |
| AC-5: Universe = weights ∪ held; marks for unheld targets | Pass | Exits are planned rather than forgotten |
| AC-6: Full diagnostic table + contract specs threaded | Pass | |
| AC-7: Tests extended; full suite green | Pass | 125 passing at migration; 1 by-design failure from the armed debug harness |
| AC-8: User's dry run, then live run | **Pending** | User-driven, needs TWS — carried into Phase 7 |

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/pipelines/rebalance_live.py` | Modified | Base-currency valuation via marketValue; universe = weights union held |
| `config/asset_universe.yaml` | Modified | live_book vector and 5 ticker entries, incl. the GBX/GBP pence note |
| `src/portutils/ingestion/ibkr_requests.py` | Modified | Order submitters take con_id; submit_rebalance_orders takes contract_specs |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Value via `marketValue` | IB already applies the FX conversion; recomputing it invites drift | Base-currency totals reconcile against the account snapshot |
| Universe includes current holdings | A target of zero is still a trade | Exits appear in the diagnostic table |

## Next Phase Readiness

**Ready:** Orders exist to be verified — which is Phase 6.
**Concerns:** No live dry run has been approved yet.
**Blockers:** TWS unavailable.

---
*Phase: 05-live-rebalancer, Plan: 01*
*Completed: 2026-07-26 (AC-8 outstanding)*
