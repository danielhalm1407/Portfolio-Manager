# PAUL Session Handoff

**Session:** 2026-10-17 → 2026-10-18
**Phase:** 13 — Walk-forward validation harness
**Plan:** 13-02 (in-sample grid and response surface)
**Context:** 13-02 built end to end through Task 5, published as a third site section. The blocking
human-verify checkpoint is NOT approved and UNIFY has NOT run.

---

## Session Accomplishments

### 13-02, Tasks 1 through 5 — all built, all verified

| Task | What landed | Where |
|---|---|---|
| 1 | Six indicative windows (3 drawdown, 3 calm), `max_drawdown` with peak reset at slice start, floored Calmar returning `floor_bound`, tidy `score_grid`, `spy_reference_rows`, worst-of-two `rank_configs` | `src/portutils/strategies/scoring.py`, `tests/test_scoring.py` (18 tests) |
| 2a | `run_config` — the single code path every later step calls, with the state-leak assertion | `src/pipelines/option_grid_sweep.py` |
| 2b | 63-combination Cartesian sweep (9 moneyness × 7 trigger depths incl. blind roll), each simulated once over 2006-2026, three artefacts persisted per combination | same, `main()` |
| 3 | Penalised B-spline surfaces (Eilers-Marx, scipy): `fit_1d`, `fit_2d`, `optimum`, `snap_to_strike` | `src/portutils/strategies/surface.py`, `tests/test_surface.py` (14 tests) |
| 4 | Four figures: raw grid heatmap, per-window surfaces, pooled contour, pooled 3-D | `src/pipelines/option_grid_figures.py` |
| 5 | Reading script that loads persisted artefacts and never re-simulates | `research/validation/option_grid_scan.py` |

**Measured:** 63 trials, projected 6.6 min from 13-01.1's recorded constant, **actual 2.7 min**
(2.58 s/combination). Ledger width constant across all 63 runs. Artefacts in gitignored
`outputs/option_grid/`.

### Repository reorganisation

All three option research scripts gathered under `research/validation/` and renamed for what they
answer rather than the order they were written:

- `research/option_overlay_probe.{py,md}` → `research/validation/option_ladder_prices_probe.{py,md}`
- `research/validation/option_ladder_probe.py` → `option_hedge_structures_probe.py`
- `research/validation/option_monetisation_gfc.md` → `option_hedge_structures_probe.md`
- new: `option_moneyness_optimisation.py` / `.md`, `option_grid_scan.py`

### Publication — third site section

`docs/optimisation/` now live alongside `docs/probing/` and `docs/validation/`, rendered through
the same `build_report`. All three write-ups rewritten for a public reader: planning-sequence
framing and "Finding N" numbering removed, the volatility surface and Black-Scholes forms written
out as typeset equations, calibration status stated with its consequence.

**Site verified:** 19 Plotly script tags and every local link resolve; 0 unsubstituted
placeholders on any page.

### Commits — all pushed to `master`

```
16eb602  docs: rewrite the research write-ups for readers, and publish the moneyness study
29ea8b2  feat(13-02): the full grid, the response surface and the reading script
9a66604  feat(13-02): the moneyness axis — indicative windows, floored Calmar, stacked reading script
b4f2358  refactor(13-02): gather the three option research scripts under research/validation/
```

Working tree clean apart from `.claude/settings.json` (pre-existing, unrelated, deliberately
excluded from every commit). Suite **259 passed, 1 pre-existing `ARM_LIVE` failure**.

---

## Findings

### The headline: nothing works in both regimes

| | |
|---|---|
| Spearman rank correlation, crisis set vs calm set | **−0.637** |
| Configurations in the top ten of both | **none** |
| Crisis winner (1.00× / 20%) — its calm rank | **63rd of 63** |
| Calm winner (0.93× / 6%) — its crisis rank | 31st |
| Best worst-of-two (0.93× / 8%) | 21st in each |

Not a weak trade-off — an actively negative one. AC-4's worst-of-two sort exists for exactly this
and it fired.

### The decision is made by calm periods, not crises

Within-window score spread across all 54 monetising configurations:

```
GFC 2007-2009     0.088      Calm 2023-2024    2.300
COVID 2020        0.631      Calm 2017         3.380
Rates 2022        0.995      Calm 2013-2015    5.059
```

Through the GFC every configuration lands within 0.088 of floored Calmar. Through 2013-2015 the
same 54 span 5.059 — roughly sixty times the dispersion. This inverts the usual framing: a hedging
programme should be designed around carry, with crisis performance as a constraint.

### Four method problems found and handled

1. **Floored Calmar inverts on losing windows.** Negative numerator over a larger denominator moves
   towards zero, so a deeper drawdown scores better. GFC blind roll: 0.80× (−54.0% return, 59.5%
   drawdown) scored −0.908 and ranked *above* 0.95× (−42.4%, 44.5%) at −0.953. All three drawdown
   windows inherit this. **Not fixed** — AC-3 fixes the metric. Documented in `scoring.calmar`'s
   header and printed as a runtime caveat.
2. **Pooling raw scores fits a flat plane** (R² 0.009 at maximum smoothing). Window identity
   dominates the variance so GCV correctly reports no parameter signal. A window-demeaned pooled
   fit was added beside it (edf 8.19, R² 0.236, optimum 0.873 / 6%), labelled as a deviation from
   window mean, **not** a Calmar.
3. **GCV tied at zero residual and the tie was decided by float noise**, returning the most
   flexible fit — a straight line came back with 31 effective degrees of freedom. Fixed: residuals
   floored relative to TSS, lambda swept smooth→flexible so a tie keeps the smoother fit.
4. **edf does not measure spikiness in 2-D**, which AC-5 assumed. With no noise to regularise
   against it reports basis representability: broad bowl edf 31.3, narrow spike 21.9 — the wrong
   way round. Curvature discriminates (−8 vs −3,232). `test_surface.py` pins this deliberately
   backwards so it fails if the assumption ever becomes true.

### Secondary findings

- **Fits sit near interpolation.** New `fit_vs_raw` diagnostic runs every sweep: positions agree
  with raw argmax in all six windows (max gap 0.014), but three windows claim a height no
  simulated cell reached (Rates 2022 fitted +0.520 against +0.161 actual). Positions quotable,
  heights not.
- **The drawdown floor never binds** — 0% of 378 rows, calm windows included. Their drawdowns run
  7-14%, far above 2%. The floor is currently inert.
- **Monetisation is inert in calm windows.** Event counts: 2013-2015 and 2017 monetise zero times,
  2023-2024 once. Every difference between strikes there is pure premium drag.
- **Snapping is free.** Pooled optimum 0.8730 → strike 665 (0.8731), cost 0.00000.
- **COVID window carries no fills** — 40 bars is shorter than the 63-bar roll, so `premium_in_window`
  reports 0.0 meaning "nothing bought in these bars", not "free". Documented at the function.

---

## Decisions Made

| Decision | Rationale | Impact |
|---|---|---|
| `MONEYNESS_GRID` widened to 9 points, 0.80–1.00 step 0.025 (plan said 7 points, 0.85–0.97) | User request. A surface fitted over 0.85–0.97 cannot say whether the optimum is interior or at an edge; interleaving also makes every ladder strike a grid point | 63 combinations rather than 49; still 2.7 min |
| Blind roll appears only at the GFC, not across every window or in the grid | User request. It is a control, not a candidate — 13-01 settled that it gives gains back. Carrying it everywhere would halve the moneyness resolution | Grid is monetising-only; one GFC control panel retained |
| Grid colour normalised **within each column** | User request. Scores run ≈ −0.7 in crises and ≈ +5 in calm, so a shared scale showed only which regime was benign | `fig_score_grid(normalise="column")`; raw value still printed and hovered |
| SPY carried as its own grid **row** | User request. "Did this beat holding the thing" is invisible as a column | `scoring.spy_reference_rows` |
| Second grid: score ÷ SPY's score, one shared scale centred on 1.0 | User request. Comparable across windows in a way the raw score is not | `score_vs_spy` column; sign trap documented (ratio > 1.0 means WORSE on negative windows) |
| `POOLED_DEMEANED` fit added beside the raw pooled fit | The raw pooled fit is empty (R² 0.009). Both kept: the raw one is what AC-5 literally asks for and its emptiness is a reportable null | Pooled figures drawn from the demeaned fit, labelled as such |
| scipy DECLARED in `pyproject.toml` | Plan requires whichever smoother library is used to be declared rather than relied on transitively through scikit-learn. pygam not installed and not worth a dependency for two axes | One new declared dep, no new install |
| Legend moved to the side on stacked figures | User request — a horizontal top legend wrapped through the title at ten entries | New `theme.apply_side_legend`, the no-secondary-axis counterpart to `apply_secondary_axis_spacing` |
| Shared IV path drawn in ink and sorted to legend bottom | It had taken `CATEGORICAL[0]`, the same blue the underlying keeps, and was added mid-loop so it interleaved among the strikes | Cosmetic but the legend was misleading |
| **13-02 published to `docs/`, amending AC-6** | User approved explicitly. AC-6 and SCOPE LIMITS both said nothing under `docs/` is written | `docs/optimisation/` is a new section; **this amendment must be recorded at UNIFY** |
| Write-ups rewritten for a public reader | User request. They were internal progress notes — planning-sequence framing, "Finding N" numbering, corrections to earlier drafts in body text | Three `.md` rewritten; three pages rebuilt |
| Maths typeset in the page's own CSS, not KaTeX/MathJax | Every page vendors its Plotly copy specifically to render with no network; a CDN maths library would give that up for a handful of formulas | New `.math` rule in `build_report._CSS` |

---

## Gap Analysis with Decisions

### The human-verify checkpoint is NOT approved
**Status:** OPEN — blocking
**Notes:** 13-02's plan ends in a `checkpoint:human-verify` gate marked `gate="blocking"`. It was
presented with all eight of its questions answered, and the user moved on to publication without
approving. UNIFY must not run before this clears.
**Reference:** `@.paul/phases/13-walk-forward-validation/13-02-PLAN.md`

### UNIFY has not run for 13-02
**Status:** OPEN — required by PAUL rule 3
**Notes:** Every APPLY must be followed by UNIFY. Four commits of 13-02 work are on `master`
with STATE.md, ROADMAP.md and the ledger all still describing 13-01 as the last activity.
**Effort:** One session; needs the checkpoint approved first.

### AC-6's `docs/` boundary was deliberately crossed
**Status:** INTENTIONAL — needs recording
**Notes:** AC-6 states "nothing under `data/` or `docs/` is written" and SCOPE LIMITS lists `docs/`
as not-regenerated. The user approved publication explicitly after being told this. It is a plan
amendment, not a slip, and belongs in the decision log at UNIFY rather than being left implicit.

### The metric is unsound on three of six windows
**Status:** OPEN — user decision required
**Notes:** Floored Calmar inverts on negative-return windows. Every crisis-window ranking on the
published page inherits it. The page states this in the body rather than footnoting it, and reads
crisis conclusions off returns and drawdowns directly. Replacing the metric would invalidate the
published rankings and require a re-run (2.7 min) and a re-publish.
**Reference:** `@src/portutils/strategies/scoring.py` — `calmar` docstring carries the measurement

### `.claude/settings.json` modified and uncommitted
**Status:** DEFER — pre-existing
**Notes:** Modified before this session began; permission-list changes unrelated to 13-02.
Excluded from all four commits deliberately.

### `ARM_LIVE = True` still committed
**Status:** DEFER — pre-existing, tracked in STATE Blockers
**Notes:** `orders/rebalance_live_debug.py:67`. Causes the one standing test failure by design.

---

## Open Questions

Three were put to the user at the checkpoint and none were answered:

1. **Does floored Calmar stay as AC-3's metric, given the inversion on losing windows?**
   If it is replaced, 13-02's rankings and the published optimisation page both need regenerating.

2. **Does 13-03 carry the pooled surface, the per-window surfaces, or neither?**
   The optimum moves between regimes (at-the-money edge in all three crises, 0.911–1.000 at the
   shallow trigger edge in calm), which suggests a single pooled optimum is the wrong object and a
   **regime-conditional rule** may be the more defensible candidate to validate.

3. **Which window set goes into CPCV?**
   The six indicative windows are an in-sample scoring device, not necessarily the right blocks for
   a purged/embargoed split.

A fourth, not yet raised: **optima sit at axis edges** in five of six windows, so "the optimum is
at 1.000×" partly means "the axis stopped there". Whether to widen the moneyness axis beyond 1.00
or accept the edge is a 13-03 scoping question.

---

## Reference Files for Next Session

```
@.paul/phases/13-walk-forward-validation/13-02-PLAN.md      # the checkpoint and its 8 questions
@.paul/phases/13-walk-forward-validation/CONTEXT.md         # CPCV design, subject to 13-02 overrides
@.paul/phases/13-walk-forward-validation/13-01-SUMMARY.md   # engine; the runtime constant
@src/portutils/strategies/scoring.py                        # windows, metric, the inversion note
@src/portutils/strategies/surface.py                        # smoothers; the display-not-selection boundary
@src/pipelines/option_grid_sweep.py                         # sweep, fits, publication
@research/validation/option_grid_scan.py                    # reads persisted artefacts, no simulator
@research/validation/option_moneyness_optimisation.md       # the published write-up
@outputs/option_grid/scores_tidy.csv                        # 378 scored rows
@outputs/option_grid/fit_vs_raw.csv                         # fitted vs raw argmax per window
@outputs/option_grid/surface_fits.csv                       # lam, edf, curvature per fit
```

---

## Prioritized Next Actions

| Priority | Action | Effort |
|---|---|---|
| 1 | **Approve or reject the 13-02 human-verify checkpoint** — answer the three open questions, especially the metric | Discussion |
| 2 | **Run `/paul:unify`** for 13-02 — reconcile STATE/ROADMAP/ledger, write `13-02-SUMMARY.md`, record the AC-6 publication amendment and the four method findings as decisions | 1 session |
| 3 | If the metric is replaced: re-run the sweep and re-publish | ~10 min + re-publish |
| 4 | **Plan 13-03** — CPCV at N=6 / k=2, selection by discrete grid argmax (never by refitting the smoother inside a split), PBO figure. Trial count 63 is recorded and ready | 1 session to plan |
| 5 | Consider whether 13-03 validates a regime-conditional rule rather than one parameter set | Folds into 4 |

Alternatives already scoped if Phase 13 is paused: 02-02/02-03 (trade rationale), 07-02, 04-05,
11-02, 10-02.

---

## State Summary

**Current:** Phase 13, plan 13-02. Loop position **PLAN ✓ → APPLY ✓ → UNIFY ○**, held at the
blocking human-verify checkpoint. 13-01 and 13-01.1 closed. Phase 13 is 3 of 4 applied, not
transitioned.

**Published:** three-section site — `docs/index.html` hub → `docs/probing/`, `docs/validation/`,
`docs/optimisation/`. One vendored `plotly.min.js`, no CDN dependency anywhere.

**Next:** approve the checkpoint, then `/paul:unify`.

**Resume:** `/paul:resume` then read this handoff.

---

*Handoff created: 2026-10-18*
