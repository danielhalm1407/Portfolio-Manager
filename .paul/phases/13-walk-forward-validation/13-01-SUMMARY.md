---
phase: 13-walk-forward-validation
plan: 01
subsystem: strategies
tags: [options, monetisation, backtest, plotly, static-site, portfolio-simulator]

requires:
  - phase: 13-walk-forward-validation
    plan: "01.1"
    provides: NarrowLedger (constant-width ledger), option_ladder_probe.py, classify_option_events
  - phase: 11-long-history-data
    plan: "01"
    provides: prices_long_history.parquet (SPY from 1993), iv_long_history.parquet (real IV from 2006-01-09)
provides:
  - v2 pricing (real IV level + current-spot moneyness) on OptionOverlayRule
  - the monetise / wait / reopen state machine (monetise_drawdown, monetise_multiple, reentry_iv, max_flat_bars)
  - option_monetisation_batch.py — the full-history named-set comparison, now actually runnable
  - a published two-section site (docs/index.html hub, docs/probing/, docs/validation/)
  - persisted per-configuration series + blotter under outputs/option_monetisation/{gfc,full}/ and the batch's own series/blotter/
  - a retrievable runtime_log.csv
  - classify_option_fills (leg-level fill classification), premium_financing_summary
affects: [13-02, 13-03]

tech-stack:
  added: [markdown-it-py (10-01, reused), NarrowLedger (13-01.1, reused)]
  patterns:
    - "one build_report implementation serving two published reports via keyword arguments"
    - "theme.py owns the page-dark CSS (BODY_CSS/darken_page/page_css) and stacked-figure heights (STACKED_FIGURE_HEIGHT), not each pipeline"
    - "a hub page (build_site_index.py) linking section pages, each linking back"

key-files:
  created:
    - research/validation/option_monetisation_gfc.md
    - src/pipelines/build_site_index.py
  modified:
    - src/portutils/strategies/rules/options.py
    - src/pipelines/option_monetisation_batch.py
    - src/pipelines/option_probe_figures.py
    - src/pipelines/build_report.py
    - src/portutils/viz/theme.py
    - src/portutils/strategies/scoring.py
    - research/validation/option_ladder_probe.py
    - tests/test_option_monetisation.py

key-decisions:
  - "AC-3b re-opened and satisfied same day it was descoped — the blocker was a bug, not missing scope"
  - "put_spread_v2_ref/collar_v2_ref cut from the batch's named set — Task 4's GFC report already exercises both under the real policy"
  - "GITHUB_BRANCH corrected to master — pushed directly rather than merging a PR"
  - "docs/validation/index.html is a rendered write-up with generated stats, never a link list"

patterns-established:
  - "a page that is nothing but a figure: write_html + theme.darken_page. A page that is a document containing figures: assemble the head yourself from theme.page_css()"
  - "classify_option_fills underneath classify_option_events, so leg-level detail (long vs short) is one implementation, not a second traversal"

duration: "one session, 2026-09-26"
completed: "2026-09-26T13:15:00+01:00"
description: "The monetisation engine's full-history run, a two-section published site with generated commentary, and two bugs found and fixed while closing the checkpoint (a missing NarrowLedger wiring that meant the batch script had never once completed, and a stale GitHub branch constant)"
type: Summary
about: "Portfolio-Manager"
---

# Phase 13 Plan 01: Option monetisation over the GFC — engine, publication, and a genuinely-run batch

**v2 pricing and the monetise/wait/reopen policy are proven on the real GFC window and published as
a readable site; the full-history batch that AC-5/AC-6 require is, for the first time, actually run
to completion — which is how a `NarrowLedger` wiring gap that had silently blocked it since the
file was created got found and fixed.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | one session, 2026-09-26 (Tasks 1-3 and the four 13-01.1 findings were resolved on prior days, per the plan's own progress log) |
| Tasks | 4 of 4 (Task 4 step 6 — following stale doc links — left as a small non-gating follow-up) |
| Files created | 2 (`option_monetisation_gfc.md`, `build_site_index.py`) |
| Files modified | 8 (see key-files) |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: Defaults change nothing | Pass | Byte-identity hashes unchanged; full suite 227 passed throughout every change this session |
| AC-2: v2 pricing couples the two terms | Pass | Verified on prior days; unaffected by this session's changes |
| AC-3: Monetisation closes the whole structure | Pass | Verified on prior days |
| AC-3b: The two triggers are compared | Pass | **Re-opened same day it was descoped.** Descoped because `monetise_multiple` had no full-history run; that blocker turned out to be the `NarrowLedger` bug below. Fixed, re-ran, and the batch's own `trigger_comparison()` (code that already existed, never executed) printed: drawdown fired 17 times, multiple 44 times, median gap 2 bars, **70.6%** of drawdown firings within 5 bars of a multiple firing |
| AC-4: Re-entry waits for cheaper insurance | Pass | Verified on prior days |
| AC-5: The full history runs in one command | Pass | `python -m pipelines.option_monetisation_batch` now completes in seconds (was: never finished). Five named configs — `unhedged, blind_roll_v1, blind_roll_v2, monetise_drawdown, monetise_multiple` — exactly what the gherkin names, after cutting the two bare reference rows (checkpoint 0c) |
| AC-6: Per-bar series persisted, one run timed | Pass | Series (parquet) + blotter (CSV) now genuinely exist for all five batch configs, plus `comparison_table.csv` and `trigger_events.csv`. Single-run measurement: 0.45s (first config), corroborated at 1.0-1.8s for `protective_put` across several full-history runs via the ladder probe |
| AC-7: Published as a site, not a link list | Pass | `docs/index.html` hub links `docs/probing/` and `docs/validation/`, each linking back; 22 local links site-wide, 0 missing; every page dark; one vendored `plotly.min.js`; figures full-width in both reports |

## Accomplishments

- **The GFC report is a real write-up, not a link list**: `research/validation/option_monetisation_gfc.md`, rendered through a generalised `build_report` (shared with 10-01's probe report — one implementation, 8 new keyword arguments), four structures embedded full-width, a generated summary table, and two generated premium-financing sentences (put spread's short leg financed 34% of the long put's cost; the collar's short call, 10%) — computed via a new `classify_option_fills`, never typed.
- **A published site, not one page pretending to be the whole project**: `docs/index.html` (new `build_site_index.py`) links `docs/probing/` (10-01, moved) and `docs/validation/` (this plan), both sections linking back up, one vendored `plotly.min.js` shared across both, zero pages depending on a CDN.
- **AC-5/AC-6's actual deliverable — the full-history batch — was found to have never run.** `option_monetisation_batch.run_one` never wired `ledger=NarrowLedger()` into its `PortfolioSimulator` call, so every configuration was silently running at the pre-13-01.1 O(dead-legs) cost. This session's first attempt to run it hung past a two-minute timeout, mid-`blind_roll_v2`, flooding stdout with `ledger.py`'s fragmentation warning. Fixed with the one missing argument; the batch now completes in seconds and AC-3b's comparison came out as a side effect.
- **A second stale constant caught the same way**: `GITHUB_BRANCH = "feat/16-option-instruments"` in both page builders, left over from before this session pushed straight to `master`. Fixed, all three published pages rebuilt, verified zero remaining references.
- **13-02's plan updated** with everything from this session that bears on it: the runtime constant corroborated on the actual structure it will sweep, `monetise_multiple` explicitly out of scope, the reopen-reset fix's effect on runtime attributed, and an explicit incremental build order (one window/four strikes/no trigger, then the drawdown trigger, then the rest) confirmed with the user.

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| AC-3b re-opened and satisfied, same day it was descoped | The descoping's premise (`monetise_multiple` has no full-history run) was a bug, not a genuine gap; fixing the bug produced the comparison for free | 13-02 inherits a real trigger-agreement measurement (70.6% within 5 bars) instead of an open question |
| Cut `put_spread_v2_ref`/`collar_v2_ref` from the batch's named set | AC-5's own gherkin never named them (a premise in the plan's own prior text — "AC-5 names them" — was checked and found wrong); Task 4's GFC report already exercises both structures under the real policy, which a bare-defaults reference row could not | `build_configs` now returns exactly the five configs AC-5 specifies |
| `theme.py` owns the page-dark CSS and stacked-figure heights | `apply_export_theme` darkens the figure and cannot reach the page around it (`write_html`'s bare `<body>`); two pipelines had each spelled out the same palette, and `fig_ladder` was missing the height fix `fig_overlay_values`/`fig_put_paths_market` already had | One place to change; `fig_ladder`'s 3-row figure no longer renders squeezed |
| `build_report` generalised with 8 new keyword arguments rather than duplicated | The heading→figure raise-on-mismatch guard, the link rewriting and the CSS are the parts most costly to keep in step across two reports | Both published reports share one implementation; a real `rewrite_links`/`source_dir` bug (see Deviations) was fixed once, for both |
| AC-6's blotter is `sim.blotter()`'s shape, not `rule.events_frame()` | AC-6's gherkin defines it literally; the batch script's own persistence used the richer rule log instead, which is a DIFFERENT artefact, not a superset | Flagged for whoever runs the full batch next, so the two directories' differing columns are expected, not a surprise |

## Deviations from Plan

### Summary

| Type | Count | Impact |
|------|-------|--------|
| Bugs found and fixed (not planned) | 2 | Essential — one silently blocked AC-5/AC-6/AC-3b entirely, the other would have 404ed every source link post-push |
| Scope re-opened after being explicitly descoped | 1 (AC-3b) | Positive — unblocked by the bug fix, not a scope expansion |
| Scope cut (with the user) | 1 (the two reference rows) | Matches a recommendation the plan already carried and had left open |
| Deferred | 1 (Task 4 step 6 — stale doc/cell-comment links) | Non-gating, cosmetic |

**Total impact:** No scope creep. Both bugs were essential fixes surfaced by actually running the
scripts the plan's own acceptance criteria require running — neither was found by inspection.

### Bugs found and fixed

**1. `option_monetisation_batch.run_one` never passed `ledger=NarrowLedger()`**
- **Found during:** closing the human-verify checkpoint, running `python -m
  pipelines.option_monetisation_batch` for what turned out to be the first time it had ever been
  run against the current engine.
- **Issue:** `PortfolioSimulator` defaulted to `StateLedger()` — the O(dead-legs) ledger 13-01.1
  exists to replace. The run hung past a 120-second timeout, warning-flooding stdout with
  `ledger.py:90`'s "DataFrame is highly fragmented" message, mid-`blind_roll_v2`.
- **Fix:** added `ledger=NarrowLedger()` and `book=Book(base_equity=...)` to the `PortfolioSimulator`
  call, matching the convention `option_ladder_probe.run_one` already used.
- **Files:** `src/pipelines/option_monetisation_batch.py`
- **Verification:** re-ran; completed in seconds (0.45s single-run measurement); all five configs'
  series and blotter artefacts now exist under `outputs/option_monetisation/{series,blotter}/`;
  full suite 227 passed / 1 pre-existing unrelated failure.

**2. `GITHUB_BRANCH` stale at `"feat/16-option-instruments"` in two page builders**
- **Found during:** the same closing pass, cross-checking STATE.md's own prior note that this
  exact class of bug would bite on merge.
- **Issue:** this session pushed straight to `master` rather than merging a PR, so the branch
  constant was stale by a different path than the one STATE.md anticipated, but the same defect —
  every "generated from"/source link on both published reports would 404.
- **Fix:** `GITHUB_BRANCH = "master"` in both `build_report.py` and `build_site_index.py`; rebuilt
  all three published pages.
- **Files:** `src/pipelines/build_report.py`, `src/pipelines/build_site_index.py`
- **Verification:** `grep -c "feat/16-option-instruments"` returns 0 across all three pages; the
  hub's footer link resolves to `.../tree/master`.

### Real bug found and fixed mid-build (not a checkpoint discovery, logged for completeness)

**3. `rewrite_links` resolved docs-internal links relative to an assumed `docs/index.html`**
- **Found during:** Task 4 step 3, building the second report.
- **Issue:** the function stripped a hardcoded `"docs/"` prefix rather than computing a path
  relative to the actual page. Correct only while the probe report sat at `docs/index.html`; once
  it moved to `docs/probing/index.html`, its own `../docs/README.md` link resolved one level too
  shallow.
- **Fix:** `rewrite_links` takes `page_dir`/`source_dir` and uses `posixpath.relpath`.
- **Verification:** full-site link check, 0 missing, both before and after the branch fix above.

### Deferred Items

- Task 4 step 6: following the remaining stale mentions of `docs/figures/`/`docs/index.html` in
  `docs/README.md`, `research/option_overlay_probe.py`'s cell comments, and the root `README.md`.
  Cosmetic, non-gating.
- The AC-6 blotter-shape discrepancy (`sim.blotter()` vs `rule.events_frame()`) noted above —
  not reconciled, flagged for whoever next reads both directories side by side.
- Checkpoint item 2's stated expectation ("v2 dearer in 2008 AND 2020") does not fully hold: v2 is
  dearer in GFC 2007-2009 and in Rates 2022, but slightly CHEAPER than v1 in COVID 2020 (-29.54 vs
  -31.66). Reported rather than explained away; not investigated further this session.

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `research/validation/option_monetisation_gfc.md` | Created | The GFC report's prose, rendered — never retyped |
| `src/pipelines/build_site_index.py` | Created | The published site's hub, `docs/index.html` |
| `src/portutils/strategies/rules/options.py` | Modified (prior sessions) | v2 pricing, the monetise/wait/reopen state machine |
| `src/pipelines/option_monetisation_batch.py` | Modified | `build_series_frame` extracted for reuse; `ledger=NarrowLedger()` fix; two reference-row configs cut; `GITHUB_BRANCH`-class fix not applicable here (no such constant) |
| `src/pipelines/option_probe_figures.py` | Modified | Moved under `docs/probing/`; vendored-library path; shared page CSS via `theme.page_css()` |
| `src/pipelines/build_report.py` | Modified | Generalised to serve two reports; `rewrite_links` `page_dir`/`source_dir` fix; `GITHUB_BRANCH` fix |
| `src/portutils/viz/theme.py` | Modified | `BODY_CSS`/`darken_page`/`page_css`/`STACKED_FIGURE_HEIGHT` added |
| `src/portutils/strategies/scoring.py` | Modified | `classify_option_fills` extracted from `classify_option_events` |
| `research/validation/option_ladder_probe.py` | Modified | GFC report wiring, blotter/series/runtime persistence, full-history smoke-test cells (10-12), `premium_financing_summary` |
| `tests/test_option_monetisation.py` | Modified (prior sessions) | 23 new tests for v2 pricing and the state machine |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| Batch script hung past a 120s timeout with no visible output | Diagnosed via a background run with unbuffered stdout; traced to the missing `ledger=` argument |
| A print line computed "max drawdown" from the rule's own trigger-reference `drawdown` column, which resets on reopen | Fixed to `(equity / equity.cummax() - 1).min()`, the same computation the summary table uses |
| `build_report`'s first call for the GFC report used the wrong `plotly_src` (the `figures/`-depth constant, not the section-index depth) | Caught by the link check; fixed |

## Next Phase Readiness

**Ready:**
- 13-02 can size its grid from a corroborated runtime constant (1.0-1.8s per full-history run on
  the actual structure it sweeps), not an extrapolation.
- 13-02's plan now explicitly excludes `monetise_multiple`, attributes part of the speed to the
  2026-09-21 reopen-reset fix, and states an incremental build order confirmed with the user.
- The published site is a stable base to add a third section to later (one directory, one
  `SECTIONS` entry, per `build_site_index.py`'s own design).

**Concerns:**
- The AC-6 blotter-shape discrepancy (two different "blotter" artefacts under two different
  directories) should be resolved, or at least documented somewhere more permanent than this
  SUMMARY, before 13-02 or 13-03 read from `outputs/option_monetisation/blotter/`.
- Checkpoint item 2's COVID-2020 discrepancy is unexplained.

**Blockers:** None.

---
*Completed: 2026-09-26*
