---
phase: 04-ibkr-read-path
plan: 01
subsystem: ingestion
tags: [ibkr, ibapi, tws, executions, backcast, sync]
requires:
  - phase: 01-pnl-accounting
    provides: Book to sync broker state into
provides:
  - get_executions_data with commission and end callbacks
  - ibkr_sync.py, backcast.py, log_positions.py, check_existing_port.py
affects: [05-live-rebalancer, 06-order-verification, 07-live-verification-runbook]
tech-stack:
  added: [ibapi]
  patterns: ["stub the app, not the network — StubApp holds the same dicts IBApp fills from callbacks"]
key-files:
  created: [src/portutils/portfolio/ibkr_sync.py, src/portutils/portfolio/backcast.py, src/pipelines/log_positions.py, research/check_existing_port.py]
  modified: [src/portutils/ingestion/ibkr_requests.py]
key-decisions:
  - "rebalance_port_basic.py renamed via git mv and tracked as a rename; zero importers repo-wide"
  - "Live round trip deliberately left BLOCKED rather than faked — TWS will not start on this machine"
patterns-established:
  - "Every IBKR request has an explicit end-event; nothing polls"
duration: unrecorded
started: 2026-07-25
completed: 2026-07-26
description: "IBKR read path: executions, sync, backcast and position logging, all offline-tested"
type: Summary
about: "Portfolio-Manager"
---

# Phase 4 Plan 01: IBKR Read Path Summary

**Everything that only reads from Interactive Brokers — executions, account sync, backcast and
position logging — built and driven offline with faked callbacks. The live round trip is blocked.**

> Migrated from [`.claude/plans/ibkr-live-integration.md`](../../../.claude/plans/ibkr-live-integration.md)
> (archive). Reconstructed at PAUL adoption.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: `get_executions_data` + commission/end callbacks | Pass | Driven with fake `execDetails` / `commissionReport` / `execDetailsEnd`: fill captured, commission stored by `execId`, end event set. `reqExecutions` itself untested — needs TWS |
| AC-2: `ibkr_sync.py` | Pass | 11 tests in `tests/test_ibkr_sync.py` |
| AC-3: `backcast.py` | Pass | Round trip recovers a known constant-mix book bar-for-bar; wrong-policy check fires; `separation()` added |
| AC-4: `rebalance_port_basic.py` rename | Pass | `git mv`, tracked as a rename; grep found zero importers repo-wide |
| AC-5: `check_existing_port.py` | Pass (unrun) | Compiles; every imported name resolves. Cannot execute without TWS |
| AC-6: `log_positions.py` | Pass | Append/replace-by-date tested offline: a same-day re-run replaces rather than duplicating |
| AC-7: Markdown explainers | Pass | 5 written (4 planned + `log_positions.md`) |
| AC-8: `rebalance_live.py` + config | Pass | `live_trading` block in `config/settings.yaml`; 12 tests in `tests/test_rebalance_live.py` |
| AC-8b: End-to-end stubbed dry run of `main()` | Pass | All 3 modes correct; caught Finding 4 |
| AC-9: Live round trip | **Blocked** | TWS will not start on this machine |

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/portfolio/ibkr_sync.py` | Created | Syncs broker executions into the Book; 11 tests |
| `src/portutils/portfolio/backcast.py` | Created | Reconstructs a book bar-for-bar; adds separation() |
| `src/pipelines/log_positions.py` | Created | Append or replace-by-date position snapshots |
| `research/check_existing_port.py` | Created | Cell script reading the live account; compiles, unrun against TWS |
| `src/portutils/ingestion/ibkr_requests.py` | Modified | get_executions_data plus commission and end callbacks |

## Deviations from Plan

| Type | Count | Impact |
|------|-------|--------|
| Deferred | 1 | AC-9 live round trip carried into Phase 7 |

## Next Phase Readiness

**Ready:** Account snapshot and contract handling are what Phase 5 values and trades against.
**Concerns:** `reqExecutions` and `check_existing_port.py` have never executed against a real gateway.
**Blockers:** TWS unavailable.

---
*Phase: 04-ibkr-read-path, Plan: 01*
*Completed: 2026-07-26 (AC-9 outstanding)*
