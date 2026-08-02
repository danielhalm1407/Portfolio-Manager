---
phase: 04-ibkr-read-path
plan: 02
subsystem: ingestion
tags: [ibkr, contracts, historical-data, non-us, con-id]
requires:
  - phase: 04-ibkr-read-path
    provides: get_equity_data and the IBApp callback surface
provides:
  - contract_specs_from_portfolio
  - partial-success semantics for get_equity_data
affects: [05-live-rebalancer]
tech-stack:
  added: []
  patterns: ["errors set pending events so a failed request cannot hang the caller"]
key-files:
  modified: [src/portutils/ingestion/ibkr_requests.py, src/portutils/ingestion/__init__.py]
key-decisions:
  - "A rejected symbol degrades to partial success rather than failing the whole batch"
patterns-established:
  - "Resolve contracts by con_id where available; never guess the exchange"
duration: unrecorded
started: 2026-07-26
completed: 2026-07-26
description: "get_equity_data no longer hangs on error and resolves non-US holdings by con_id"
type: Summary
about: "Portfolio-Manager"
---

# Phase 4 Plan 02: Non-US Contracts and Hang Fix Summary

**`get_equity_data` stopped hanging on a rejected request and learned to resolve non-US holdings,
by threading `con_id` through contract construction and degrading to partial success.**

> Migrated from [`.claude/plans/ibkr-hist-nonus-contracts.md`](../../../.claude/plans/ibkr-hist-nonus-contracts.md)
> (archive). Reconstructed at PAUL adoption.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: `error()` sets pending events + `req_errors` | Pass | A failed request can no longer leave the caller waiting forever |
| AC-2: `contract(con_id=…)` | Pass | |
| AC-3: `contract_specs_from_portfolio` | Pass | Exported from `portutils.ingestion` |
| AC-4: `get_equity_data` specs + partial success + `what_to_show` | Pass | |
| AC-5: Cell 7 rewired | Pass | `app=ib_app`, specs, unpriced-holding report |
| AC-6: Offline verification 1, 2, 4, 6 | Pass | |
| AC-7: Live re-run of cell 7 against TWS (verification 3, 5) | **Pending** | Needs TWS — carried into Phase 7 |

## Accomplishments

- The hang class of bug is structurally closed: every error path sets the event the caller waits on
- Foreign holdings price correctly instead of being silently dropped

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/ingestion/ibkr_requests.py` | Modified | error() sets pending events; contract(con_id); get_equity_data partial success |
| `src/portutils/ingestion/__init__.py` | Modified | Exports contract_specs_from_portfolio |

## Next Phase Readiness

**Ready:** Contract specs are the input Phase 5 threads into order submission.
**Concerns:** Verifications 3 and 5 unrun.
**Blockers:** TWS unavailable.

---
*Phase: 04-ibkr-read-path, Plan: 02*
*Completed: 2026-07-26 (AC-7 outstanding)*
