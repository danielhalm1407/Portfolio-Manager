# `viz/` — figures that look like one system

Plotting helpers shared by notebooks, the Dash app and exported reports.

| module | what |
|---|---|
| [`theme.py`](theme.py) | **the single source of truth for colour.** `ticker_colour_map()`, `apply_export_theme()` |
| [`panel.py`](panel.py) | `PanelBuilder` — data-loading plus Plotly theming for portfolio notebooks. Includes various different plots that one can call on a dataframe of returns. |
| [`dash_timeseries_app.py`](dash_timeseries_app.py) | configurable time-series and histogram apps: `TimeSeriesAppConfig`, `HistogramAppConfig`, `ColourGroupConfig`, `GradientColourMap`, `AutoLabelMap` |

Only `PanelBuilder` is re-exported from `portutils.viz` (see [`__init__.py`](__init__.py)); everything
else is imported from its module, e.g. `from portutils.viz.dash_timeseries_app import make_level_figure`.

## Which figure do I use? (quick chooser)

| I want to… | use | module |
|---|---|---|
| put several IBKR price series **next to each other, re-based to 100**, in an interactive window | `make_level_figure(df, [...], reindex=...)` | `dash_timeseries_app` |
| same, but with a **draggable date-range slider** in a browser tab | `LevelDashApp(df, cfg).run()` | `dash_timeseries_app` |
| see **how far back each series goes** (ragged start dates, no truncation) | `make_level_figure` with `reindex="common"` or `"self"` on a wide frame — see [Series with different start dates](#series-with-different-start-dates) | `dash_timeseries_app` |
| look at the **distribution** of returns (histogram + KDE + quartile lines) | `make_distrib_figure(returns_series)` | `dash_timeseries_app` |
| the sector-grid / PCA / correlation / animation plots on the fixed ETF book | `PanelBuilder` | `panel` |
| the same colour for a ticker in every figure | `ticker_colour_map(symbols)` | `theme` |
| save a figure to HTML in the dark house style | `apply_export_theme(fig)` | `theme` |

## `dash_timeseries_app.py` — method by method

This is the module to reach for when comparing IBKR series. Two entry points build the same figure:
`make_level_figure` (a plain Plotly `go.Figure` — call `.show()`, works in the VS Code interactive
window, no server) and `LevelDashApp` (a Dash server with a range slider). Both funnel into the private
`_build_level_figure`, so re-basing behaves identically in each.

### Input the level figure needs

* a DataFrame with a **`time` column of dtype datetime64** (checked by `_validate_inputs`; it raises
  `ValueError` otherwise), at least 2 rows, and every name in `cols_of_interest` present as a column;
* a wide price frame indexed by date (as in the `data/processed/prices_<tag>.parquet` cache written by
  `pipelines/cache_prices.py`) therefore needs `df.rename_axis("time").reset_index()` first;
* the x-axis is the DataFrame's **index**, with tick text taken from the `time` column, so the default
  integer index is fine for plotting. It is **not** fine for a `pd.Timestamp` anchor — see below.

### Public functions and classes

| name | what it does |
|---|---|
| `make_level_figure(df, cols_of_interest=None, cfg=None, …, reindex=True, time_window=None)` | Builds the multi-series line figure and returns a `go.Figure`. Three calling styles: flat keyword args (every kwarg mirrors a `TimeSeriesAppConfig` field), `cfg=TimeSeriesAppConfig(...)`, or the config as the second positional argument. `time_window` pre-filters to `(start, end)` datetimes or `(int, int)` row positions. Also supports `stack_mode` (`"none"`, `"stack"`, `"stack_split_sign"`), an optional overall/composite line, and native Plotly stack-mode buttons. |
| `make_distrib_figure(data, *, cfg=None, base_cfg=None, type_cfg=None, **overrides)` | Histogram + KDE with optional vertical lines for min / q1 / median / mean / q3 / max. `data` is anything `np.asarray(..., dtype=float)` can flatten (a returns Series is the intended input). |
| `LevelDashApp(df, config)` or `LevelDashApp(sections=[SectionConfig, …])` | Stateful Dash builder. `.build()` returns the `dash.Dash` instance, `.run(debug=, port=, use_reloader=)` blocks and serves (default `http://localhost:8050`). Multi-section mode stacks several figures on one page, each with its own heading, optional commentary, slider and callback; component ids are auto-suffixed so sections cannot collide. Requires `dash` (imported lazily — `make_level_figure` works without it). |
| `SectionConfig(df, cfg, commentary=None, show_slider=True, time_window=None)` | One panel of a multi-section page. `time_window` also sets the slider's initial position. |
| `AutoLabelMap(cols).run()` | Prettifies column names into legend labels. Used automatically when `auto_label_map=True`. |
| `GradientColourMap(cols, start, end).run()` | Spreads a linear colour gradient across the columns. Used automatically when `auto_colour_map=True`. |
| `pids_on_port(port)`, `kill_processes_on_port(port)`, `stop_dash_ports()` | Windows-only (`netstat` / `taskkill`) helpers to free Dash ports 8050–8060 when a previous server is still holding the port. `dry_run=True` reports without killing. |

### Config dataclasses (all frozen)

| class | holds |
|---|---|
| `BaseFigureConfig` | figure-agnostic look: `fig_height`, `port`, backgrounds, fonts, gridlines, legend, colour-group machinery. Colour defaults come from `theme`. |
| `TimeSeriesFigureConfig` | time-series specifics: **`reindex`**, `cols_of_interest`, `regime_col`, `label_map`, `colour_map`, `opacity_map`, `x_tick_label_mode` / `x_tick_label_format`, `stack_mode`, overall-line options. |
| `HistogramFigureConfig` | histogram/KDE specifics: bins, colours, bandwidth, summary-stat lines. |
| `TimeSeriesAppConfig` / `HistogramAppConfig` | the flat composites of Base + the figure-specific config. These are what you actually construct. |
| `ColourGroupConfig(cols, colour, opacity, start, end)` | paints a named subset of columns one flat colour or its own gradient, instead of the global gradient. |

### Re-basing to 100 — the `reindex` field

Each plotted series is divided by its value on an **anchor date** and multiplied by 100, so every line
reads 100 there and the y-axis becomes "Level (Base 100)". The anchor is chosen by `cfg.reindex`
(the same anchor-policy words as `analysis.returns.normalise`):

| `reindex=` | anchor | use when |
|---|---|---|
| `True` (**default**) or `"first_row"` | the first row of the currently displayed window | all series already start together. A series that is NaN on that row comes out **all-NaN** — it silently vanishes. |
| `"common"` | the first date on which **every** series has data, recomputed for the displayed window | series start on different dates and you want a like-for-like race from the first date they all exist. Raises `ValueError` listing each series' first/last date if no such date exists. |
| `"self"` | each series' **own** first valid observation | you want to see every series' full history. Lines all start at 100 on *different* dates, so they are **not** comparable in level — it shows "how far back does it go", not "who won". |
| a `pd.Timestamp` | that exact date | you want to pick the anchor yourself (e.g. re-base at a 2020 or 2022 low). |
| `False` | none — raw levels | series already share a scale. |

Verified on a two-column test frame where `b` starts 3 rows after `a`: `True` → `b` all-NaN;
`"common"` → `a` starts at 25.0, `b` at 100; `"self"` → both start at 100; `False` → raw 1.0 and 10.0.

Things worth knowing before relying on it:

* **There is no on-page date picker.** Choose the anchor in code via `reindex=`. In `LevelDashApp` the
  range slider *does* re-base live for `True`/`"first_row"` and `"common"`, because the anchor is
  recomputed on the window the slider leaves visible — dragging the left handle re-bases to 100 at
  that date.
* **A `pd.Timestamp` anchor is looked up on the DataFrame index, not on the `time` column.** With the
  default `RangeIndex` it raises `KeyError`. Use `df.set_index("time", drop=False)` (verified working).
  The date must also fall inside the displayed window and hold a value for every series (read from the
  code; not exercised).
* The overall/composite line (`show_overall_line`) uses the same anchor as the series.

## `panel.py` — `PanelBuilder`

A class that loads close prices, derives normalised/return frames and plots the sector-grid book. It is
built around the fixed sector map below, so for an ad-hoc IBKR comparison prefer the level figure above.

Module constants: `SECTOR_MAP` (sector → tickers), `SECTOR_COLORS`, `SECTOR_COLOUR_SCALES`,
`EIGENVEC_STYLES`, `LINE_STYLES`, `OPACITIES`, `WIDTHS`.

### Loading

`PanelBuilder(df_all=None, tickers=<all in SECTOR_MAP>, start_date="2024-01-01", data_dir=None, ncols=2, nrows=None, ragged=False)`

* With no `df_all`, `_load()` reads **`{data_dir}/{ticker}.csv`** (default `data/raw/market/`), lower-cases
  the columns, accepts `date` or IBKR's `datetime`, parses `YYYYMMDD`, and keeps the `close` column.
  It reads CSV only, not the parquet cache. A ticker that fails to load is skipped with a printed
  warning, not an error.
* **`start_date` defaults to `"2024-01-01"`** — rows before it are dropped, so raise the reach (e.g.
  `"1990-01-01"`) when looking at deep history.
* `ragged=False` (default) forward-fills then `dropna()`s, which truncates the *whole* panel to the
  latest first-bar across all tickers. `ragged=True` skips the drop, leaves each series NaN outside its
  own coverage (Plotly draws that as a line break) and prints each series' first/last bar. The
  forward-fill still runs first, so a series that ended early is flat-filled to the end of the panel and
  its printed "last bar" is the panel end, not its true last observation.
* Passing `df_all` skips `_load()` entirely, so `ragged` has no effect there.
* `__init__` always builds `df_norm` with `anchor="first_row"`, so with a ragged panel a late-starting
  column is all-NaN in `df_norm` / `df_plot`. Rebuild with `pb._normalise(anchor="common")` (or
  `"self"`) if you plot from those.

Attributes set in `__init__`: `df_all` (closes), `dates`, `df_norm` (re-based to 100), `df_ret` (daily
returns), `df_plot` (copy of `df_norm` used by the plotting/animation methods), `trace_configs`.

### Derived frames (thin wrappers over `portutils.analysis.returns`)

| method | returns |
|---|---|
| `_normalise(df=None, base=100, anchor="first_row")` | wealth index `price / anchor_price * base`; `anchor` also takes `"common"`, `"self"` or a `pd.Timestamp` |
| `_pct_returns(df=None, anchor="first_row")` | cumulative % return from the anchor |
| `_daily_returns(df=None)` | simple daily % returns, first row dropped |
| `_log_returns(df=None)` | daily log returns, first row dropped |
| `simulate_weights(rebal_freq="QE")` | drifting equal weights snapped back to 1/N on rebalance dates |
| `attribution(weights, sector_map)` | 5-tuple `(sector_contrib, portfolio_daily, sector_cum, portfolio_series_change, portfolio_series)` |

### Building a figure

| method | what it adds |
|---|---|
| `make_panel_subplots(sector_map, titles, horizontal_spacing, ncols, nrows, overall_per_sector, df_norm)` | the empty subplot grid, one cell per sector. With `overall_per_sector=True` it titles each cell with the sector's total return, and **writes `<sector>_Composite` columns into `df_norm` in place**. |
| `add_sector_traces(fig, df, sector_map, colours, line_styles, opacities, widths, normalise, overall_per_sector)` | one line per ticker in each sector's cell, plus an equal-weight composite line; records what it plotted in `trace_configs`. |
| `add_sector_correlation_heatmaps(fig, ret_df, sector_map, colours, colour_scales)` | pairwise-correlation heatmaps per sector, from a returns frame. |
| `add_pca_waterfall(pca_df, …)` | scree plot: eigenvalue bars plus cumulative explained variance on a secondary axis. Returns its own figure. |
| `add_var_explained_chart(var_exp_df, n_pcs, …)` | per-asset % of variance explained by the market/sector factors (idiosyncratic-risk view). Builds and returns its own figure. |
| `add_loadings_comparison(fig, pca_df, ncols, …)` | principal-component loadings by ticker, coloured by sector. |
| `animation_indices(dates, num_frames=60)` *(static)* | evenly spaced frame positions for an animation. |
| `add_in_frames(fig, df, trace_configs, normalise, overall_per_sector, num_frames)` | animation frames for the traces already added (uses `trace_configs`, so call after `add_sector_traces`). |

### Styling helpers (class methods — call on the class, no instance needed)

| method | what |
|---|---|
| `dark_axis_style()` | the standard dark axis dict (faint grid, off-white ticks/titles) |
| `play_button(label, y, duration)` | a single-button `updatemenus` list to start an animation |
| `fix_axes(fig, df, sector_map, dates, *, ncols, buffer=0.05, normalise, y_label, square_cells)` | sets x/y ranges per sector cell from the data with a symmetric buffer |
| `apply_dark_theme(fig, height=700, width=1200, play_label, play_y, play_duration, second_axis, **layout_overrides)` | applies the dark transparent house theme; extra keywords go straight to `update_layout` |

## `theme.py` — colour and export styling

Constants (colour tokens, no functions): `INK`, `GRID`, `ZEROLINE`, `PAPER_BG`, `PLOT_BG`, `PAGE_BG`,
`MUTED`, `HOVER_FG`, `HOVER_BG`, `BOOK_COLOURS`, `TICKER_COLOURS` (pinned identities: KMLM, SPY, MNA,
FLSP), `CATEGORICAL` (the 8-hue palette), `SPLIT_COLOURS` (realised / unrealised / total),
`RESIDUAL_LABEL`, `RESIDUAL_COLOUR`, plus the secondary-axis legend-spacing constants.

| function | what |
|---|---|
| `ticker_colour_map(symbols, overrides=None)` | deterministic `symbol → hex`. Pinned tickers keep their fixed colour; the rest take `CATEGORICAL` slots in the order given; a **9th** distinct unpinned symbol gets `MUTED` grey rather than wrapping round. Pass a sorted list for a stable map. Feed it to `colour_map=` of the level figure to keep colours consistent across figures. |
| `apply_export_theme(fig, *, ink, grid, paper, plot)` | stamps the opaque dark palette onto a *finished* figure in place, just before `write_html`. Touches presentation only, never traces or data. |
| `apply_secondary_axis_spacing(fig, *, enabled=None)` | moves the legend clear of a right-hand y-axis and reserves margin for it. `enabled` overrides auto-detection. |
| `apply_export_spacing(fig)` | widens that legend gap for a saved figure (the same fraction of plot width is narrower inline than on a full-width page). Idempotent. |

## Typical recipe: compare several IBKR series

```python
import pandas as pd
from portutils.viz.dash_timeseries_app import make_level_figure
from portutils.viz.theme import ticker_colour_map

prices = pd.read_parquet("data/processed/prices_<tag>.parquet")     # wide: date index, one column per symbol
df = prices.rename_axis("time").reset_index()                       # level figure wants a 'time' column
cols = [c for c in df.columns if c != "time"]

# every series from its own first bar — shows how far back each one reaches
fig = make_level_figure(df, cols, reindex="self", colour_map=ticker_colour_map(sorted(cols)))
fig.show()

# like-for-like from the first date they all exist
make_level_figure(df, cols, reindex="common").show()

# re-base at a date you choose (needs the date index kept)
make_level_figure(df.set_index("time", drop=False), cols, reindex=pd.Timestamp("2020-03-23")).show()
```

## One colour per ticker, everywhere

`theme.py` exists so a symbol is the same colour in a notebook, in the Dash app and in an exported
PNG. Charts that colour by position-in-legend instead make two figures of the same book look like
two different books — which is a correctness problem disguised as a styling one.

Use `ticker_colour_map()` to get the mapping and pass it through; call `apply_export_theme()` before
writing a figure to disk.

## Configuration objects rather than arguments

`dash_timeseries_app.py` takes dataclass configs (`TimeSeriesFigureConfig`, `HistogramFigureConfig`,
…) rather than long keyword lists, so a chart's setup can be stored, diffed and reused. The public
Dash app in [`app/`](../../../app/) is separate — it consumes these helpers, it does not define them.
