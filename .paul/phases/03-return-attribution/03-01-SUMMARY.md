---
phase: 03-return-attribution
plan: 01
subsystem: analysis
tags: [attribution, returns, refactor, plotly, theme]
requires:
  - phase: 02-rebalance-realisation
    provides: turnover extras and weight plumbing
provides:
  - ReturnAttribution and simulate_weights in analysis/performance.py
  - wide-matrix return helpers in analysis/returns.py
  - viz/theme.py palette module
affects: [08-docs-hub, 09-research-half]
tech-stack:
  added: []
  patterns: ["viz/ holds rendering only; analysis/ owns the maths", "shims preserve public tuple shapes across a move"]
key-files:
  created: [src/portutils/viz/theme.py]
  modified: [src/portutils/analysis/returns.py, src/portutils/analysis/performance.py, src/portutils/viz/panel.py]
key-decisions:
  - "simulate_weights takes rets explicitly rather than reading self — decouples maths from the panel object"
  - "panel.attribution kept as a shim preserving the 5-tuple, so callers did not break"
patterns-established:
  - "Comments merged and retained verbatim when code moves between modules"
duration: unrecorded
started: 2026-07-26
completed: 2026-07-26
description: "Return attribution and return/weight definitions moved from viz/ into analysis/, with shims preserving callers"
type: Summary
about: "Portfolio-Manager"
---

# Phase 3 Plan 01: Return Attribution Consolidation Summary

**Attribution maths left the visualisation layer for `analysis/`, with baselines captured first and
a shim keeping every existing caller working.**

> Migrated from [`.claude/plans/return-attribution-consolidation.md`](../../../.claude/plans/return-attribution-consolidation.md)
> (archive). Reconstructed at PAUL adoption.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-0: Mirror plan to repo with relative links | Pass | |
| AC-1: Capture pre-change baselines | Pass | Verifications 1, 7, 8 — captured before any edit, which is what made the rest checkable |
| AC-2: Wide-matrix helpers → `returns.py`; panel wrappers | Pass | |
| AC-3: `simulate_weights` → `performance.py` | Pass | Takes `rets`, not `self` |
| AC-4: `ReturnAttribution` in `performance.py` | Pass | Imports helpers from `returns.py` |
| AC-5: `panel.attribution` → shim | Pass | 5-tuple preserved |
| AC-6: `fig_attribution` → caller + residual leg | Pass | |
| AC-7: Comments merged and retained | Pass | Repo policy — comments travel verbatim |
| AC-8: Verifications 1-10 | Pass | Results recorded in the archived plan |

## Accomplishments

- A clean layering rule the codebase now follows: `analysis/` computes, `viz/` renders
- Theme constants extracted to `viz/theme.py`; 10 HTML figures regenerated, 52 tests passing
- Baselines captured before edits, so "unchanged" was demonstrated rather than asserted

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/viz/theme.py` | Created | Palette and export-theme constants lifted out of the pipeline |
| `src/portutils/analysis/returns.py` | Modified | Wide-matrix return helpers moved here from viz/ |
| `src/portutils/analysis/performance.py` | Modified | simulate_weights and ReturnAttribution moved in; take rets, not self |
| `src/portutils/viz/panel.py` | Modified | attribution reduced to a shim preserving the 5-tuple for callers |

## Next Phase Readiness

**Ready:** Attribution is importable for research use in Phase 9.
**Concerns:** Attribution figures still need a human review pass (carried into Phase 7).
**Blockers:** None.

---
*Phase: 03-return-attribution, Plan: 01*
*Completed: 2026-07-26*
