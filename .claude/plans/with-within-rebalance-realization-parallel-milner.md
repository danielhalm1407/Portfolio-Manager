# Staged verification runbook — attribution → IBKR read → order submission

> **Canonical copy** mirrors automatically to `.claude/plans/` via the `mirror-plan.py` PostToolUse
> hook. This home copy carries absolute `file:///C:/...` links; the repo copy carries `../../` links.
>
> Supersedes the previous contents of this file (the attribution-consolidation plan), which is
> **complete** and lives at
> [.claude/plans/return-attribution-consolidation.md](../../.claude/plans/return-attribution-consolidation.md).

## Context

The attribution refactor landed and is verified, but only against parquet diffs and assertions —
nothing has been *looked at*. Separately, the IBKR half of this repo
([check_existing_port.py](../../research/check_existing_port.py),
[log_positions.py](../../src/pipelines/log_positions.py),
[rebalance_live.py](../../src/pipelines/rebalance_live.py))
has never been exercised in this session and only runs with TWS open.

Goal: three staged confidence checks, each a precondition for the next — see the attribution
visually, prove the broker *read* path, then prove the broker *write* path. Ordered so that
nothing touches an order until the two read-only stages have passed.

**Decisions taken:** the user runs every TWS-dependent command (all broker connections stay under
their hand); Claude interprets output. Stage 3 goes as far as **live orders on the paper account**,
with an explicit stop for approval on the printed order table before the transmit step.

## Stage 0 — Fix the standalone-HTML theming (code change, do first)

**Diagnosis, confirmed in the source.** There is one root cause, and it is not the grid.

| Path | `paper_bgcolor` / `plot_bgcolor` | Where set |
|---|---|---|
| Dash app | `rgb(17,17,17)` (dark) | [`BaseFigureConfig`, dash_timeseries_app.py:94-96](../../src/portutils/viz/dash_timeseries_app.py#L94-L96) |
| `make_level_figure` flat args | **`rgba(0,0,0,0)` (transparent)** | [dash_timeseries_app.py:1529-1530](../../src/portutils/viz/dash_timeseries_app.py#L1529-L1530) |

Your intuition was right that the Dash app uses `make_level_figure` underneath — but the Dash page
supplies its own dark backdrop: the page `div` is styled `backgroundColor: page_cfg.page_bgcolor`,
default `#111` ([line 1198](../../src/portutils/viz/dash_timeseries_app.py#L1198)).
A transparent figure over a `#111` div looks dark. The same transparent figure written by
`fig.write_html` ([rebalance_study.py:441](../../src/pipelines/rebalance_study.py#L441))
has nothing behind it, so the browser paints its default white. Chrome's dark mode does not repaint
page backgrounds — only its own UI — so it cannot rescue this.

**The grid is not black.** `gridcolor` / `zerolinecolor` are `#526070` (slate, the plotly_dark
value) from `BaseFigureConfig`. Against white they merely *read* as dark. Fixing the background
fixes the perceived grid colour with no change to the grid itself. That is also why your fonts
already look right — `font_color` defaults to `'white'` in the flat args, which is invisible-ish on
white but correct once the background is dark.

### The rule this establishes

The dark palette applies **only where an HTML document is produced** — a running Dash app, or a
figure saved with `write_html`. A figure created in a `.py` cell and shown in the VS Code
interactive window keeps `make_level_figure`'s existing defaults, because a transparent background
there inherits VS Code's own dark theme and already looks right. Baking an opaque background into
the defaults would fix the export case by breaking the inline case.

**`make_level_figure`'s defaults are therefore NOT changed.**

### Changes

**1. New `src/portutils/viz/theme.py` — the single source of truth for colour.**

A tiny constants module with no plotting dependencies, so `panel.py`, the pipelines and the
research scripts can import a hex string without pulling in the whole Dash app. (Putting it inside
`dash_timeseries_app.py` would mean importing a 1,700-line module to learn what "off-white" is.)

```python
INK        = "#e0e0e0"        # primary text: titles, axis titles, tick labels
GRID       = "#526070"        # gridlines, zerolines, ticks — the BaseFigureConfig value
PAPER_BG   = "rgb(17,17,17)"  # outside the plot frame
PLOT_BG    = "rgb(17,17,17)"  # inside the plot frame
PAGE_BG    = "#111"           # the Dash page div behind the figure
MUTED      = "#6e7681"        # de-emphasised legs (the attribution residual)
```

plus the semantic palettes **moved out of the pipeline**, where they should never have lived:
`BOOK_COLOURS`, `TICKER_COLOURS`, `SPLIT_COLOURS`, `RESIDUAL_LABEL` / `RESIDUAL_COLOUR` — currently
[rebalance_study.py:86-101](../../src/pipelines/rebalance_study.py#L86-L101),
which `research/rebalance_realisation.py` reaches into via `study.SPLIT_COLOURS`. Same disease as
attribution: a pipeline owning a definition. `rebalance_study` re-exports them so
`study.TICKER_COLOURS` keeps working and the research script needs no edit.

**2. `apply_export_theme(fig)` in `theme.py`** — one function that stamps `INK` on
title/axis-title/tick fonts, `GRID` on gridlines and zerolines, and the opaque backgrounds, via
`fig.update_layout`. It is applied to a **finished** figure, so it never touches how figures are
built.

**3. Call it at the one place HTML is written** —
[rebalance_study.py:440-441](../../src/pipelines/rebalance_study.py#L440-L441),
the single `for name, fig in figures.items(): fig.write_html(...)` loop:

```python
for name, fig in figures.items():
    theme.apply_export_theme(fig)   # dark palette belongs to the saved document, not the figure
    fig.write_html(OUT_DIR / f"{tag}_{name}.html", include_plotlyjs="cdn")
```

This is why the theme is applied at write time rather than inside the `fig_*` functions: those same
functions serve **both** paths. `research/rebalance_realisation.py` §7 calls `study.fig_attribution`
and displays inline with `fig.show()`; the pipeline's `main()` writes the same figures to HTML.
Theming at export means one implementation serves both with no `theme=` argument threaded through
five function signatures — and `rebalance_realisation.py` needs no change at all.

**4. Rewire `BaseFigureConfig` defaults to the theme constants** (the running-Dash-app path, which
your rule covers). Mostly identical values — but note two deliberate changes:
`font_color` `#f2f5fa` → `INK` (`#e0e0e0`), and the `"white"` title/axis-title/tick colours at
[lines 115-118](../../src/portutils/viz/dash_timeseries_app.py#L115-L118)
→ `INK`. So Dash-rendered text goes very slightly softer. Flagging because it is a visible change
to something that currently works.

### Explicitly not changed

- `make_level_figure`'s `paper_bgcolor` / `plot_bgcolor` / `font_color` defaults — per your rule.
- `panel.py`'s `SECTOR_COLORS` and its scattered `#ffffff` / `#e0e0e0` / `'white'` literals — out of
  scope for this pass, left as known debt.
- The grid colour itself. `#526070` was never the problem; it only *read* as black against white.

*Verification:* re-run `python src/pipelines/rebalance_study.py`, open a regenerated HTML in Chrome
— dark background, `#e0e0e0` text, legible `#526070` grid. Then run `research/rebalance_realisation.py`
§4 and §7 in the interactive window and confirm those are **unchanged** (transparent, inheriting the
VS Code theme). Both behaviours from one code path is the thing being verified.

## Stage 1 — Return attribution, visually (no TWS)

The current figures were generated at 11:51 today, **before** the Stage 0 fix, so they still have
the white background. Re-run `python src/pipelines/rebalance_study.py` after Stage 0 and review the
regenerated files.

**Open these three first** — the charts the refactor changed:
`spy_kmlm_attribution_drift.html`, `..._mix.html`, `..._mix_overlay.html`

What to check, in order:

1. **The stack closes on the total.** Coloured legs (SPY, KMLM, `cash / costs`) must sum to the
   white "total" line at every point. This is the property that did *not* hold before the refactor
   and is the single most important thing to eyeball.
2. **The total line matches the equity curve.** Open `spy_kmlm_equity.html` alongside. The
   attribution total is now the book's own base-100 equity change, so the shapes must agree —
   drift ending at +13.77, mix at +14.20, mix+overlay at +14.05.
3. **`cash / costs` is ~flat at zero** in all three, because the pipeline's default run has zero
   slippage. That leg going non-zero at default settings would mean something is wrong.
4. **The weight paths differ visibly**: `drift`'s legs wander, `mix`'s stay pinned. That difference
   is the entire argument of the study.

Then, for the constant-weight strawman side by side, run
[research/rebalance_realisation.py](../../research/rebalance_realisation.py)
cell by cell in the interactive window. **§3–4** is the `hedge_sleeves.py` model (constant weights,
no residual); **§7** is the same chart on actual weights. Both now run through
`performance.ReturnAttribution`, so they are finally on one convention and directly comparable —
§7's extra `cash / costs` leg is the only structural difference.

*Gate before stage 2:* the stack closes and the total tracks equity in all three books.

## Stage 2 — Prove the IBKR read path (TWS required, read-only)

Preconditions: TWS or IB Gateway running on `127.0.0.1:7497`, API access enabled, paper account
`DUP102412` logged in.

Run in this order. **Client ids are already distinct per script** (131 / 141 / 151) so they can
coexist, but do not run two scripts sharing an id.

**2a. Position snapshot** — the cheapest possible connection test, and independently worth doing
every day:

```bash
python src/pipelines/log_positions.py --account DUP102412 --client-id 131
```

Expect: one row per holding written to `data/raw/ibkr/positions_<today>.parquet` plus the
accumulated `positions_history.parquet`. An empty account writes a dated row with no holdings —
that is correct behaviour, not a failure. Re-running the same day replaces only that date's rows.

Per [log_positions.md](../../src/pipelines/log_positions.md):
every day this does not run is a day of history that cannot be recovered later. Worth scheduling
after this stage passes.

**2b. Account P&L breakdown** — the real analysis, cell by cell in the interactive window:

[research/check_existing_port.py](../../research/check_existing_port.py),
settings at §2: `ACCOUNT = "DUP102412"`, `CLIENT_ID = 141`, `EXEC_DAYS_BACK = 7`,
`BACKCAST_POLICY = backcast.BUY_AND_HOLD`.

What to read, and in what spirit:

- The script prints its **verdict before its numbers**, deliberately, so you see whether to believe
  the chart before you see the chart.
- **An empty executions result means "no fills in the ~7 days TWS serves", never "no trades ever".**
  Raising `EXEC_DAYS_BACK` does not defeat that ceiling; it is a TWS limitation.
- The **avgCost falsification check** is a consistency test. It can refute the backcast assumption
  confidently; it can only weakly support it. If it fails, the backcast policy is wrong for this
  account, not the code.

*Gate before stage 3:* both scripts connect, positions come back matching what TWS shows on screen,
and the backcast either validates or fails for an understood reason.

## Stage 3 — Order submission (TWS required, writes)

Current config in
[config/settings.yaml](../../config/settings.yaml)
is already at the safe end of every switch — `enabled: false`, `account: DUP102412`,
`require_paper: true`, `max_order_value: 50000`. **No config edit is needed for either step below**,
and none should be made: `--live` on the command line is the deliberate per-run authorization,
whereas `enabled: true` would make every future run transmit silently.

**3a. Dry run** (transmits nothing):

```bash
python src/pipelines/rebalance_live.py --dry-run
```

This exercises the entire path except the send: connect → account → marks → the *same*
`ConstantMixRule` used in the research → signed deltas → whole-share truncation → min-turnover gate
→ max-order-value guard → printed table → `outputs/live/orders_<timestamp>.csv`.

Check on the printed table:
- current weight vs target weight per leg, and that targets match `spy_kmlm` in
  `config/asset_universe.yaml` — the live policy cannot silently drift from the studied one;
- `units_diff` signs point the way you expect (contrarian: sell what rallied);
- no leg is skipped for a missing `marketPrice` (a skipped leg is correct behaviour but means an
  incomplete rebalance);
- no order near the 50,000 ceiling — that guard **rejects**, it does not clip.

**3b. Live on paper — stop here for explicit approval.** Do not run 3b until the 3a table has been
read and approved. Then:

```bash
python src/pipelines/rebalance_live.py --live
```

`require_paper: true` refuses any account id without IB's `DU` prefix, so a real account cannot be
hit by accident; that would additionally need `--i-know-this-is-real`, which is not part of this
plan and should not be passed.

After the run: confirm fills in TWS, then re-run **2a** (`log_positions.py`) to capture the
post-trade state — that is what makes today's trades reconstructible later.

### Hazards to respect

- **Never `import` [orders/rebalance_port_basic.py](../../orders/rebalance_port_basic.py).**
  It is a cell script with module-level side effects: importing it opens a connection and
  transmits real orders *before any guard runs, even on a dry run*. Pinned by
  `test_does_not_import_the_cell_script`.
- **Market orders, no limit protection** — do not run 3b into an illiquid open.
- **Whole shares, truncated** (not rounded), so the book lands slightly under target rather than
  overshooting.

## Verification summary

| Stage | Command | Pass condition |
|---|---|---|
| 0 | `python src/pipelines/rebalance_study.py`, open an HTML in Chrome | dark bg, `#e0e0e0` text, legible `#526070` grid |
| 0 | `research/rebalance_realisation.py` §4 and §7 inline | **unchanged** — transparent, inherits VS Code theme |
| 1 | regenerated figures in `outputs/scenarios/` | legs sum to total; total tracks equity; `cash / costs` ≈ 0 |
| 1 | `research/rebalance_realisation.py` cells | §3–4 and §7 comparable; only §7 has a residual leg |
| 2a | `log_positions.py --account DUP102412 --client-id 131` | parquet written; holdings match TWS |
| 2b | `research/check_existing_port.py` cells | verdict prints first; avgCost check understood |
| 3a | `rebalance_live.py --dry-run` | table sane; CSV written; nothing transmitted |
| 3b | `rebalance_live.py --live` | **only after 3a approved**; fills visible in TWS |
| post | `log_positions.py` again | post-trade state recorded |

## Progress Log

| Step | Status | Notes |
|------|--------|-------|
| 0a. New `viz/theme.py`; move palettes out of the pipeline | **done** | re-exported; `study.TICKER_COLOURS` still resolves |
| 0b. `apply_export_theme` at the `write_html` loop | **done** | export only; inline confirmed untouched |
| 0c. Rewire `BaseFigureConfig` to theme constants | **done** | Dash text now `#e0e0e0` |
| 0d. Regenerate figures | **done** | 10 HTML rewritten; 52 tests pass |
| 1. Attribution figures reviewed | pending | after 0b, not the 11:51 set |
| 1b. rebalance_realisation §3–4 vs §7 | pending | interactive cells |
| 2a. log_positions.py | pending | needs TWS |
| 2b. check_existing_port.py | pending | needs TWS |
| 3a. rebalance_live --dry-run | pending | transmits nothing |
| 3b. rebalance_live --live | **blocked** | requires explicit approval of the 3a table |
| post. log_positions.py re-run | pending | captures post-trade state |

## Decisions

- **Read before write.** Stages 2 and 3 are ordered so the broker read path is proven before
  anything can place an order.
- **User runs all TWS commands**, Claude interprets. Every broker connection stays under their hand.
- **No config edit for going live.** `--live` per run rather than `enabled: true`, which would
  remove the "did you mean it" step from every future run.
- **3b is an explicit gate, not a step.** The printed order table gets read and approved first.
- **The dark palette belongs to the HTML document, not to the figure.** Applied at export
  (`write_html`) and in the Dash app; never baked into `make_level_figure`'s defaults. A figure
  shown in the interactive window keeps a transparent background so it inherits the VS Code theme.
  This is what lets one set of `fig_*` functions serve both the pipeline and the research scripts.
- **Theme at the write loop, not inside the `fig_*` functions.** Those functions are shared between
  the export path and the inline path; theming inside them would force a `theme=` argument through
  five signatures and change what `rebalance_realisation.py` sees.
- **A dedicated `viz/theme.py`, not constants inside `dash_timeseries_app.py`.** Otherwise reading
  one hex string means importing 1,700 lines of Dash machinery.
- **Semantic palettes move out of `rebalance_study.py`.** A pipeline should not own the definition
  of what colour a ticker is — `research/rebalance_realisation.py` currently imports them from
  there. Re-exported so no caller breaks.
- **Grid colour left at `#526070`.** It was never the problem; it only *read* as black against a
  white background.
