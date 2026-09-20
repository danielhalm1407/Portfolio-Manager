---
phase: 10-deployment-web-app
plan: 01
subsystem: pipelines/publishing
tags: [static-site, github-pages, plotly, markdown, report, theme]

requires:
  - phase: 16-strategy-architecture
    provides: "16-02/16-03's figures and the option_overlay_probe.md write-up this page publishes"
provides:
  - "src/pipelines/build_report.py — a write-up + a {heading: figure} map rendered to ONE themed HTML page"
  - "docs/index.html is now the narrative report, not a link list"
  - "theme.apply_secondary_axis_spacing / theme.apply_export_spacing — legend clearance for a right-hand axis, inline and on export"
affects: [Phase 10 Track B, any future report page, every figure with a secondary y-axis]

tech-stack:
  added: [markdown-it-py]
  patterns:
    - "The prose exists once, in the Markdown file; the page renders it and never retypes it"
    - "A mapped heading that no longer matches RAISES rather than publishing a figure under the wrong finding"
    - "One figure object serves the notebook and the page; only spacing differs, via an explicit export call"

key-files:
  created:
    - src/pipelines/build_report.py
  modified:
    - src/pipelines/option_probe_figures.py
    - src/portutils/viz/theme.py
    - research/option_overlay_probe.py
    - research/option_overlay_probe.md
    - docs/README.md
    - pyproject.toml

key-decisions:
  - "markdown-it-py DECLARED in pyproject rather than relied on transitively (the objection pricing.py records about scipy)"
  - "Two legend-x constants — inline 1.04, export 1.10 — because legend.x is a fraction of plot-area width and the page renders wider"
  - "Spacing constants and helpers live in theme.py, not in the pipeline that needed them first (user's call, 2026-09-20)"

duration: ~2h
started: 2026-09-20
completed: 2026-09-20
description: "The option-probe write-up and its five figures published as one static, server-free HTML report at docs/index.html"
type: Summary
about: "Portfolio-Manager"
---

# Phase 10 Plan 01: The narrative report page Summary

**`docs/index.html` is now the project's first public piece of analysis: the whole
`option_overlay_probe.md` write-up, in order, with each figure interactive and inline at the finding
it belongs to. 366 KB, no server, no CDN, no build step.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~2h, one session |
| Tasks | 1 decision checkpoint + 2 auto (both PASS) + 1 human-verify checkpoint (approved 2026-09-20) |
| Files | 1 created, 6 modified |
| Suite | 167 passed, the single known ARM_LIVE failure unchanged |
| Page size | 366 KB (AC-3's budget: under 2 MB) |

## Acceptance Criteria Results

| Criterion | Status | Evidence |
|-----------|--------|----------|
| AC-1: one page, prose and figures in narrative order | Pass | Five figure divs, each after its mapped `<h2>`: iv_smirk/Finding 2, put_paths/Finding 3, iv_paths/Finding 5, drawdown_episode/Finding 6, overlay_values/Finding 7. Hover, zoom and legend verified in the browser at the checkpoint. |
| AC-2: the prose is never retyped | Pass | Every sentence comes from `research/option_overlay_probe.md`; no prose in any `.py`. `_split_sections` raises `KeyError` naming an unmatched heading — verified by renaming one and seeing the build fail. |
| AC-3: no CDN, no bloat, no new runtime | Pass | One `<script src="figures/plotly.min.js">`, `include_plotlyjs=False` per figure, no `cdn.plot.ly` anywhere, 366 KB, no CI or bundler added. |
| AC-4: reads as a dark-themed document | Pass | ~46rem measure for prose, `overflow-x:auto` on tables, figures in a full-width breakout block, colours from `theme.py`. Phone-width check done at the checkpoint. |

## Accomplishments

- **`build_report.py` is generic, not a one-off.** It takes a write-up path and a
  `{heading-substring: figure-stem}` map, so the next report is a call rather than a copy.
- **Links survive publication.** `rewrite_links` keeps `docs/`-relative targets (the standalone
  figure pages the write-up points at) and turns every source/PAUL link into a GitHub blob URL on
  the current branch, carrying `#L123` anchors across.
- **One command does everything.** `python -m pipelines.option_probe_figures` writes the five
  figure pages, the link-list index and the report; cell 10 of the probe script is that call.
- **The figure layout problems raised at the checkpoint were all fixed and verified by rendering
  the figures to PNG at page width and inspecting them**, rather than by eyeballing the notebook:
  legend/secondary-axis collision, the `premium paid` label overlapping a spot tick, and a wide
  empty band on the three-panel figure.

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/pipelines/build_report.py` | Created | Markdown + figures → one themed page; link rewriting; loud failure on a stale heading map |
| `src/pipelines/option_probe_figures.py` | Modified | `main()` calls `build_report` last; `_inline_theme` gained the spacing call and its override; `fig_overlay_values` hides an unused right axis and reclaims its x-domain; two-line `premium<br>paid` label with an opaque background |
| `src/portutils/viz/theme.py` | Modified | `SECONDARY_AXIS_*` constants, `apply_secondary_axis_spacing`, `apply_export_spacing`, `_is_secondary_axis` |
| `research/option_overlay_probe.py` | Modified | Cells 9 and 10 swapped so the export runs last |
| `research/option_overlay_probe.md` | Modified | Cell references updated to match |
| `docs/README.md` | Modified | What the page is, both regeneration commands, the vendored-plotly trade-off |
| `pyproject.toml` | Modified | `markdown-it-py` declared |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| `markdown-it-py` declared in `pyproject.toml` | It was already installed transitively; `pricing.py` records the standing objection to depending on a package the repo does not declare. Tables are where a hand-rolled converter breaks | One declared dependency, pure Python, no build step |
| Two legend-x values: inline 1.04, export 1.10 | `legend.x` is a fraction of the PLOT AREA's width while the margin is pixels, so one number cannot be right in both renders; the page is much wider | `apply_export_spacing` is called only on the export paths; the notebook keeps its width |
| The spacing constants and helpers live in `theme.py` | User's call, 2026-09-20: presentation numbers belong beside the palette, not in whichever pipeline needed them first | Also removed a deferred import in `build_report` that existed only to break the resulting cycle |
| `apply_export_spacing` stays separate from `apply_export_theme` | `_inline_theme` calls `apply_export_theme` too (with transparent backgrounds — that is how the notebook gets the theme's ink and grid), so folding the export legend x into it would give every inline figure the export spacing | Two functions: one at construction, one only before a write |

## Deviations from Plan

| Type | Count | Impact |
|------|-------|--------|
| Boundary override | 1 | The plan lists `src/portutils/**` under DO NOT CHANGE. `theme.py` was modified anyway, on the user's explicit instruction, to move the spacing constants and helpers out of the pipeline. Additive only — existing `theme.py` callers are untouched and the suite is unchanged |
| Scope additions | 4 | All user-requested at the checkpoint: the figure-width breakout CSS, the legend/secondary-axis clearance, the two-line `premium paid` label with an opaque background, and the cell 9/10 swap |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| A wide empty band between the three panels and the legend on `overlay_values` | Not a margin problem. `make_subplots(specs=[..., {"secondary_y": True}, ...])` shrinks every shared x domain to `(0, 0.94)` to reserve a strip for the right axis, at construction, before it can know whether any series will land there — and on this window none does. The axis is now hidden and `domain=[0.0, 1.0]` restored when it is unused |
| The `premium paid` label overlapping a secondary-axis tick | It is a paper-space annotation while the spot ticks land wherever the data puts them, so no margin separates them reliably. Given an opaque background the colour of the figure surface, so it masks what it covers |
| `\n` not breaking the annotation onto two lines | Plotly renders annotation text as HTML — `<br>`, not a Python newline |

## Next Phase Readiness

**Ready:**
- Track B (10-02) is unaffected and still depends on Phase 7, as designed.
- The report generator is reusable: a second write-up needs a path and a heading map.

**Concerns:**
- `GITHUB_BRANCH = "feat/16-option-instruments"` in `build_report.py` must be updated to `master`
  when this branch is merged, or every source link on the published page 404s.
- GitHub Pages still has to be ENABLED in repository settings — a settings change, not a code
  change, and the one thing standing between this page and being publicly served.
- `plotly.min.js` remains 4.2 MB committed once; `include_plotlyjs="cdn"` is the one-word fallback.

**Blockers:** None.

---
*Phase: 10-deployment-web-app, Plan: 01*
*Completed: 2026-09-20*
