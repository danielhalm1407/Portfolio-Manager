---
description: "Phase 13 discussion context — how the parameter sweep generates its own target variable, which validation scheme scores it, and what 13-02 must therefore build"
phase: 13-walk-forward-validation
type: Context
about: "Portfolio-Manager"
---

# Phase 13 — Discussion Context

Written 2026-09-20. This sits ABOVE [13-01-PLAN.md](13-01-PLAN.md) and was the input to the
13-02 plan. It records the decisions taken with the user about **how** the parameter search and
its validation should work, which 13-01 currently leaves almost entirely implicit.

> **Status as of 2026-09-20, after 13-02 was written: this file is no longer the source of truth
> for 13-02's scope.** It was drafted before the sweep was sized and before the work was split
> across plans, and several of its early decisions were overruled in the drafting.
> [13-02-PLAN.md](13-02-PLAN.md) is authoritative wherever the two disagree. The superseded
> points, each flagged inline below:
>
> | section | what this file says | what actually holds |
> |---|---|---|
> | §2.4 | raw grid argmax vs smoothed argmax compared, as a result | discrete grid argmax is the selection rule in 13-03; the comparison is deferred to 13-04 |
> | §6 | primary metric is strategy maxDD / SPY maxDD; Calmar only "supporting" | **floored Calmar is the single target variable**; the drawdown ratio is a descriptive column that never ranks |
> | §8.2 | the drawdown trigger is added in 13-02 | 13-01 built it; 13-02 consumes it |
> | §8.3 | the sweep is moneyness x drawdown x tenor | **tenor is descoped** — fixed at 63 bars, two axes only |
> | §5, §8.5 | CPCV lives in 13-02 | CPCV, purging, embargo and PBO are **13-03**; 13-02 is in-sample only and stops at the trial count |
> | §8.7 | rolling-origin walk-forward in 13-02 | not 13-02; sequenced after 13-03 |
>
> Sections 3, 4, 6.1, 6.2 and 7 are unaffected and are what 13-02 implements.

The full conceptual treatment — why the target has to be simulated, what purging and embargo do,
the CPCV path formulas, and the spline families — lives in the Obsidian vault at
`Knowledge/Finance/Quant finance/Out-of-sample validation of strategy parameters.md`
(`obsidian_notes` repo). This file is the short, decision-oriented version.

---

## 1. What 13-01 leaves open

13-01 builds the monetisation state machine and runs a small, hardcoded set of configurations
in-sample. Reviewed against what the user actually wants to answer, four gaps:

| gap | detail |
|---|---|
| moneyness is not swept | `floor = 0.90` is fixed from 12-01; "how far out of the money is best" is the headline question and no axis exists for it |
| tenor is not swept | the 63-bar roll is fixed, and moneyness cannot be tuned without also fixing or varying the rollover period |
| trigger is price-spike only | 13-01 monetises on `long leg value / premium >= M`; the user's framing is that insurance should pay on the **loss event**, i.e. a drawdown threshold |
| no search or selection design | no Cartesian-product structure, no metric definition, no out-of-sample scheme, no runtime budget for a grid |

**Decision: all four are 13-02's scope, not 13-01's.** Grid tuning only means anything over the
long history, so 13-01 stays as written (in-sample policy engine plus the first full-history run)
and everything below lands in 13-02.

---

## 2. Decisions taken 2026-09-20

1. **Trigger.** Drawdown of the underlying is the **primary** monetisation trigger — close the
   structure when the underlying is X% below its running peak. The existing value-over-premium
   multiple stays as a **comparator**, and 13-02 must report how often the two fire on the same
   bar. The hypothesis under test is that they largely coincide.
2. **Structures.** Long put only for the sweep (`ProtectivePutRule`). Put spread and collar run
   once at defaults as reference rows. Reason: the interpretable question is about a single long
   option; combined structures confound the moneyness reading.
3. **Surrogate staging.** One dimension first (performance against moneyness alone, everything
   else at defaults), then one dimension per parameter separately, and only then a
   two-dimensional tensor-product smooth with interaction. Do not start at the surface.
4. **Validation.** Both the raw discrete grid argmax and the smoothed argmax, compared —
   whichever degrades less out of sample is the reported method, and the comparison is a result
   rather than an assumption.
   > **SUPERSEDED (13-02).** The comparison stands as an eventual goal, but not as the starting
   > point. The smooth fit is an **in-sample display** in 13-02 and nothing more; it is not
   > promoted to a selection rule. 13-03 selects by **discrete grid argmax** on each of the 15
   > splits' training groups, because that is the weaker, more transparent selection step and it
   > needs no smoothing parameter chosen per split. Running the smoothed argmax as a second
   > selection rule doubles 13-03's selection surface and invites a fitted-per-split smoother to
   > leak; it is deferred to a **13-04** plan whose whole job is that comparison, if 13-03's
   > result makes it worth having.
5. **Sourcing.** The methodology note is external-research backed and cited; 13-02 should not
   re-derive it.

---

## 3. The part that changes the engineering

**The simulator must not be re-run per evaluation window.** Run each parameter combination once
over the full 2006-2026 history and persist its **per-bar profit and loss series**. Every
window's score is then a slice-and-aggregate of that stored series.

- Cost falls from (grid size) x (number of origins) simulations to (grid size) simulations.
- Daily-stepped origins become affordable, which is what the user assumed would be prohibitive.
- Consequence to state explicitly in the plan: the rule's state machine carries across a window
  boundary, so each window is scored **as if the overlay had already been running** rather than
  starting flat. For a hedging overlay that is the more realistic convention, but it is a choice,
  not a neutral default, and the alternative (restart-from-flat per origin) restores the
  expensive cost structure.

This single design point should drive 13-02's Task 1: the batch layer must emit a per-bar P&L
series per configuration, keyed by the parameter vector, before any scoring code is written.

---

## 4. Window and origin sizing — the honest constraint

Real IV starts 2006-01-09, so the usable window is 5,204 bars (about 20 years) and contains
roughly **four** major drawdowns.

- A 1,000-bar (~4 year) training window may contain **zero** drawdowns. For a hedging strategy
  that window teaches nothing, regardless of bar count. **The binding constraint is the number of
  drawdown events, not calendar length.**
- Stepping a 1,000-bar window daily gives ~4,200 origins overlapping by 99.9%. The effective
  independent sample is closer to four. Any statistic reported as though n = 4,200 is misleading,
  and 13-02 must state the effective count beside the nominal one.
- Therefore report **both**: a handful of named, interpretable regimes (2007-10 to 2009-03,
  2020-02 to 2020-03, 2022-01 to 2022-10) for the reader, and the full rolling set for stability
  statistics only.
- **The indicative set must include CALM windows, not only drawdowns.** Every crisis window is one
  in which more protection wins, because the premium drag never gets a chance to hurt; scoring only
  those selects mechanically for the heaviest, most expensive hedge available. Pair the drawdown
  windows above with calm ones (2013-2015, 2017, 2023-2024). **The parameter combination worth
  choosing is the one near the top in BOTH sets** — a materially harder bar than topping the
  crisis set, and the real content of the indicative-window stage.
- Window length, step and anchored-versus-rolling are themselves tunable and must be **fixed
  before results are inspected**, and recorded in the plan. Otherwise the walk-forward becomes
  another thing that was optimised.

---

## 5. Validation scheme — what to build

> **SCOPE NOTE (13-02).** Everything in this section is **13-03's** work, not 13-02's. 13-02 is
> in-sample by construction: it sweeps the grid, scores the indicative windows of Section 4, fits
> the surfaces of Section 7, and records the trial count that the PBO figure below consumes. No
> splitter, no purging, no embargo and no PBO figure is written in 13-02. The design recorded
> here — $N=6$, $k=2$, 15 splits, 5 paths, exhaustive enumeration, purge horizon = option tenor —
> carries into 13-03 unchanged, with the one amendment that the per-split selection rule is the
> **discrete grid argmax** (see §2.4 above).

- **Out-of-sample** is the property wanted; walk-forward and CPCV are two protocols for it, not
  competitors. Walk-forward answers "could I have known this at the time"; CPCV answers "is this
  parameter good in general". Run both, for different questions.
- **Walk-forward** (primary): fit on a backward window, score on the window that follows, roll.
  Rolling rather than anchored, given regime sensitivity. Conventional in-sample to out-of-sample
  ratios are 3:1 to 5:1.
- **Purging and embargo** are corrections applied inside a scheme, not schemes themselves. Here
  the purge horizon is the **option tenor** (a position opened near a boundary resolves after it),
  not a label window.
- **CPCV** (the systematic stage, after the indicative windows): $N$ contiguous groups, $k$ as
  test, giving $\binom{N}{k}$ splits and $\varphi[N,k] = \frac{k}{N}\binom{N}{k} = \binom{N-1}{k-1}$
  complete paths. **Chosen setting: $N=6$, $k=2$ — 15 splits, 5 out-of-sample paths.** The
  combinations are enumerated **exhaustively, not sampled at random**.
  - Each group is then about 3.5 years, and the purge horizon is the option tenor (63 bars), so
    purging costs roughly 1.5% of each boundary. Purging is close to free at this group size.

#### Where the 5 comes from

Counted two ways over the same 30 cells:

1. **By split.** 15 splits, each testing $k = 2$ groups, so $k\binom{N}{k} = 2 \times 15 = 30$
   out-of-sample blocks in total.
2. **By group.** G1 is a test group in every pair containing 1 — $\{1,2\},\{1,3\},\{1,4\},\{1,5\},
   \{1,6\}$, five of them — and by symmetry so is every group. In general
   $\binom{N-1}{k-1} = \binom{5}{1} = 5$. Cross-check: $6 \times 5 = 30$. ✓

A path needs one block per group position, so it consumes $N = 6$ blocks. Hence

$$
\varphi[6,2] \;=\; \frac{k\binom{N}{k}}{N} \;=\; \frac{30}{6} \;=\; 5 \text{ paths}
$$

The $\tfrac{2}{6}$ is simply *blocks produced per split over blocks consumed per path* — a plain
division, not an adjustment for overlap or double-counting. The equivalent form
$\varphi[N,k] = \binom{N-1}{k-1}$ needs no division and reads directly as "how many splits test any
given group".

The block-to-path assignment is **arbitrary** beyond the rule "one block per group position per
path"; many valid decompositions exist and none is canonical. Paths are a bookkeeping device for
organising 30 blocks into 5 full-length histories.

#### What overlaps, and the real meaning of the discontinuity caveat

- **Within a path**: the 6 blocks are the 6 consecutive, disjoint groups in chronological order.
  They tile the history end to end — no gaps, no overlaps, full coverage exactly once.
- **Across paths**: all 5 paths span the same calendar period. They overlap completely in time,
  which is the point — five views of the same 20 years.
- **What changes at a group boundary inside a path** is not the date but which split produced that
  block, and hence which training subset stood behind it.

So the caveat is **not** about time gaps between blocks. Two time-adjacent blocks in one path come
from two different runs, so the simulated position at the end of one block belongs to a different
run's history than the position assumed at the start of the next. **Score per test block and
aggregate; do not glue the 5 paths into 5 continuous equity curves.**

### 5.1 What a CPCV path measures — corrected 2026-09-20

**A path is the performance of the PROCEDURE, not of a fixed parameter set.** Each block along a
path came from a different split, and in each split the parameters were re-selected on that
split's own training groups. So the parameters attached to consecutive blocks of one path are
generally different from each other. What the path traces is "fit on what you have, deploy on what
comes next, repeat", and the distribution across the 5 paths is the sampling distribution of that
pipeline's out-of-sample performance.

The claim CPCV supports is therefore **architecture-level**: the protective put with drawdown
monetisation, tuned by this selection rule, holds up out of sample regardless of which particular
moneyness happened to win in any one training set.

Two consequences:

- **A good CPCV result does not tell us which parameters to deploy.** It certifies the procedure.
  Deployment is a separate step — run the selection on the most recent training window and use
  what it returns. Do not report the CPCV distribution as though it endorsed a parameter vector.
- The selection step here is weak (an argmax over a grid, not a fitted classifier), so leakage
  exposure is mild but not zero. That is what the PBO figure measures.

### 5.2 Three distinct exercises, only two of which are validation

| exercise | training step? | uses the combinatorial structure? | answers |
|---|---|---|---|
| **procedure validation** (canonical CPCV) — primary | yes, parameters re-selected per split | yes | is the architecture plus selection rule robust out of sample? |
| **selection diagnostic** (PBO) — supporting | yes | yes | does the in-sample winner land below the out-of-sample median of all trials? |
| **block-wise rank stability** — descriptive only | no | no | does the same combination rank high across sub-periods? |

**Correction to the earlier draft of this file, which named rank stability as the primary and
called it CPCV.** It is neither. With no fitting step the 15 splits and 5 paths do no work — there
are only 6 disjoint blocks, and identical numbers would fall out of a single partition into 6
sub-periods. It stays in the plan as an in-sample robustness display (effectively the
indicative-window stage on a regular partition) and must be labelled as such, not as validation.

The honest out-of-sample form of the "which combination is best" question is the selection
diagnostic: rank on the training groups, take the winner, score it on the test blocks, and compare
against the distribution of all combinations on those same blocks. That form does need the
combinatorial structure, because it needs many train/test draws.

All three are affordable once the per-bar series of Section 3 exist.
- **Trial counting.** The grid size *is* the number of trials, which is honestly known here.
  Record it and report probability of backtest overfitting (in-sample best landing below the
  out-of-sample median of trials).

---

## 6. Metric — settled 2026-09-20

Raw return is disqualified: a hedge is supposed to cost money in calm markets, so ranking by
return selects the parameters that hedged least. All metrics are expressed as ratios so that they
are dimensionless and comparable across windows and across price levels.

> **SUPERSEDED (13-02) — the roles in the table below were inverted.** The decision taken when
> 13-02 was drafted is that **floored Calmar is the single TARGET VARIABLE**: the one number that
> ranks the grid, that the response surface is fitted to, and that 13-03 optimises. The floor is
> kept (`max(max_drawdown, 0.02)`) precisely for the calm-window instability the old "supporting"
> row describes, and the share of window-combination pairs where the floor BOUND is reported so a
> floored score is never read as a Calmar. The other two metrics are **demoted to descriptive
> columns**: computed, carried in the tidy frame, shown beside every result, and never used as a
> ranking key, a fitting target or a selection criterion. One target variable, because a surface
> can only be fitted to one surface height, and because two ranking keys that disagree turn
> selection into an unstated judgement call.

| role | metric | notes |
|---|---|---|
| **target** | **floored Calmar** — window return / max(window max drawdown, 0.02) | the ranked, fitted and optimised quantity. Floor is a named constant, stated in every output; floor-bound share reported |
| descriptive | strategy max drawdown as a fraction of SPY max drawdown, same window | bounded, interpretable ("kept 60% of the loss"), stable across regimes. Reported, never ranks |
| descriptive | drawdown reduction (% of spot) per premium paid (% of spot) | the efficiency-of-insurance reading; both terms in % of spot so the ratio is unitless. Reported, never ranks |

**Share of bars spent FLAT stays a mandatory separate column** (already an AC-5 requirement in
13-01) rather than being folded into any metric — a policy can score well by sitting unhedged.

### 6.1 What is persisted, and what is derived

Only the **per-bar equity (or per-bar P&L) series** is a stored primitive. Max drawdown is **not**
sliceable — a window's drawdown cannot be recovered from the full history's drawdown — so every
drawdown statistic, and everything built on one (the primary metric, Calmar), is computed at
scoring time from the equity slice. A configuration's stored artefact is a time series, not a row
of summary statistics.

### 6.2 Drawdown peak convention — must be stated

Within a window, drawdown runs against a running peak. Either the peak resets at the window's
first bar, or the pre-window high carries in. A window opening mid-decline shows almost no
drawdown under the first convention and a large one under the second. **Decision: reset at window
start**, as the more comparable choice across windows. State it beside every result.

---

## 7. Surrogate model — what to fit and what not to

Fit: **smoothing spline** in one dimension, then a **penalised tensor-product smooth** in two,
with the smoothing parameter chosen by generalised cross-validation and effective degrees of
freedom reported. `pygam` gives the statistics (`te()` terms, edf, partial dependence);
`scipy.interpolate` gives the fit but not the diagnostics.

Not: **lasso / ridge** (regularisers for high-dimensional linear coefficients; there is no
variable-selection problem here), **trees** (piecewise constant — they produce exactly the spiky
surface the exercise argues against). **Gaussian process / kriging** is the reasonable second
stage if uncertainty bands are wanted; not the starting point.

Outputs: the raw grid heatmap (unsmoothed design points), the fitted surface as a filled contour
on the same axes, and the 3-D surface with the optimum marked plus the nearest tradeable strike
beside it.

**Two caveats the plan must carry:**

1. The in-sample $R^2$ of the surface is **not** an estimate of generalisation error. The scatter
   around the fit is path-specific detail, not measurement noise — the simulator is deterministic
   at fixed window.
2. **A smooth surface is not by itself evidence against overfitting.** Smoothness rules out
   dependence on one lucky cell; it does not rule out a smooth ridge in the wrong place. Peak
   stability across origins is the separate, necessary check — plot where the argmax sits at each
   origin over time.

Plus one reporting item: listed options exist only at discrete strikes, so report the **snapping
cost** — the performance difference between the surface's continuous optimum and the nearest
tradeable strike. A material gap means the surface is too sharp to trust.

---

## 8. What this implies for the 13-02 plan

Sketch, for `/paul:plan` to turn into tasks. **Written before the split across plans; the routing
column is the correction.** The list is kept as drafted, because the reasoning behind each item is
still the reasoning, and struck through in prose rather than deleted.

Ordered as the user described the workflow on 2026-09-20: run the whole history once per
combination, read the indicative windows first, then go systematic with CPCV.

| # | item | actually lands in |
|---|---|---|
| 1 | per-bar persistence | **13-01** (its AC-6) |
| 2 | drawdown trigger + co-firing comparison | **13-01** |
| 3 | Cartesian sweep, **two axes not three** | **13-02** |
| 4 | windowed scoring on the indicative set | **13-02** |
| 5 | CPCV, PBO, rank stability | **13-03** |
| 6 | surrogate fitting and its figures | **13-02** |
| 7 | rolling-origin walk-forward, raw vs smoothed argmax | **13-04**, if 13-03 justifies it |

1. **Per-bar equity/P&L persistence per configuration** (Sections 3 and 6.1) — prerequisite for
   everything else. A time series per configuration, not summary rows.
   > **Landed in 13-01**, as its AC-6. 13-02 consumes the persisted series and does not re-derive
   > the persistence format.
2. Drawdown trigger added to the rule alongside the existing multiple trigger, defaulting off,
   plus the co-firing comparison (Section 2.1).
   > **Landed in 13-01**, as `monetise_drawdown` on `OptionOverlayRule`. 13-02 sweeps it; editing
   > the rule is explicitly on 13-02's DO-NOT-CHANGE list.
3. Cartesian-product sweep over moneyness x drawdown threshold x tenor for the long put, with the
   grid size recorded as the trial count.
   > **SUPERSEDED — tenor is DESCOPED from the product.** Two axes only: moneyness and
   > monetisation drawdown. Tenor stays **fixed at 63 bars**. A third axis multiplies total
   > runtime by the length of its list, and 13-02 must first demonstrate that two axes are
   > affordable and that the two-axis surface says something before a third is paid for. The
   > tenor sweep is a later plan, sized from 13-02's measured total runtime.
4. **Windowed scoring layer**: the indicative set — drawdown windows **and calm windows**
   (Section 4) — with the metrics of Section 6 and the reset-at-window-start peak convention.
   First question asked of it: does one combination sit at the top of both sets?
   > Stands, with Section 6's roles corrected: floored Calmar is the target, the other two are
   > descriptive columns.
5. **CPCV procedure validation** at $N=6, k=2$ (Sections 5.1-5.2): parameters re-selected per
   split, 5 paths, distribution of the pipeline's out-of-sample performance. PBO as the supporting
   statistic. Block-wise rank stability reported alongside but labelled in-sample.
   > **Moved to 13-03 in full.** 13-02 hands it the persisted series, the fixed metric and the
   > trial count, and stops. Per-split selection is by discrete grid argmax (§2.4).
6. Surrogate fitting, staged 1-D to 2-D, with the heatmap, contour and 3-D surface outputs.
   > Stands, in 13-02, and is where the fitting stays: an in-sample display, not a selection rule.
7. Rolling-origin walk-forward: raw argmax against smoothed argmax, plus the argmax-over-time plot.
   > **Not 13-02, and not 13-03 either.** It is the same comparison §2.4 defers, and it belongs
   > with it in a 13-04 plan.

Still carried forward from the ROADMAP and unaddressed here: the `cap = 1.28` sweep and hedge
sizing (notional / overhedge), both of which 13-01's scope limits explicitly deferred.
