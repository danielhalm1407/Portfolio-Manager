# Move return attribution and return/weight definitions out of `viz/` into `analysis/`

> **Canonical copy.** A mirror with absolute `file:///` links lives at
> `~/.claude/plans/with-within-rebalance-realization-parallel-milner.md` because Claude Code pins
> its plan-approval banner there and that path cannot be redirected. Edit this file; re-mirror.

## Context

Return attribution is implemented **twice**, in two modules that have nothing to do with each
other, and the two implementations disagree:

- [`PanelBuilder.attribution`](../../src/portutils/viz/panel.py#L256-L302)
  — lives in a **plotting** module. Does `contrib = weights * rets`, groups by `sector_map`,
  scales each bar by `portfolio_prev` (V_{t-1}) to get a base-100 wealth index, so the stacked
  legs sum *exactly* to `portfolio_series_change`. Cannot express a weight shift, cannot take an
  externally-supplied equity path, cannot show a residual.
- [`fig_attribution`](../../src/pipelines/rebalance_study.py#L252-L277)
  — lives in a **pipeline** module, with the attribution maths inlined in a figure-building
  function. Shifts weights by one bar (correctly — the simulator's weights are post-trade),
  but `cumsum`s **raw percentage** contributions with no V_{t-1} scaling, so its stacked legs
  sum to the arithmetic sum of daily contributions rather than the book's compounded return.
  No sector grouping.

Neither location is right. Attribution is a **performance** calculation, so it belongs in
[src/portutils/analysis/performance.py](../../src/portutils/analysis/performance.py)
alongside `PerformanceSummary`, `PerformanceCompare` and `ReturnsToLevels`. `panel.py` should
plot; `rebalance_study.py` should orchestrate. Both should *call* the calculation, not own it.

Second, structural gap in the pipeline path: for a real book,
`sum_i w_{i,t-1} r_{i,t}` does **not** equal the book's own return. Slippage, uninvested cash and
intra-bar trading sit outside the weights×returns identity. Today that gap is silently absorbed
into the total line. It should be an explicit, visible bucket.

The same fault runs wider than attribution. `panel.py` — a plotting module — also owns the
primary definition of `_normalise`, `_pct_returns`, `_daily_returns`, `_log_returns` and
`simulate_weights`. Those are performance/returns definitions that other modules have to reach
into a `PanelBuilder` to get. They move too (§1b, §1c), with `panel.py` left calling them.

Intended outcome: one attribution implementation, in `performance.py`, whose capability set is
the **union** of what the two current versions can do; the return and weight definitions living
in `analysis/`, not `viz/`; and every existing call site working unchanged, with the two charts
on the same base-100 footing.

## Answers to the questions raised while planning

Recorded here because they drive the design.

**`contrib.mul(wealth_prev, axis=0)`** — `contrib` is a `pd.DataFrame`, T×N (index = bar dates,
columns = tickers). `wealth_prev` is a `pd.Series` of length T on the same date index. The result
is another T×N DataFrame.

- For `DataFrame * Series`, pandas' default broadcast aligns the Series index against the
  DataFrame's **columns**. `wealth_prev` is indexed by dates, not tickers, so `contrib * wealth_prev`
  matches dates to ticker names, finds no overlap, and returns an all-NaN frame over the union of
  both label sets. `.mul(s, axis=0)` overrides that: align `s` on the DataFrame's **index**,
  broadcast the row's scalar across every column. `.mul` exists because the `*` operator has
  nowhere to put an `axis` argument.
- **`axis=0` here vs `axis=1` in panel.py**: different verbs sharing a keyword. Every `axis=1` in
  panel.py is `.sum(axis=1)` — a *reduction direction*, collapsing N columns down to a per-bar
  Series. `axis=0` in `.mul` is an *alignment axis*.
- **Why panel.py never needed `.mul`**: it scales inside the per-sector loop at
  [panel.py:295](../../src/portutils/viz/panel.py#L295),
  where `contrib[tickers].sum(axis=1)` is already a Series. `Series × Series` on a shared date
  index aligns automatically, no ambiguity. The consolidated version keeps the full N columns so
  the per-ticker legs survive, which is exactly when the ambiguity appears.

## Approach

### 1. New `ReturnAttribution` class in `performance.py`

Matches the module's existing class-based style (`__init__` stores config, a `run()`-style method
returns the result). Capability union:

| Capability | From | Control |
|---|---|---|
| Arbitrary time-varying T×N weights | both | required arg |
| Constant weights | rebalance_realisation §3 | caller broadcasts a scalar dict, as today |
| Per-column daily + cumulative contribution | both | always produced (`col_contrib`, `cum_col`) |
| Group columns into sectors/strategies | panel | `group_map=None` (default) → grouped fields are `None` |
| Shift weights one bar (post-trade → beginning-of-bar) | rebalance_study | `shift_weights: bool = False` |
| Base-100 wealth-index scaling by V_{t-1} | panel | `scale: "wealth" \| "raw"`, default `"wealth"` |
| Wealth path derived from contributions | panel | default when `equity=None` |
| Wealth path taken from an external equity series | **new** | `equity: pd.Series \| None` |
| Explicit cash/costs residual bucket | **new** | `residual_label: str \| None = None` |

Core maths, in one place:

```
rets        = returns frame (T×N), caller-supplied or from prices.pct_change()
w           = weights.shift(1) if shift_weights else weights
col_contrib = w * rets                               # T×N, elementwise — always computed
grouped     = per-group sum(axis=1) over group_map   # only if group_map is not None

# Total: from the grouped frame when grouping is on, so a group_map that deliberately
# omits columns still totals to what its groups actually cover — this is panel.py's
# existing behaviour and the baseline test in Verification 1 depends on it.
portfolio_daily = (grouped if group_map else col_contrib).sum(axis=1)

wealth      = (1 + portfolio_daily).cumprod() * 100       if equity is None
              equity / equity.iloc[0] * 100                otherwise
prev        = wealth.shift(1).fillna(100.0)          # V_{t-1}
cum_col     = col_contrib.mul(prev, axis=0).cumsum() # scale into portfolio-value units
cum_grouped = grouped.mul(prev, axis=0).cumsum()     # only if group_map is not None
```

When `equity` is supplied, the book's own bar return is `r^p = wealth / wealth.shift(1) - 1`, and
the residual `prev * (r^p - grouped.sum(axis=1))` is cumsummed into a `residual_label` column.
appended to `cum_col` (and to `cum_grouped` when grouping is on).
With it present, `cum_col.sum(axis=1) == wealth - 100` holds to float tolerance — the same
closure property panel.py already has, now extended to books with cash and costs.

### 1a. Return type — `NamedTuple`

`run()` returns a `typing.NamedTuple` (`AttributionResult`) with fields, in the order that
matches panel.py's current 5-tuple so positional unpacking is preserved:

```python
class AttributionResult(NamedTuple):
    grouped_contrib: Optional[pd.DataFrame]  # per-GROUP daily contribution  (T x G) [= sector_contrib]
    portfolio_daily: pd.Series               # total daily contribution      (T,)
    cum_grouped:     Optional[pd.DataFrame]  # per-GROUP cumulative, portfolio-value units (T x G) [= sector_cum]
    wealth_change:   pd.Series               # wealth index minus 100        (T,)
    wealth:          pd.Series               # base-100 wealth index         (T,)
    col_contrib:     pd.DataFrame            # per-COLUMN daily contribution (T x N)
    cum_col:         pd.DataFrame            # per-COLUMN cumulative, portfolio-value units (T x N)
```

**Grouping is optional; per-column is always produced.** `col_contrib` and `cum_col` are computed
on every call — they are the base quantity, and the grouped frames are just a partition-sum of
them. `grouped_contrib` and `cum_grouped` are `None` unless a `group_map` is supplied. So a caller
that does not care about sectors (the `fig_attribution` case, one leg per ticker) never has to
invent the `{t: [t] for t in weights}` singleton-list trick that
[rebalance_realisation.py:84](../../research/rebalance_realisation.py#L84)
and hedge_sleeves.py both currently need — it just reads `cum_col`.

Field **order** is fixed by back-compat: the first five are exactly panel.py's current 5-tuple, so
its positional unpack keeps working. The two per-column fields are appended.

Rationale, since this was explicitly weighed:

- **Not a single DataFrame.** The outputs have three different shapes — T×N (per ticker), T×G
  (per group), T×1 (portfolio series). One frame forces either prefixed column names you then
  string-match to split apart, or a MultiIndex column level. Both are worse than the status quo.
- **Not a dict.** Named access, but no autocompletion, no type checking, and a typo'd key fails at
  runtime instead of at edit time.
- **A NamedTuple gives named access** (`res.cum_col`) without forcing every caller to remember
  field order. **Correction, found during implementation:** the plan originally claimed a 7-field
  NamedTuple would still satisfy the existing 5-way unpack. It does not — Python tuple unpacking
  requires EXACT arity, so `a, b, c, d, e = <7-tuple>` raises `ValueError: too many values to
  unpack`. Back-compat is therefore handled in the shim instead: `panel.attribution` slices the
  result down to the legacy five (see §2). New code calls `ReturnAttribution` directly and reads
  by name.
- `col_contrib` / `cum_col` are last because they are new — appending cannot disturb the existing
  5-way unpack.

### 1b. Move the wide-matrix return helpers out of `panel.py` into `returns.py`

Same disease as attribution: `panel.py` is a **plotting** module that currently owns the primary
definition of what a return *is*. Four helpers move to
[src/portutils/analysis/returns.py](../../src/portutils/analysis/returns.py)
as **module-level functions** (not methods — they take a frame and return a frame; the `self`
was never used for anything but the `df=None` default):

| Now | Becomes | Body |
|---|---|---|
| [`_normalise`](../../src/portutils/viz/panel.py#L173) | `normalise(df, base=100)` | `(df / df.iloc[0]) * base` |
| [`_pct_returns`](../../src/portutils/viz/panel.py#L182) | `pct_returns(df)` | `((df / df.iloc[0]) - 1) * 100` |
| [`_daily_returns`](../../src/portutils/viz/panel.py#L187) | `daily_returns(df)` | `df.pct_change().dropna()` |
| [`_log_returns`](../../src/portutils/viz/panel.py#L192) | `log_returns(df)` | `np.log(df / df.shift(1)).dropna()` |

`ReturnsCalculator` / `ReturnsConfig` in that file are **not touched**. They solve a different
problem — suffix-named columns in one frame (`spx_level` → `spx_period_return`), name-driven — vs
these, which are wide T×N matrix → wide T×N matrix with no naming convention. Both belong in a
module called `returns`; neither subsumes the other. A short comment at the top of the new section
says exactly this, so the next reader does not try to merge them.

**`panel.py`'s methods stay as one-line delegating wrappers**, preserving the `df=None →
self.df_all` default:

```python
def _daily_returns(self, df=None):
    # Definition lives in analysis/returns.py — panel.py plots, it does not define what a
    # return is. This wrapper only supplies the df_all default.
    return returns.daily_returns(self.df_all if df is None else df)
```

That keeps every internal call site working with no edit — [panel.py:109](../../src/portutils/viz/panel.py#L109),
[113](../../src/portutils/viz/panel.py#L113),
[346](../../src/portutils/viz/panel.py#L346),
[396](../../src/portutils/viz/panel.py#L396),
[401](../../src/portutils/viz/panel.py#L401),
[536](../../src/portutils/viz/panel.py#L536),
[877](../../src/portutils/viz/panel.py#L877),
[882](../../src/portutils/viz/panel.py#L882)
— plus `hedge_sleeves.py`, whose comment at
[line 203](../../research/hedge_sleeves/hedge_sleeves.py#L203)
names `PanelBuilder._daily_returns` explicitly. That comment gets updated to point at the new
home, not deleted.

`ReturnAttribution` imports from `returns.py` directly, so `performance.py` never duplicates the
maths either.

**Explicitly out of scope**: the loading (`_load`), colour/theme constants and figure-building
machinery in `panel.py` stay put. That is acknowledged technical debt, deferred on purpose — this
plan moves only the return/weight definitions.

### 1c. Move `simulate_weights` to `performance.py`

[`simulate_weights`](../../src/portutils/viz/panel.py#L199)
becomes a module-level `simulate_weights(rets, rebal_freq="QE")` in `performance.py`, next to
`ReturnAttribution` — it generates the weight path that attribution consumes. Signature change:
it takes a **returns frame** rather than reaching for `self._daily_returns()`, which is what makes
it usable outside a `PanelBuilder`.

`panel.py` keeps `def simulate_weights(self, rebal_freq='QE')` as a wrapper calling
`performance.simulate_weights(self._daily_returns(), rebal_freq)`, so
`panel.simulate_weights(rebal_freq='QE')` in `notebooks/exploration/ff.ipynb` is unaffected.

All of its existing comments — the `equal_w` dimension note, the pandas `'Q'`/`'QE'` version
caveat, the multi-line `rebal_dates` walkthrough, the elementwise-vs-matrix-multiplication note on
the drift line — **move verbatim** with the code.

### 2. `panel.attribution` becomes a thin shim

Body becomes: build `rets` via `self._daily_returns()`, delegate to `ReturnAttribution`, and
return **the legacy five fields as a tuple** — `(grouped_contrib, portfolio_daily, cum_grouped,
wealth_change, wealth)`, which are exactly the historical
`(sector_contrib, portfolio_daily, sector_cum, portfolio_series_change, portfolio_series)`.
Returning the full 7-field result would break every caller (exact-arity unpacking). This keeps
[hedge_sleeves.py:249](../../research/hedge_sleeves/hedge_sleeves.py#L249),
[rebalance_realisation.py:87](../../research/rebalance_realisation.py#L87)
and `notebooks/exploration/ff.ipynb` working untouched. Its docstring gains a line pointing at
`performance.ReturnAttribution` as the real implementation.


### 3. `fig_attribution` becomes a caller

[rebalance_study.py:252-277](../../src/pipelines/rebalance_study.py#L252-L277)
drops its inline maths and calls `ReturnAttribution` with `shift_weights=True`,
`equity=df["equity"]`, `residual_label="cash / costs"`, **no `group_map`** — it plots one leg per
ticker, so it reads `res.cum_col` and drops the singleton-list workaround entirely. `actual_weights` stays where it is — it
reads simulator-specific column names (`f"{t}_mark_value"`), so it is pipeline glue, not a
performance primitive.

Figure wiring: extend `cols_of_interest` to include the residual column, add a neutral grey to
`colour_map` distinct from
[`TICKER_COLOURS`](../../src/pipelines/rebalance_study.py#L82),
and a `label_map` entry. `stack_split_sign` already handles negative legs, so a negative cost
bucket stacks below the axis with no further work.

### 4. Comments

Per project convention: a header block on `ReturnAttribution` covering its role, the two
conventions it now unifies, and when each option applies; a rationale comment above each
meaningful step, specifically why `.mul(..., axis=0)` and not `*` (the reasoning above, condensed).
Existing comments at all three sites are **retained verbatim** and travel with the code they
describe — including the look-ahead note on the `w.shift(1)` line in `fig_attribution` (moves to
the `shift_weights` branch) and the `portfolio_prev` / units explanation in panel.py (moves to the
scaling step). Where the same rationale exists in both files, combine into one comment at the new
home rather than dropping either.

## Files

- [src/portutils/analysis/performance.py](../../src/portutils/analysis/performance.py) — **new** `ReturnAttribution` + `simulate_weights`.
- [src/portutils/analysis/returns.py](../../src/portutils/analysis/returns.py) — **new** wide-matrix `normalise` / `pct_returns` / `daily_returns` / `log_returns`. `ReturnsCalculator` untouched.
- [src/portutils/viz/panel.py](../../src/portutils/viz/panel.py) — `attribution`, the four return helpers and `simulate_weights` all reduced to delegating wrappers. Loading/theming/figure code untouched.
- [src/pipelines/rebalance_study.py](../../src/pipelines/rebalance_study.py) — `fig_attribution` calls the class; residual leg wired into the figure.
- [research/rebalance_realisation.py](../../research/rebalance_realisation.py) — §7 narrative comment updated (chart now base-100 and ties to book equity, so it is directly comparable to the §3-4 strawman). No API change needed at §3.
- [research/hedge_sleeves/hedge_sleeves.py](../../research/hedge_sleeves/hedge_sleeves.py) — comment-only: [line 203](../../research/hedge_sleeves/hedge_sleeves.py#L203) names `PanelBuilder._daily_returns`, repointed to the new home. Code unchanged.
- Unchanged: `notebooks/exploration/ff.ipynb`.

## Verification

1. **Shim non-regression (do this first).** Before touching anything, run
   `research/rebalance_realisation.py` §3-4 and dump `sector_cum` / `portfolio_series` to a
   parquet in the scratchpad. After the refactor, re-run and assert
   `abs(new - old).max().max() < 1e-10`. Same for `hedge_sleeves.py`'s `sector_cum`. This is the
   test that the consolidation changed no existing numbers.
2. Run the pipeline end to end:
   `~/miniconda3/envs/venv-stats/python.exe src/pipelines/rebalance_study.py` (reads the cached
   parquet from `cache_prices.py`; universe and weights from `config/asset_universe.yaml`).
3. **Closure check** — for each book, assert
   `abs(cum.sum(axis=1) - (wealth - 100)).max() < 1e-8`. Currently false in the pipeline path;
   this is the point of the change.
4. **Tie-out check** — `wealth.iloc[-1] - 100` equals
   `df["equity"].iloc[-1] / df["equity"].iloc[0] * 100 - 100` per book.
5. **Zero-cost sanity** — `slippage_sweep` at `k=0.0`: the `hold` book's residual leg should be
   ~0 across the whole path (fully-deployed buy-and-hold has no drag), validating that the
   residual measures what it claims.
6. **Broadcast-bug guard** — a unit test asserting `ReturnAttribution` output contains no NaNs and
   keeps exactly the input columns. This is the failure mode `contrib * wealth_prev` would produce
   and it fails silently in a chart.
7. **Helper-migration equivalence** — for the cached price frame, assert the four moved helpers
   return frames identical to the pre-change `PanelBuilder` methods (same `< 1e-12` diff, same
   index and columns, `_daily_returns`/`_log_returns` still dropping the first row). Then check
   `PanelBuilder.__init__` still populates `df_norm` and `df_ret`
   ([panel.py:109](../../src/portutils/viz/panel.py#L109),
   [113](../../src/portutils/viz/panel.py#L113))
   and that a plotting call exercising each remaining wrapper site still renders — the `normalise`
   path at 346/396/401/877/882 and the returns path at 536.
8. **`simulate_weights` equivalence** — same weights frame before and after for `rebal_freq='QE'`
   and `'ME'`, including the calendar snap-back dates; run the ff.ipynb cell that calls it.
9. `~/miniconda3/envs/venv-stats/python.exe -m pytest tests/` — the existing suite must stay green.
10. Eyeball the three §7 figures: `mix` legs pinned near target, `drift` legs wandering, total line
   now overlaying the equity curve from the existing equity figure.

## Progress Log

| Step | Status | Notes |
|------|--------|-------|
| 0. Mirror plan to repo `.claude/plans/`, relative links | done | |
| 1. Capture pre-change baselines (verification 1, 7, 8) | done | must precede any edit |
| 2. Wide-matrix helpers → `returns.py`; panel wrappers | done | |
| 3. `simulate_weights` → `performance.py`; panel wrapper | done | takes `rets`, not `self` |
| 4. `ReturnAttribution` in `performance.py` | done | imports helpers from `returns.py` |
| 5. `panel.attribution` → shim, 5-tuple preserved | done | |
| 6. `fig_attribution` → caller + residual leg | done | |
| 7. Comments merged and retained | done | |
| 8. Verification 1-10 | done | see results below |

## Decisions

- **Home is `performance.py`, not `panel.py` or `rebalance_study.py`.** It is a performance
  calculation; `panel.py` plots and `rebalance_study.py` orchestrates.
- **`panel.attribution` kept as a shim rather than deleted.** Three call sites including a
  notebook depend on the 5-tuple; a shim makes the consolidation a no-op for them and keeps the
  baseline test meaningful.
- **Per-column output is the base case, grouping is the opt-in.** Grouped frames are a
  partition-sum of the per-column ones, so computing per-column always costs nothing and removes
  the `{t: [t] for t in weights}` singleton-list workaround from two call sites.
- **`NamedTuple` over dict or single DataFrame.** Only option that gives named access *and*
  keeps the existing positional unpack working; the outputs' shapes differ too much for one frame.
- **Plan file location is not configurable.** Claude Code's plan-mode banner is pinned to
  `~/.claude/plans/`; the repo copy stays canonical and the home copy is a mirror with absolute
  `file:///C:/...` links (uppercase drive — lowercase does not resolve in the viewer).
- **panel.py's V_{t-1} convention wins** over the pipeline's raw cumsum. It already has the
  closure property, and two existing charts depend on its output.
- **`panel.py` should not define what a return is.** The four wide-matrix helpers move to
  `returns.py`; panel keeps delegating wrappers so its ~8 internal call sites and the notebook are
  untouched. `simulate_weights` moves to `performance.py` next to `ReturnAttribution`, taking a
  returns frame so it works outside a `PanelBuilder`.
- **`returns.py` over `performance.py` for the wide helpers.** That module is named for exactly
  this. `ReturnsCalculator` is left alone — different contract (suffix-named columns, not a wide
  matrix); neither subsumes the other, so both live there with a comment saying why.
- **Rest of `panel.py` is deferred debt, on purpose.** Loading, theming and figure construction
  stay; only the return/weight *definitions* move in this plan.
- **`actual_weights` stays put.** It reads simulator-specific column names (`f"{t}_mark_value"`),
  so it is pipeline glue, not a performance primitive.
- **Residual shown, not absorbed.** Folding cash/costs into the total keeps the chart tidy at the
  cost of the argument the study is making.


## Outcome (2026-07-26)

All steps landed; every check in the Verification section ran.

- **Verification 1 / 7 / 8 — no existing number moved.** A before/after snapshot of 19 frames
  (the four wide helpers, `df_norm`, `df_ret`, `simulate_weights` at QE and ME, the full
  `panel.attribution` 5-tuple, and each book's weight path and equity) compares **19/19 identical,
  maxdiff 0.000e+00**. Scripts in the session scratchpad: `capture_baseline.py`,
  `compare_baseline.py`.
- **Verification 3 — closure.** Legs incl. residual sum to `wealth_change` on every bar, for all
  three books at `slippage_k` 0.0 and 0.001: max diff `1.8e-13`. This property did not hold before.
- **Verification 4 — tie-out.** Final `wealth_change` equals the book's own equity change exactly
  (drift +13.767021, mix +14.200486, mix+overlay +14.046805 at k=0).
- **Verification 5 — zero-cost sanity.** At `k=0` the drift book's residual is `max|r| = 9.5e-14`,
  i.e. zero. With `k=0.001` the traded books go negative — mix `-0.1105`, mix+overlay `-0.1504`
  points of base-100 — which is the rebalance being paid for, previously invisible.
- **Verification 6 — broadcast guard.** No NaNs; columns are exactly the input tickers plus the
  residual. Also asserted: grouped output equals the per-column sum for a partition
  (`8.9e-15`), ungrouped runs leave the grouped fields `None`, and `residual_label` without
  `equity` raises rather than silently defaulting.
- **Verification 2 / 10 — pipeline.** `python src/pipelines/rebalance_study.py` runs clean and
  writes 10 HTML figures + CSVs to `outputs/scenarios`.
- **Verification 9 — suite.** `pytest tests/` → **52 passed**.

### Deviation from plan

The NamedTuple back-compat claim was wrong (see the corrected bullet in §1a). `panel.attribution`
slices to the legacy 5-tuple rather than returning the result whole. No other deviation.
