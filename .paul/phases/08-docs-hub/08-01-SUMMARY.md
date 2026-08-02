---
phase: 08-docs-hub
plan: 01
subsystem: docs
tags: [readme, documentation, execution-stack]
requires:
  - phase: 06-order-verification
    provides: the verdict vocabulary the execution doc explains
provides:
  - root README as a hub, EXECUTION_STACK.md, 10 folder-level READMEs
affects: [09-research-half]
tech-stack:
  added: []
  patterns: ["a doc beside its subject is the one people actually update"]
key-files:
  created: [EXECUTION_STACK.md, orders/README.md, research/README.md, src/README.md, src/pipelines/README.md, src/portutils/README.md, src/portutils/analysis/README.md, src/portutils/ingestion/README.md, src/portutils/portfolio/README.md, src/portutils/utils/README.md, src/portutils/viz/README.md, tests/README.md]
  modified: [README.md, .gitignore]
key-decisions:
  - "Folder-level README.md rather than a root docs/ directory"
  - "CLAUDE.md files left untouched — they instruct Claude, READMEs explain the system to a person"
  - "Every path and symbol verified before being written"
patterns-established:
  - "Cross-folder narratives live at root because they belong to no folder"
duration: unrecorded
started: 2026-08-01
completed: 2026-08-01
description: "Root README turned into a navigational hub with one doc per area beneath it"
type: Summary
about: "Portfolio-Manager"
---

# Phase 8 Plan 01: Documentation Hub Summary

**The root README became a hub with a "where do I look for X" table, `EXECUTION_STACK.md` documents
the order path end to end, and ten folder-level READMEs sit beside the code they describe.**

> Migrated from [`.claude/plans/docs-hub-and-execution-stack.md`](../../../.claude/plans/docs-hub-and-execution-stack.md)
> (archive). Reconstructed at PAUL adoption. **The archived Progress Log reads "not started" for all
> five steps — it is stale.** Commit `3701f26` shipped every one of them; the 12 created files and
> the two deleted merge plans are in that commit's diff. This staleness is the specific failure mode
> PAUL's UNIFY step exists to prevent.

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: README hub — sections, corrected tree, "where do I look for X" | Pass | Verified in `README.md` (242 lines) |
| AC-2: `EXECUTION_STACK.md` | Pass | 203 lines, including the verdict vocabulary section |
| AC-3: Folder READMEs (10 files) | Pass | All 10 present on disk and in `3701f26` |
| AC-4: Fold merge plans in, then archive + gitignore | Pass | `CONN_REQUESTS_MERGE_PLAN.md` (241 lines) and `MERGE_INFRASTRUCTURE_PLAN.md` (302 lines) deleted; `archive/` gitignored |
| AC-5: Repo-wide link and symbol verification | Pass | Per the plan's own decision that every path and symbol be verified before being written |

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `EXECUTION_STACK.md` | Created | End-to-end order path, 203 lines, incl. verdict vocabulary |
| `README.md` | Modified | Rewritten as a hub with a corrected tree and a where-do-I-look table |
| `.gitignore` | Modified | archive/ ignored |
| `orders/README.md` | Created | Folder doc |
| `research/README.md` | Created | Folder doc |
| `src/README.md` | Created | Folder doc |
| `src/pipelines/README.md` | Created | Folder doc |
| `src/portutils/README.md` | Created | Folder doc |
| `src/portutils/analysis/README.md` | Created | Folder doc |
| `src/portutils/ingestion/README.md` | Created | Folder doc |
| `src/portutils/portfolio/README.md` | Created | Folder doc |
| `src/portutils/utils/README.md` | Created | Folder doc |
| `src/portutils/viz/README.md` | Created | Folder doc |
| `tests/README.md` | Created | Folder doc — incl. why one test fails on purpose |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Folder-level `README.md`, not a root `docs/` | GitHub renders it on folder browse, and a doc beside its subject is the one people actually update | 10 files, each next to its code |
| `CLAUDE.md` files untouched | They instruct Claude; READMEs explain the system to a person. Merging makes both worse | Two parallel doc systems, deliberately |
| Full docs for the execution stack and portutils; a paragraph for thin areas | `models/` holds no code — a page about it would document an intention and then go stale | Proportionate coverage |
| Every path and symbol verified before being written | The old README listed four pipelines where ten exist — the exact failure mode of documentation written from memory | |
| Merge plans folded out, then archived to a gitignored folder | The step-by-step migration text has no future reader; the layering decisions do, and belong with the stack they describe | |

## Next Phase Readiness

**Ready:** A reader can now find the execution stack without reading source.
**Concerns:** The research half is documented as scaffolded — Phase 9 has to make that true.
**Blockers:** None.

---
*Phase: 08-docs-hub, Plan: 01*
*Completed: 2026-08-01*
