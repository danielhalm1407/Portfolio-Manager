---
phase: 04-ibkr-read-path
plan: 03
subsystem: analysis
tags: [backcast, verdicts, rendering, feature-flag]
requires:
  - phase: 04-ibkr-read-path
    provides: backcast.py and the unpriced-holding report
provides:
  - per-symbol backcast verdicts and SHOW_SUSPECT_BACKCAST flag
affects: [07-live-verification-runbook]
tech-stack:
  added: []
  patterns: ["render suspect data marked, rather than hiding it"]
key-files:
  modified: [research/check_existing_port.py]
key-decisions:
  - "Cells render regardless of verdict, with suspect symbols labelled — hiding them made the gap invisible"
patterns-established:
  - "sym_verdict / label_for / banner / title_tag as the classification vocabulary"
duration: unrecorded
started: 2026-07-26
completed: 2026-07-26
description: "Backcast cells render regardless of verdict, with per-symbol suspect marking"
type: Summary
about: "Portfolio-Manager"
---

# Phase 4 Plan 03: Backcast Render When Inconsistent Summary

**Backcast cells 9-11 now render even when a symbol's reconstruction is inconsistent, marking the
suspect symbols instead of silently suppressing the whole view.**

> Migrated from [`.claude/plans/backcast-render-when-inconsistent.md`](../../../.claude/plans/backcast-render-when-inconsistent.md)
> (archive). Reconstructed at PAUL adoption.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: Header block restated | Pass | |
| AC-2: `SHOW_SUSPECT_BACKCAST` flag | Pass | |
| AC-3: Cell 8c classification | Pass | `sym_verdict` / `label_for` / `banner` / `title_tag` |
| AC-4: Cell 9 gate + trusted-only exposure table | Pass | |
| AC-5: Cell 10 gate + `verdict` column, unpriced rows kept | Pass | |
| AC-6: Cell 11 gate + excluded-weight share | Pass | |
| AC-7: Verifications 1, 2, 5 | Pass | |
| AC-8: Verifications 3, 4 (live TWS re-run, flag-off regression) | **Pending** | Needs TWS — carried into Phase 7 |

## Accomplishments

- The excluded-weight share is now visible, so a partially-trusted backcast can be judged rather than guessed at

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `research/check_existing_port.py` | Modified | SHOW_SUSPECT_BACKCAST flag; cells 8c-11 gated with per-symbol verdicts |

## Next Phase Readiness

**Ready:** Verdict vocabulary reused by the order-verdict work in Phase 6.
**Concerns:** Flag-off regression unrun.
**Blockers:** TWS unavailable.

---
*Phase: 04-ibkr-read-path, Plan: 03*
*Completed: 2026-07-26 (AC-8 outstanding)*
