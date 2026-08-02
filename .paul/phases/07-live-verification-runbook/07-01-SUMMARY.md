---
phase: 07-live-verification-runbook
plan: 01
subsystem: testing
tags: [cell-banners, runbook, check-existing-port]
requires:
  - phase: 04-ibkr-read-path
    provides: check_existing_port.py
provides:
  - 13 cell-label banners in check_existing_port.py
affects: [07-live-verification-runbook]
tech-stack:
  added: []
  patterns: ["cell scripts carry numbered labels so a runbook can name a step precisely"]
key-files:
  modified: [research/check_existing_port.py]
key-decisions:
  - "Banners inserted with a parse + diff verification requiring zero deleted lines"
patterns-established:
  - "Insert-only edits verified by requiring no '-' lines in the diff"
duration: unrecorded
started: 2026-07-26
completed: 2026-07-26
description: "Cell-label banners added to check_existing_port.py so the runbook can name steps"
type: Summary
about: "Portfolio-Manager"
---

# Phase 7 Plan 01: Cell-Label Banners Summary

**`check_existing_port.py` gained 13 numbered cell banners, so the staged runbook can refer to a
specific step rather than "the cell after the one with the table".**

> Migrated from [`.claude/plans/if-you-go-to-replicated-tarjan.md`](../../../.claude/plans/if-you-go-to-replicated-tarjan.md)
> (archive). Reconstructed at PAUL adoption.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: Insert 13 cell-label banners | Pass | |
| AC-2: Parse + diff verification | Pass | Zero `-` lines required and achieved — the edit was insert-only |
| AC-3: `log_positions.py` run | **Pending** | User-driven, needs TWS — moved to plan 07-02 |
| AC-4: `check_existing_port.py` cells run | **Pending** | User-driven, needs TWS — moved to plan 07-02 |

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `research/check_existing_port.py` | Modified | 13 cell-label banners inserted; diff verified insert-only |

## Next Phase Readiness

**Ready:** The runbook has stable step names to reference.
**Concerns:** None.
**Blockers:** The two run steps need TWS; they are tracked in plan 07-02.

---
*Phase: 07-live-verification-runbook, Plan: 01*
*Completed: 2026-07-26*
