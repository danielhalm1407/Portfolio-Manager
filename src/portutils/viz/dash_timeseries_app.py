# dash_timeseries_app.py

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from dataclasses import dataclass, replace

# Dash is an optional dependency — if it is not installed the module can still
# be imported and used for pure Plotly figure construction (make_level_figure).
# Attempting to call LevelDashApp.build() without Dash will raise a clear error.
try:
    from dash import Dash, html, dcc, Input, Output
except ImportError:
    Dash = None
    html = None
    dcc = None
    Input = None
    Output = None


import plotly.graph_objects as go

import matplotlib.colors as mcolors  # used to build linear colour gradients
import numpy as np
import pandas as pd
import subprocess  # used only by the port-management helpers at the bottom
from typing import Mapping, Sequence, Optional, Any
from scipy.stats import gaussian_kde


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────

# Human-readable axis/legend labels for well-known column names.
# Used as a fallback when auto_label_map=False and no label_map is supplied.
DEFAULT_LABEL_MAP = {
    "spx_level_yf": "S&P 500 Index<br>Level YF",
    "spx_period_return_yf": "S&P 500 Index<br>Return YF",
    "es_level_yf": "ES Futures<br>Settlement YF",
    "es_period_return_yf": "ES Futures<br>Return YF",
    "spx_level": "S&P 500 Index<br>Level CIQ",
    "spx_period_return": "S&P 500 Index<br>Return CIQ",
    "spy_period_return": "SPY ETF<br>Return CIQ",
}

# Default trace colours for well-known column names.
# Used as a fallback when auto_colour_map=False and no colour_map is supplied.
DEFAULT_COLOUR_MAP = {
    "spx_level_yf": "purple",
    "es_level_yf": "blue",
    "spx_level": "cyan",
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA-CLASS CONFIGS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ColourGroupConfig:
    """
    Defines colour/opacity for a named subset of columns.

    Used inside TimeSeriesAppConfig.colour_groups to paint groups of series
    with a single flat colour or a short gradient, instead of relying on
    the global gradient that spans all cols_of_interest.

    Fields
    ------
    cols    : columns that belong to this group
    colour  : flat hex/named colour — if set, overrides start/end gradient
    opacity : float in [0, 1]; if None, the global default_opacity is used
    start   : gradient start colour (used when colour is None)
    end     : gradient end colour   (used when colour is None)
    name    : label for the matplotlib colormap created internally
    """
    cols: Sequence[str]
    colour: Optional[str] = None
    opacity: Optional[float] = None
    start: Optional[str] = None
    end: Optional[str] = None
    name: str = "gradient"


@dataclass(frozen=True)
class BaseFigureConfig:
    """Figure-agnostic theme/layout — reusable across any figure kind."""
    # Sizing & server
    fig_height: int = 800
    port: int = 8050
    # Background / paper — defaults mimic plotly_dark template
    paper_bgcolor: str = "rgb(17,17,17)"
    plot_bgcolor: str = "rgb(17,17,17)"
    # Dash page styling
    page_bgcolor: str = "#111"
    page_title_color: str = "white"
    page_text_color: str = "white"
    page_padding: str = "20px"
    # Page typography (used by LevelDashApp section headings + commentary)
    page_title_font_family: str = "Open Sans, Arial, sans-serif"
    page_title_font_size: str = "22px"
    page_title_margin_bottom: str = "4px"
    commentary_font_family: str = "Open Sans, Arial, sans-serif"
    commentary_font_color: str = "white"
    commentary_font_size: str = "16px"
    commentary_line_height: str = "1.5"
    commentary_margin_bottom: str = "16px"
    commentary_max_width: str = "1100px"
    panel_margin_bottom: str = "32px"
    # Fonts — plotly_dark uses light text on dark background
    font_color: Optional[str] = "#f2f5fa"
    title_font_color: Optional[str] = "white"
    axis_title_font_color: Optional[str] = "white"
    axis_tick_font_color: Optional[str] = "white"
    legend_colour: Optional[str] = "white"
    hover_label_font_color: Optional[str] = "black"
    hover_label_bgcolor: Optional[str] = "white"
    # Gridlines / axes — plotly_dark grid tones
    gridcolor: Optional[str] = "#526070"
    zerolinecolor: Optional[str] = "#526070"
    # Per-axis grid visibility — default to horizontal-only background grid
    xaxis_showgrid: bool = False
    yaxis_showgrid: bool = True
    # Legend
    show_legend: bool = True
    # Colour-group machinery (reusable across ts AND hist)
    colour_groups: Optional[Sequence[Any]] = None
    auto_colour_map: bool = True
    colour_start: str = "cyan"
    colour_end: str = "purple"
    default_opacity: float = 1.0
    # Dash app
    title: str = "Figure"


@dataclass(frozen=True)
class TimeSeriesFigureConfig:
    """Time-series-specific configuration."""
    figure_title: str = "Levels Reindexed to 100"
    reindex: bool = True
    cols_of_interest: Optional[Sequence[str]] = None
    regime_col: Optional[str] = None
    label_map: Mapping[str, str] = None
    auto_label_map: bool = True
    colour_map: Mapping[str, str] = None
    opacity_map: Mapping[str, float] = None
    regime_fill_opacity: float = 0.2
    x_tick_label_mode: str = "full"
    x_tick_label_format: Optional[str] = None
    graph_id: str = "level-plot"
    slider_id: str = "time-range-slider"
    num_marks: int = 20
    close_hour: int = 15
    close_minute: int = 50
    stack_mode: str = "none"
    show_overall_line: bool = False
    overall_col: Optional[str] = None
    overall_label: str = "Overall"
    overall_colour: str = "white"
    overall_width: float = 3.0
    overall_opacity: float = 1.0
    show_stack_mode_control: bool = False
    stack_mode_control_id: str = "stack-mode-control"
    show_plotly_stack_mode_buttons: bool = False
    plotly_stack_mode_buttons_x: float = 0.0
    plotly_stack_mode_buttons_y: float = 1.18


@dataclass(frozen=True)
class HistogramFigureConfig:
    """Histogram + KDE figure-specific configuration."""
    figure_title: str = "Histogram + KDE"
    xaxis_title: str = "Value"
    yaxis_title: str = "Density"
    # Histogram
    hist_colour: str = "cyan"
    hist_opacity: float = 0.6
    nbinsx: int = 60
    histnorm: str = "probability density"
    hist_name: str = "histogram"
    # KDE
    kde_colour: str = "blue"
    kde_opacity: float = 0.5
    kde_fill: Optional[str] = "tozeroy"
    kde_points: int = 200
    kde_name: str = "kde"
    kde_bandwidth: Optional[float] = None
    # Summary-stat vertical lines
    vline_stats: Optional[Sequence[str]] = ("min", "q1", "median", "mean", "q3", "max")
    vline_colour_map: Optional[Mapping[str, str]] = None
    vline_default_colour: str = "white"
    vline_dash: str = "dash"
    vline_width: float = 1.0
    vline_annotate: bool = True


@dataclass(frozen=True)
class TimeSeriesAppConfig(TimeSeriesFigureConfig, BaseFigureConfig):
    """Flat composite of Base + TimeSeries — replaces the legacy TimeSeriesAppConfig."""
    title: str = "Comparison of Levels"


@dataclass(frozen=True)
class HistogramAppConfig(HistogramFigureConfig, BaseFigureConfig):
    """Flat composite of Base + Histogram."""
    title: str = "Distribution"


def _resolve(
    *,
    single_cfg,
    base_cfg,
    type_cfg,
    composite_cls,
    **overrides,
):
    """Merge a (base_cfg, type_cfg) pair (or accept a pre-built single_cfg) into
    a flat composite, then apply per-call overrides via dataclasses.replace.
    """
    if single_cfg is not None:
        merged = single_cfg
    else:
        base_kwargs = base_cfg.__dict__ if base_cfg is not None else {}
        type_kwargs = type_cfg.__dict__ if type_cfg is not None else {}
        # merged here creates a composite class which is like a longer dict combining the stuff from
        # the base config and type config
        merged = composite_cls(**{**base_kwargs, **type_kwargs})
    if overrides:
        # Filter overrides to known fields of composite_cls so we don't pass
        # stray kwargs through.
        allowed = {f for f in merged.__dataclass_fields__}
        filtered = {k: v for k, v in overrides.items() if k in allowed}
        merged = replace(merged, **filtered)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: AXIS-TICK FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

def _format_time_label(ts, cfg: TimeSeriesAppConfig) -> str:
    """Format a single Timestamp into a string for axis ticks / slider marks.

    Resolution is controlled by cfg.x_tick_label_mode or cfg.x_tick_label_format.
    """
    # If the caller supplied an explicit strftime string, use it directly.
    if cfg.x_tick_label_format:
        return ts.strftime(cfg.x_tick_label_format)

    # "year_month" mode is useful for multi-year daily data where full timestamps crowd the axis.
    if cfg.x_tick_label_mode == "year_month":
        return ts.strftime("%Y-%m")

    # Default "full" mode: show date + time, useful for intra-day data.
    return ts.strftime("%y-%m-%d %H:%M")


# ─────────────────────────────────────────────────────────────────────────────
# LABEL GENERATION
# ─────────────────────────────────────────────────────────────────────────────

class AutoLabelMap:
    """
    Derives human-readable legend/axis labels from raw column names.

    Algorithm: replace underscores with spaces, title-case, then insert
    Plotly HTML line-breaks (<br>) so no line exceeds 16 characters.
    """

    def __init__(self, cols, start="cyan", end="purple", name="gradient"):
        # Store the list of columns to label; remaining keyword args are
        # accepted for API symmetry with GradientColourMap but are unused here.
        self.cols = list(cols)
        self.label_map = None  # populated by run()

    def run(self):
        """Build and return the label_map dict {col_name: formatted_label}."""
        label_map = {}

        for col in self.cols:
            # Convert "spx_period_return_yf" → ["Spx", "Period", "Return", "Yf"]
            words = col.replace("_", " ").title().split(" ")

            label = ""          # accumulated label with <br> separators
            current_line = ""   # text being gathered for the current line

            # Walk through words, flushing to label with a <br> when a word
            # would push the current line past 16 characters.
            for word in words:
                # +1 for the space separator that would precede the word
                if len(current_line) + len(word) + 1 <= 16:
                    # Word fits on the current line → append it
                    current_line = (current_line + " " + word) if current_line else word
                else:
                    # Word does not fit → save the current line and start fresh
                    label = (label + "<br>" + current_line) if label else current_line
                    current_line = word

            # Flush whatever remains in current_line after the loop ends
            if current_line:
                label = (label + "<br>" + current_line) if label else current_line

            label_map[col] = label

        self.label_map = label_map
        return self.label_map


# ─────────────────────────────────────────────────────────────────────────────
# COLOUR GENERATION
# ─────────────────────────────────────────────────────────────────────────────

class GradientColourMap:
    """
    Maps column names → hex colour strings using a linear two-colour gradient.

    The gradient spans from `start` to `end` with one colour per column,
    evenly spaced so that the first column gets `start` and the last gets `end`.

    Usage
    -----
        gen = GradientColourMap(cols, start="cyan", end="purple")
        gen.run()
        colour_map = gen.colour_map   # {"col_a": "#00ffff", "col_b": ..., ...}
    """

    def __init__(self, cols, start="cyan", end="purple", name="gradient"):
        self.cols = list(cols)
        self.start = start    # colour at position 0 of the gradient
        self.end = end        # colour at position 1 of the gradient
        self.name = name      # internal name for the matplotlib LinearSegmentedColormap

        self.colour_map = None  # populated by run()

    def run(self):
        """Build and return the colour_map dict {col_name: hex_colour}."""
        n = len(self.cols)
        # Guard: return empty dict if there are no columns to colour.
        if n == 0:
            self.colour_map = {}
            return self.colour_map

        # Build a matplotlib colormap from just two colour stops.
        cmap = mcolors.LinearSegmentedColormap.from_list(self.name, [self.start, self.end])

        # Sample n evenly-spaced positions in [0, 1] — one per column.
        positions = np.linspace(0, 1, n)

        # Evaluate the colormap at each position and convert to hex strings.
        colours = [mcolors.to_hex(cmap(p)) for p in positions]

        # Zip column names with their assigned hex string colours
        self.colour_map = dict(zip(self.cols, colours))
        return self.colour_map


# ─────────────────────────────────────────────────────────────────────────────
# COLOUR-GROUP HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_colour_group(group: Any, idx: int, cols_of_interest: Optional[Sequence[str]]) -> ColourGroupConfig:
    """Coerce a raw colour-group specification (dict or ColourGroupConfig) into a ColourGroupConfig.

    Accepts dicts so callers can pass compact literals instead of importing
    the dataclass explicitly.  Validates that all named columns exist in
    cols_of_interest and that either a flat colour or a gradient is specified.
    """
    if isinstance(group, ColourGroupConfig):
        # Already the right type — use as-is.
        spec = group
    elif isinstance(group, dict):
        # Accept both "cols" and "columns" as key names for flexibility.
        cols = group.get("cols", group.get("columns"))
        spec = ColourGroupConfig(
            cols=cols,
            colour=group.get("colour", group.get("color")),  # accept US / UK spelling
            opacity=group.get("opacity", group.get("alpha")),
            start=group.get("start"),
            end=group.get("end"),
            name=group.get("name", f"group_{idx}"),
        )
    else:
        raise ValueError("colour_groups items must be ColourGroupConfig objects or dicts")

    # Validate: must have at least one column.
    if spec.cols is None or len(spec.cols) == 0:
        raise ValueError("Each colour group must define a non-empty 'cols' list")

    # Optional validation: all listed columns must be present in cols_of_interest.
    #if cols_of_interest is not None:
    #    missing = [c for c in spec.cols if c not in cols_of_interest]
    #    if missing:
    #        raise ValueError(f"These colour-group columns are missing from cols_of_interest: {missing}")

    # Validate: must define either a flat colour or a gradient (start/end).
    if spec.colour is None and spec.start is None and spec.end is None:
        raise ValueError("Each colour group must define either 'colour' or a 'start'/'end' gradient")

    return spec


def _build_group_colour_map(cfg: TimeSeriesAppConfig) -> Mapping[str, str]:
    """Build a partial colour_map covering only the columns listed in colour_groups.

    Returns an empty dict when no colour_groups are defined.
    Called by _build_colour_map to layer group colours on top of the global gradient.
    """
    if not cfg.colour_groups:
        return {}

    colour_map = {}
    for idx, raw_group in enumerate(cfg.colour_groups):
        # Coerce dict / ColourGroupConfig → validated ColourGroupConfig.
        group = _normalise_colour_group(raw_group, idx, cfg.cols_of_interest)

        if group.colour is not None:
            # Flat colour: assign the same colour to every column in the group.
            # the update syntax below adds entries to colour_map for each column 
            # in group.cols, all with the same colour value of group.colour
            # this ensures we never call GradientColourMap for a group that has a flat colour specified,
            # so it is thus fine that GradientColourMaps is not built for groups with flat colours, 
            # (e.g., only handles start and end colours)
            colour_map.update({col: group.colour for col in group.cols})
            continue

        # Gradient: fall back to cfg-level start/end if the group doesn't override them.
        start = cfg.colour_start if group.start is None else group.start
        # If only start is set (no end), the gradient collapses to a single colour.
        end = start if group.end is None else group.end
        # update the colour map, which stores the colours for each series
        # in the columns. Of course, if columns is just a single column, then the gradient
        #  will collapse to a single colour, even if start and end are different.
        colour_map.update(
            GradientColourMap(group.cols, start=start, end=end, name=group.name).run()
        )

    return colour_map


def _build_group_opacity_map(cfg: TimeSeriesAppConfig) -> Mapping[str, float]:
    """Build a partial opacity_map covering only columns in colour_groups that set opacity.

    Returns an empty dict when no colour_groups are defined.
    Called by _build_opacity_map to layer group opacities on top of the global default.
    """
    if not cfg.colour_groups:
        return {}

    opacity_map = {}
    for idx, raw_group in enumerate(cfg.colour_groups):
        group = _normalise_colour_group(raw_group, idx, cfg.cols_of_interest)
        # Only override opacity for columns where the group explicitly sets it.
        if group.opacity is not None:
            opacity_map.update({col: float(group.opacity) for col in group.cols})
    return opacity_map


def _build_colour_map(cfg: TimeSeriesAppConfig) -> Mapping[str, str]:
    """Resolve the final colour_map using a three-layer priority:

    1. Global gradient (auto) or DEFAULT_COLOUR_MAP entries (manual fallback).
    2. Per-group overrides from cfg.colour_groups (applied on top of layer 1).
    3. Explicit per-column cfg.colour_map overrides (highest priority).

    This means a caller can combine an auto gradient with targeted overrides.
    """
    # Layer 1 — base colours for all cols_of_interest.
    if cfg.auto_colour_map and cfg.cols_of_interest:
        # Generate a gradient spanning all columns in order.
        colour_map = GradientColourMap(
            cfg.cols_of_interest,
            start=cfg.colour_start,
            end=cfg.colour_end,
            name="auto_gradient",
        ).run()
    else:
        # Manual mode: seed the map with whatever DEFAULT_COLOUR_MAP knows about.
        colour_map = {c: DEFAULT_COLOUR_MAP[c] for c in (cfg.cols_of_interest or []) if c in DEFAULT_COLOUR_MAP}

    # Layer 2 — group overrides (may overwrite some of the above).
    colour_map.update(_build_group_colour_map(cfg))

    # Layer 3 — explicit caller overrides (highest priority, overwrites everything).
    if cfg.colour_map is not None:
        colour_map.update(cfg.colour_map)

    return colour_map


def _build_opacity_map(cfg: TimeSeriesAppConfig) -> Mapping[str, float]:
    """Resolve the final opacity_map using a three-layer priority:

    1. Global default_opacity applied to every column.
    2. Per-group overrides from cfg.colour_groups.
    3. Explicit per-column cfg.opacity_map overrides.
    """
    # Layer 1 — fill every column with the global default opacity.
    opacity_map = {
        col: float(cfg.default_opacity)
        for col in (cfg.cols_of_interest or [])
    }

    # Layer 2 — group-level opacity overrides.
    opacity_map.update(_build_group_opacity_map(cfg))

    # Layer 3 — explicit per-column overrides (highest priority).
    if cfg.opacity_map is not None:
        opacity_map.update({k: float(v) for k, v in cfg.opacity_map.items()})

    return opacity_map


# ─────────────────────────────────────────────────────────────────────────────
# DATA FILTERING
# ─────────────────────────────────────────────────────────────────────────────

def _filter_df_by_time_window(df, time_window=None):
    """Slice df to a time window specified as either integer row indices or datetimes.

    Parameters
    ----------
    df          : the full DataFrame (must have a 'time' column for datetime slicing)
    time_window : None  → return a copy of the full df (no filtering)
                  (int, int)      → iloc-based row slice [left, right] inclusive
                  (datetime, datetime) → boolean mask on df['time']

    Returns a new DataFrame (never mutates the input).
    """
    # No window specified → pass through the full data.
    if time_window is None:
        return df.copy()

    if isinstance(time_window, (list, tuple)) and len(time_window) == 2:
        left, right = time_window

        # Integer pair → treat as positional row indices from the slider.
        if isinstance(left, (int, np.integer)) and isinstance(right, (int, np.integer)):
            # iloc slicing: right+1 because iloc end is exclusive.
            return df.iloc[left:right + 1].copy()

        # Otherwise assume datetime-like → filter on the 'time' column.
        start = pd.to_datetime(left)
        end = pd.to_datetime(right)
        return df[(df["time"] >= start) & (df["time"] <= end)].copy()

    raise ValueError("time_window must be None or a 2-item tuple/list of integer indices or datetime-like bounds")


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG NORMALISATION
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_config(cfg: TimeSeriesAppConfig) -> TimeSeriesAppConfig:
    """Return a new TimeSeriesAppConfig with label_map, colour_map, and opacity_map resolved.

    This exists to keep TimeSeriesAppConfig a plain frozen dataclass (no __post_init__
    logic) while still supporting auto-generation and default-fallback semantics.
    Called once at construction time by both LevelDashApp.__init__ and make_level_figure.
    """
    # ── label_map ─────────────────────────────────────────────────────────────
    if cfg.label_map is not None:
        # Caller supplied a complete label_map → use it verbatim.
        label_map = cfg.label_map
    else:
        if cfg.auto_label_map and cfg.cols_of_interest:
            # Auto-generate labels by prettifying column names.
            label_map = AutoLabelMap(cfg.cols_of_interest).run()
        else:
            # Fall back to the module-level defaults for well-known column names.
            label_map = DEFAULT_LABEL_MAP

    # ── colour_map and opacity_map ────────────────────────────────────────────
    # Both helpers apply the same three-layer priority (global → group → explicit).
    colour_map = _build_colour_map(cfg)
    opacity_map = _build_opacity_map(cfg)

    # Return an updated copy of cfg with the three resolved maps substituted in.
    # dataclasses.replace() creates a new frozen instance with only the listed
    # fields changed; all other fields carry over from the original cfg.
    # The **{ **cfg.__dict__, ... } pattern unpacks the existing field dict and
    # then overrides specific keys — equivalent to replace() but explicit.
    return TimeSeriesAppConfig(**{**cfg.__dict__, "label_map": label_map, "colour_map": colour_map, "opacity_map": opacity_map})


# ─────────────────────────────────────────────────────────────────────────────
# INPUT VALIDATION (standalone function — used by make_level_figure)
# ─────────────────────────────────────────────────────────────────────────────

def _validate_inputs(df, cfg: TimeSeriesAppConfig) -> None:
    """Raise ValueError with a clear message if df or cfg are inconsistent."""
    if "time" not in df.columns:
        raise ValueError("df must contain a 'time' column.")
    if not np.issubdtype(df["time"].dtype, np.datetime64):
        # Works for pandas datetime64; if timezone-aware, dtype may differ slightly.
        # A more permissive check would be: pandas.api.types.is_datetime64_any_dtype
        raise ValueError("df['time'] must be datetime-like (convert via pd.to_datetime).")

    if len(df) < 2:
        raise ValueError("df must have at least 2 rows for slider/plotting.")

    if cfg.cols_of_interest is None or len(cfg.cols_of_interest) == 0:
        raise ValueError("cols_of_interest must be provided (non-empty).")

    # Ensure every requested column actually exists in df.
    missing = [c for c in cfg.cols_of_interest if c not in df.columns]
    if missing:
        raise ValueError(f"These cols_of_interest are missing from df: {missing}")

    valid_stack_modes = {"none", "stack", "stack_split_sign"}
    if cfg.stack_mode not in valid_stack_modes:
        raise ValueError(
            f"stack_mode must be one of {sorted(valid_stack_modes)}; got '{cfg.stack_mode}'"
        )

    if cfg.overall_col is not None and cfg.overall_col not in df.columns:
        raise ValueError(f"overall_col '{cfg.overall_col}' is missing from df")


# ─────────────────────────────────────────────────────────────────────────────
# CORE FIGURE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_level_figure(df, cfg: TimeSeriesAppConfig, time_range=None):
    """Build and return a single go.Figure for the given time window and stack mode.

    This is the central rendering function.  All public entry points
    (make_level_figure, LevelDashApp._update_plot, the Dash callback) ultimately
    call this function (directly or via _build_level_figure_with_plotly_mode_buttons).

    Parameters
    ----------
    df         : full DataFrame (must have 'time' column + cols_of_interest)
    cfg        : normalised TimeSeriesAppConfig (label/colour/opacity maps already resolved)
    time_range : passed straight to _filter_df_by_time_window — either None,
                 an (int, int) slider value, or a (datetime, datetime) pair

    Returns
    -------
    go.Figure — ready to display or embed in a Dash dcc.Graph.
    """

    # ── Step 1: filter to the requested time window ───────────────────────────
    # Produces a copy of df restricted to the rows selected by the slider.
    filtered_df = _filter_df_by_time_window(df, time_range)

    # Guard: return an empty figure if the filter left no rows.
    if len(filtered_df) == 0:
        return go.Figure()

    # ── Step 2: compute evenly-spaced x-axis tick positions ───────────────────
    # We cap at 20 ticks regardless of data length to keep the axis readable.
    num_ticks = min(20, len(filtered_df))
    # linspace across the integer row index range, cast to int for iloc use.
    tick_positions = np.linspace(0, len(filtered_df) - 1, num_ticks, dtype=int)

    # ── Step 3: create an empty figure and determine y-axis label ─────────────
    fig = go.Figure()
    y_axis_title_value = "Level"  # updated below when reindex=True

    # ── Step 4: reindex (or not) each series ─────────────────────────────────
    # Store the transformed y-values keyed by column so all stack-mode branches
    # can share the same pre-processed data.
    transformed_series = {}
    for col in cfg.cols_of_interest:
        series = filtered_df[col]
        first_value = series.iloc[0]  # anchor point for reindexing

        if cfg.reindex:
            # Divide each value by the first value and multiply by 100 so the
            # series starts at 100 regardless of its original scale.
            # This makes multiple series directly comparable on the same axis.
            y = (series / first_value) * 100
            y_axis_title_value = "Level (Base 100)"
        else:
            # Use raw values — useful when series are already on the same scale.
            y = series

        transformed_series[col] = y

    # ── Step 5: add traces according to stack_mode ───────────────────────────

    if cfg.stack_mode == "none":
        # ── "none": each series drawn as an independent line ─────────────────
        for col in cfg.cols_of_interest:
            y = transformed_series[col]

            fig.add_trace(
                go.Scatter(
                    x=filtered_df.index,                    # integer row index on x-axis
                    y=y,
                    mode="lines",
                    name=cfg.label_map.get(col, col),       # legend label (falls back to col name)
                    line=dict(width=2, color=cfg.colour_map.get(col)),
                    opacity=cfg.opacity_map.get(col, cfg.default_opacity),
                )
            )

    elif cfg.stack_mode == "stack":
        # ── "stack": all series share a stackgroup so Plotly sums them cumulatively ──
        for col in cfg.cols_of_interest:
            y = transformed_series[col]

            fig.add_trace(
                go.Scatter(
                    x=filtered_df.index,
                    y=y,
                    mode="lines",
                    stackgroup="stacked",   # all traces with the same stackgroup are stacked
                    name=cfg.label_map.get(col, col),
                    line=dict(width=1.0, color=cfg.colour_map.get(col)),
                    opacity=cfg.opacity_map.get(col, cfg.default_opacity),
                )
            )

    else:
        # ── "stack_split_sign": positive values stack above zero, ─────────────
        #    negative values stack below zero, so the two halves never overlap.
        for col in cfg.cols_of_interest:
            y = transformed_series[col]

            # Separate the series into its positive and negative parts.
            y_pos = y.clip(lower=0)   # zero out negative values → positive half
            y_neg = y.clip(upper=0)   # zero out positive values → negative half

            # Check whether each half actually contains non-zero data.
            has_pos = bool((y_pos > 0).any())
            has_neg = bool((y_neg < 0).any())

            # Track whether we've already shown this column in the legend;
            # if both halves exist, show the legend entry only for the first trace.
            show_legend_once = True

            if has_pos:
                fig.add_trace(
                    go.Scatter(
                        x=filtered_df.index,
                        y=y_pos,
                        mode="lines",
                        stackgroup="positive",   # positive traces stack above zero
                        name=cfg.label_map.get(col, col),
                        legendgroup=col,         # link pos + neg traces in the legend so toggling one hides both
                        showlegend=show_legend_once,
                        line=dict(width=1.0, color=cfg.colour_map.get(col)),
                        opacity=cfg.opacity_map.get(col, cfg.default_opacity),
                    )
                )
                # Suppress the legend entry for the negative half of the same column.
                show_legend_once = False

            if has_neg:
                fig.add_trace(
                    go.Scatter(
                        x=filtered_df.index,
                        y=y_neg,
                        mode="lines",
                        stackgroup="negative",   # negative traces stack below zero
                        name=cfg.label_map.get(col, col),
                        legendgroup=col,
                        showlegend=show_legend_once,
                        line=dict(width=1.0, color=cfg.colour_map.get(col)),
                        opacity=cfg.opacity_map.get(col, cfg.default_opacity),
                    )
                )

    # ── Step 6: optional overall / sum line ──────────────────────────────────
    if cfg.show_overall_line:
        if cfg.overall_col is not None:
            # Use a dedicated column (e.g. a pre-computed aggregate).
            overall_series = filtered_df[cfg.overall_col]
            if cfg.reindex:
                first_value = overall_series.iloc[0]
                overall_y = (overall_series / first_value) * 100
            else:
                overall_y = overall_series
        else:
            # Sum across all transformed series — gives total stacked contribution.
            stacked_values = np.column_stack([transformed_series[col].to_numpy() for col in cfg.cols_of_interest])
            overall_y = pd.Series(stacked_values.sum(axis=1), index=filtered_df.index)

        fig.add_trace(
            go.Scatter(
                x=filtered_df.index,
                y=overall_y,
                mode="lines",
                name=cfg.overall_label,
                line=dict(width=cfg.overall_width, color=cfg.overall_colour),
                opacity=cfg.overall_opacity,
            )
        )

    # ── Step 7: market-close vertical lines ──────────────────────────────────
    # Identify all rows whose 'time' matches the configured close hour:minute.
    close_times = filtered_df[
        (filtered_df["time"].dt.hour == cfg.close_hour) &
        (filtered_df["time"].dt.minute == cfg.close_minute)
    ]
    # Add a dashed vertical rule at each market-close row index.
    for idx in close_times.index:
        fig.add_vline(x=idx, line=dict(color="grey", dash="dash", width=1), opacity=0.5)

    # ── Step 8: font colour resolution ───────────────────────────────────────
    # Specific font overrides fall back to the global font_color when not set.
    title_font_color = cfg.title_font_color or cfg.font_color
    axis_title_font_color = cfg.axis_title_font_color or cfg.font_color
    axis_tick_font_color = cfg.axis_tick_font_color or cfg.font_color

    # Step 9: optional regime fill highlighting.
    # cfg.regime_col names a column in filtered_df whose values label the
    # active regime at each timestamp. Consecutive equal-value runs become
    # one shaded vrect spanning that time window.
    if cfg.regime_col:
        regimes = filtered_df[cfg.regime_col].to_numpy()
        idx = filtered_df.index
        n = len(regimes)
        current_regime = regimes[0]
        start = 0
        for i in range(1, n):
            if regimes[i] != current_regime:
                fig.add_vrect(
                    x0=idx[start],
                    x1=idx[i - 1],
                    fillcolor=cfg.colour_map.get(current_regime),
                    opacity=cfg.regime_fill_opacity,
                    layer="below",  # draw below traces so they remain visible
                    line_width=0,
                )
                current_regime = regimes[i]
                start = i

        # Final run extends to the last timestamp.
        fig.add_vrect(
            x0=idx[start],
            x1=idx[n - 1],
            fillcolor=cfg.colour_map.get(current_regime),
            opacity=cfg.regime_fill_opacity,
            layer="below",
            line_width=0,
        )
        


    # ── Step 10: apply layout ─────────────────────────────────────────────────
    fig.update_layout(
        title=cfg.figure_title,
        xaxis_title="Time",
        yaxis_title=y_axis_title_value,
        showlegend=cfg.show_legend,
        legend=dict(font=dict(color=cfg.legend_colour)),
        hoverlabel=dict(font=dict(color=cfg.hover_label_font_color), bgcolor=cfg.hover_label_bgcolor),
        hovermode="x unified",    # show all series values in a single tooltip at once
        height=cfg.fig_height,
        paper_bgcolor=cfg.paper_bgcolor,   # area outside the plot frame
        plot_bgcolor=cfg.plot_bgcolor,     # area inside the plot frame (axes region)
        # Only pass font dict when a colour is set — avoids overriding Plotly defaults.
        font=(dict(color=cfg.font_color) if cfg.font_color is not None else None),
        title_font=(dict(color=title_font_color) if title_font_color is not None else None),
        xaxis=dict(
            tickmode="array",    # use explicit tick positions rather than auto
            # Map integer row indices back to the formatted datetime strings.
            tickvals=[filtered_df.index[i] for i in tick_positions],
            ticktext=[_format_time_label(filtered_df["time"].iloc[i], cfg) for i in tick_positions],
            tickangle=-90,       # rotate labels to avoid overlap
            title=dict(font=(dict(color=axis_title_font_color) if axis_title_font_color is not None else None)),
            tickfont=(dict(color=axis_tick_font_color) if axis_tick_font_color is not None else None),
            gridcolor=cfg.gridcolor,
            zerolinecolor=cfg.zerolinecolor,
            showgrid=cfg.xaxis_showgrid,
        ),
        yaxis=dict(
            title=dict(font=(dict(color=axis_title_font_color) if axis_title_font_color is not None else None)),
            tickfont=(dict(color=axis_tick_font_color) if axis_tick_font_color is not None else None),
            gridcolor=cfg.gridcolor,
            zerolinecolor=cfg.zerolinecolor,
            showgrid=cfg.yaxis_showgrid,
        ),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-MODE FIGURE BUILDER (Plotly-native buttons — no Dash required)
# ─────────────────────────────────────────────────────────────────────────────

def _build_level_figure_with_plotly_mode_buttons(df, cfg: TimeSeriesAppConfig, time_range=None):
    """Build one Plotly figure that contains all three stack modes as hidden trace sets,
    with native Plotly updatemenus buttons to switch between them.

    This is the Dash-free alternative to show_stack_mode_control: everything
    lives inside a single go.Figure so it works in a static notebook export.

    Strategy
    --------
    1. Build a separate figure for each mode (none / stack / stack_split_sign).
    2. Concatenate all their traces into one figure, with all but the active
       mode's traces set to visible=False.
    3. Attach updatemenus buttons that flip which trace set is visible.
    """
    # The three modes in display order.
    mode_order = ["none", "stack", "stack_split_sign"]
    # Button labels shown in the Plotly toolbar.
    mode_label = {
        "none": "Lines",
        "stack": "Stacked",
        "stack_split_sign": "Split +/-",
    }

    # Default to "none" if the configured mode is somehow not in the list.
    active_mode = cfg.stack_mode if cfg.stack_mode in mode_order else "none"

    # Build one figure per mode, reusing the same data and time window.
    # replace() creates a new frozen cfg with only stack_mode changed.
    mode_figs = {
        mode: _build_level_figure(df, replace(cfg, stack_mode=mode), time_range=time_range)
        for mode in mode_order
    }

    # ── Combine all traces into a flat list, tracking which indices each mode owns ──
    combined_data = []      # all traces from all modes in order
    mode_trace_indices = {} # {mode: [list of trace indices in combined_data]}
    trace_idx = 0

    for mode in mode_order:
        start_idx = trace_idx
        for tr in mode_figs[mode].data:
            # Hide all traces initially; the active mode's traces are turned on below.
            tr.visible = (mode == active_mode)
            combined_data.append(tr)
            trace_idx += 1
        # Record which slice of combined_data belongs to this mode.
        mode_trace_indices[mode] = list(range(start_idx, trace_idx))

    # Create the combined figure, inheriting layout from the active mode's figure.
    fig = go.Figure(data=combined_data, layout=mode_figs[active_mode].layout)

    # ── Build one button per mode ─────────────────────────────────────────────
    buttons = []
    for mode in mode_order:
        # Each button defines a full visibility vector over all combined traces.
        visible = [False] * len(combined_data)
        for idx in mode_trace_indices[mode]:
            visible[idx] = True   # show only this mode's traces when button is clicked
        buttons.append(
            dict(
                label=mode_label[mode],
                method="update",             # "update" modifies trace + layout properties
                args=[{"visible": visible}], # the property to update when button is pressed
            )
        )

    # Attach the button group to the figure layout.
    fig.update_layout(
        updatemenus=[
            dict(
                type="buttons",
                direction="right",      # buttons arranged horizontally
                buttons=buttons,
                x=cfg.plotly_stack_mode_buttons_x,
                y=cfg.plotly_stack_mode_buttons_y,
                xanchor="left",
                yanchor="top",
                showactive=True,        # highlight the currently active button
                active=mode_order.index(active_mode),  # pre-select the correct button
            )
        ]
    )

    return fig


# ─────────────────────────────────────────────────────────────────────────────
# SECTION CONFIG (multi-section Dash pages)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SectionConfig:
    """One section of a multi-section Dash page.

    Each section gets its own heading, optional commentary text,
    RangeSlider, and Graph wired by an independent callback.

    Fields
    ------
    df         : DataFrame for this section (must have 'time' column)
    cfg        : TimeSeriesAppConfig controlling the figure
    commentary : optional markdown text shown between heading and graph
    show_slider: when False, the slider is omitted (figure is static)
    time_window: optional (start, end) bound — datetime-like strings/Timestamps
                 or (int, int) row indices. Applied to the rendered figure
                 (and used as the initial slider value when show_slider=True).
    """
    df: Any
    cfg: TimeSeriesAppConfig
    commentary: Optional[str] = None
    show_slider: bool = True
    time_window: Optional[Any] = None


def _coerce_section(section, idx: int) -> "SectionConfig":
    """Accept dict or SectionConfig; normalise the inner cfg and unique-ify ids."""
    if isinstance(section, dict):
        section = SectionConfig(
            df=section["df"],
            cfg=section["cfg"],
            commentary=section.get("commentary"),
            show_slider=section.get("show_slider", True),
            time_window=section.get("time_window"),
        )
    elif not isinstance(section, SectionConfig):
        raise ValueError("sections must contain SectionConfig objects or dicts")

    cfg = _normalize_config(section.cfg)
    # Auto-suffix component ids so multiple sections don't collide.
    cfg = replace(
        cfg,
        graph_id=f"{cfg.graph_id}-{idx}",
        slider_id=f"{cfg.slider_id}-{idx}",
        stack_mode_control_id=f"{cfg.stack_mode_control_id}-{idx}",
    )
    section.cfg = cfg
    return section


# ─────────────────────────────────────────────────────────────────────────────
# DASH APPLICATION CLASS
# ─────────────────────────────────────────────────────────────────────────────

class LevelDashApp:
    """
    Stateful builder for an interactive Dash application.

    Typical workflow
    ----------------
        cfg = TimeSeriesAppConfig(cols_of_interest=["col_a", "col_b"], reindex=True)
        app_obj = LevelDashApp(df, cfg)
        app_obj.run()                     # blocks; opens on http://localhost:8050

    Or, for JupyterDash / inline use:
        app_obj.build()                   # returns the dash.Dash instance
        app_obj.app.run(...)

    Separation of concerns
    ----------------------
    - __init__  : store df + normalised config; run lightweight validation.
    - build()   : construct the Dash app, layout tree, and wire up callbacks.
    - _update_plot() : called by the Dash callback on every slider / dropdown change.
    - run()     : convenience wrapper that calls build() if needed, then app.run().

    Layout architecture
    -------------------
    The Dash `app` exposes one component tree at `app.layout`. Shape:

        app.layout = html.Div(            ← page wrapper (bg colour, padding)
            children=[                    ← list of per-section panels
                html.Div([                ← section 0 panel (marginBottom)
                    H3(title),
                    Markdown(commentary),     # optional
                    Dropdown(stack_mode),     # optional
                    RangeSlider,              # optional
                    Graph(id="...graph_0"),
                ]),
                html.Div([                ← section 1 panel
                    H3(title), Markdown(...), RangeSlider, Graph,
                ]),
                ...
            ],
        )

    The page-Div's *children* are per-section wrapper Divs (one per
    `SectionConfig` passed in). Each section's *children* are produced by
    `_build_section_children(section)` — see that method for details.

    Each section owns unique component IDs (graph_id, slider_id, etc.) so
    callbacks registered per section in `_register_section_callback` only
    redraw their own section's Graph.
    """

    def __init__(self, df=None, config: TimeSeriesAppConfig = None, sections=None):
        """Two calling styles:

        Single-section (back-compat):
            LevelDashApp(df, cfg)

        Multi-section:
            LevelDashApp(sections=[SectionConfig(df1, cfg1, commentary="..."),
                                   SectionConfig(df2, cfg2, commentary="...")])
        """
        if sections is not None:
            # Multi-section mode.
            if df is not None or config is not None:
                raise ValueError("Pass either (df, config) OR sections=, not both.")
            # we coerce each of the sections into SectionConfig objects and normalise their cfgs,
            #  which also auto-suffixes their component ids to keep them unique across sections
            self.sections = [_coerce_section(s, i) for i, s in enumerate(sections)]
            # Page-level cfg = first section's cfg (used for page bg/title styling).
            self.cfg = self.sections[0].cfg
            self.df = None
        else:
            # Single-section back-compat mode.
            if df is None or config is None:
                raise ValueError("Pass (df, config) or sections=...")
            self.df = df
            self.cfg = _normalize_config(config)
            self.sections = [SectionConfig(df=df, cfg=self.cfg)]

        # Validate every section.
        for sec in self.sections:
            self._validate_section(sec)

        # Placeholder — populated by build().
        self.app: Optional[Any] = None

    def _validate_section(self, section: "SectionConfig") -> None:
        """Validate one section's df + cfg consistency."""
        df = section.df
        cfg = section.cfg

        if "time" not in df.columns:
            raise ValueError("df must contain a 'time' column.")
        # If you use pandas, this check can be improved with pandas.api.types
        if not np.issubdtype(df["time"].dtype, np.datetime64):
            raise ValueError("df['time'] must be datetime-like (convert via pd.to_datetime).")
        if len(df) < 2:
            raise ValueError("df must have at least 2 rows.")
        if cfg.cols_of_interest is None or len(cfg.cols_of_interest) == 0:
            raise ValueError("cols_of_interest must be provided (non-empty).")

        # Detect columns requested in the config that do not exist in df.
        missing = [c for c in cfg.cols_of_interest if c not in df.columns]
        if missing:
            raise ValueError(f"These cols_of_interest are missing from df: {missing}")

        valid_stack_modes = {"none", "stack", "stack_split_sign"}
        if cfg.stack_mode not in valid_stack_modes:
            raise ValueError(
                f"stack_mode must be one of {sorted(valid_stack_modes)}; got '{cfg.stack_mode}'"
            )

        if cfg.overall_col is not None and cfg.overall_col not in df.columns:
            raise ValueError(f"overall_col '{cfg.overall_col}' is missing from df")

    # ─────────────────────────────────────────────────────────────────────────
    def build(self) -> Any:
        """Construct the Dash app, attach layout and callbacks, and return it.

        Calling build() is idempotent in the sense that you can call it once
        and then start/stop the server multiple times, but calling it again
        will overwrite self.app with a fresh instance.

        Returns
        -------
        dash.Dash — the configured application object (also stored as self.app).
        """
        # Guard: Dash is an optional dependency; raise early with a helpful message.
        if Dash is None:
            raise ImportError("dash is required to build the app. Install it to use LevelDashApp.")

        # ── Create Dash application instance ─────────────────────────────────
        # `Dash(__name__)` = single page web server. One app per LevelDashApp.
        # Everything visible in browser hangs off `app.layout` (a component tree).
        app = Dash(__name__)
        self.app = app

        # Page-level style (background, text colour, padding) comes from first
        # section's cfg — page-wide theme not per-section.
        page_cfg = self.cfg

        # ── Build layout tree ────────────────────────────────────────────────
        # Layout shape:
        #   app.layout = html.Div(                       ← outer page wrapper
        #       [
        #           html.Div([... section 0 children ...]),  ← section 0 panel
        #           html.Div([... section 1 children ...]),  ← section 1 panel
        #           ...                                  ← one Div per section
        #       ],
        #       style={page bg + text + padding}
        #   )
        #
        # `layout_children` = list of per-section wrapper Divs. Each wrapper
        # gets its own bottom margin so consecutive sections don't collide.
        # `_build_section_children(section)` returns the inner list of
        # components for one section (title, commentary, controls, graph).
        layout_children = []
        for section in self.sections:
            layout_children.append(
                html.Div(
                    self._build_section_children(section),   # inner components
                    style={"marginBottom": section.cfg.panel_margin_bottom},
                )
            )

        # Outer page Div — its children are the per-section wrapper Divs above.
        app.layout = html.Div(
            layout_children,
            style={
                "backgroundColor": page_cfg.page_bgcolor,
                "color": page_cfg.page_text_color,
                "minHeight": "100vh",
                "padding": page_cfg.page_padding,
            },
        )

        # ── Wire interactivity ───────────────────────────────────────────────
        # Each section owns its own Graph/Slider/Dropdown component IDs
        # (cfg.graph_id, cfg.slider_id, cfg.stack_mode_control_id). Registering
        # one callback per section means slider moves on section 1 only redraw
        # section 1's figure — sections are independent.
        for section in self.sections:
            self._register_section_callback(app, section)

        return app

    # ─────────────────────────────────────────────────────────────────────────
    def _build_section_children(self, section: "SectionConfig"):
        """Build component list for one section panel.

        Returned list is rendered in order, top-to-bottom, inside the
        section's wrapper Div (see `build()`). Composition:

            [ H3 title ]
            [ Markdown commentary ]      ← if section.commentary
            [ "Stack Mode" label,
              Dropdown ]                 ← if cfg.show_stack_mode_control
            [ Br × 4, RangeSlider, Br ]  ← if section.show_slider
            [ Graph ]                    ← always last

        Graph behaviour:
          * If section has interactive inputs (slider/dropdown) → Graph
            starts empty; callback registered in `_register_section_callback`
            populates it on first slider/dropdown event.
          * If section has no inputs (static) → figure built right here using
            `section.time_window` to pre-filter, so page renders something
            on load without waiting for a callback.
        """
        # note that each section's config contains all that is needed for that section's comments and chart to render and
        # create properly

        cfg = section.cfg
        df = section.df

        # ── Title (H3) ───────────────────────────────────────────────────────
        title_style = {
            "color": cfg.page_title_color,
            "fontFamily": cfg.page_title_font_family,
            "fontSize": cfg.page_title_font_size,
            "marginBottom": cfg.page_title_margin_bottom,
        }
        children = [html.H3(cfg.title, style=title_style)]

        # ── Optional commentary paragraph ────────────────────────────────────
        # Rendered as Markdown so user can embed **bold**, links, lists.
        if section.commentary:
            commentary_style = {
                "fontFamily": cfg.commentary_font_family,
                "color": cfg.commentary_font_color,
                "fontSize": cfg.commentary_font_size,
                "lineHeight": cfg.commentary_line_height,
                "marginBottom": cfg.commentary_margin_bottom,
                "maxWidth": cfg.commentary_max_width,
            }
            children.append(
                # note that dcc is dash_core_components, imported as dcc at the top
                # and this is a class from the dash module that allows rendering markdown text in the app
                dcc.Markdown(section.commentary, style=commentary_style)
            )

        # ── Optional stack-mode dropdown ─────────────────────────────────────
        # Lets user switch line-plot ↔ stacked-area on the fly. Wired via
        # callback in `_register_section_callback`.
        if cfg.show_stack_mode_control:
            children.extend([
                html.Div("Stack Mode"),
                dcc.Dropdown(
                    id=cfg.stack_mode_control_id,
                    options=[
                        {"label": "Lines (No Stacking)", "value": "none"},
                        {"label": "Stacked", "value": "stack"},
                        {"label": "Stacked Split Sign (+/-)", "value": "stack_split_sign"},
                    ],
                    value=cfg.stack_mode,
                    clearable=False,
                ),
            ])

        # ── Optional range slider ────────────────────────────────────────────
        # Slider value = [left_idx, right_idx] integer positions into df.
        # Marks evenly sampled along df length; rotated -90° so dense
        # date labels don't overlap. Callback turns slider value into
        # `time_range` arg of `_build_level_figure`.
        if section.show_slider:
            mark_positions = np.linspace(0, len(df) - 1, cfg.num_marks, dtype=int)
            children.extend([
                html.Br(), html.Br(), html.Br(), html.Br(),
                dcc.RangeSlider(
                    id=cfg.slider_id,
                    min=0,
                    max=len(df) - 1,
                    marks={
                        int(pos): {
                            "label": _format_time_label(df["time"].iloc[pos], cfg),
                            "style": {
                                "transform": "rotate(-90deg)",
                                "transformOrigin": "top left",
                                "whiteSpace": "nowrap",
                                "textAlign": "center",
                                "marginTop": "-20px",
                                "marginLeft": "-8px",
                            },
                        }
                        for pos in mark_positions
                    },
                    # initial starting values for start and end of slider are set so that
                    # an inital graph can be rendered even before a user interacts
                    value=[0, len(df) - 1],
                    tooltip={"placement": "bottom", "always_visible": True},
                ),
                html.Br(),
            ])

        # ── Graph (always present, always last) ──────────────────────────────
        # Two paths:
        #   1. Interactive section (slider or dropdown present) → start empty
        #      `go.Figure()`. Callback in `_register_section_callback` fills
        #      it after layout mounts in browser.
        #   2. Static section (no inputs) → build figure now using
        #      `section.time_window` to pre-filter date range. No callback
        #      will fire, so figure must be ready at layout-build time.
        static_fig = None
        if not section.show_slider and not cfg.show_stack_mode_control:
            static_fig = _build_level_figure(df, cfg, time_range=section.time_window)

        # if figure is not set to be static, we initiallise a wholly new figure
        children.append(
            dcc.Graph(
                id=cfg.graph_id,       # unique per section → callback target
                figure=static_fig if static_fig is not None else go.Figure(),
                style={"height": f"{cfg.fig_height}px"},
            )
        )
        return children

    # ─────────────────────────────────────────────────────────────────────────
    def _register_section_callback(self, app, section: "SectionConfig") -> None:
        """Wire slider + optional stack-mode dropdown → graph for one section.

        How Dash callbacks work
        -----------------------
        A callback is a function the browser triggers automatically whenever
        one of its declared `Input(...)` components changes value. Dash routes
        the function's return value into the declared `Output(...)`.

        Component identity = string IDs
        -------------------------------
        Dash does NOT use Python object references to find components — it uses
        the string `id=` you set when constructing each component. Here:

            cfg.graph_id              e.g. "level-graph"      (Graph)
            cfg.slider_id             e.g. "time-range"       (RangeSlider)
            cfg.stack_mode_control_id e.g. "stack-mode"       (Dropdown)

        These are plain strings on the cfg dataclass (NOT numeric handles).
        For multi-section apps the cfg of each section must give them unique
        IDs (e.g. `replace(cfg, graph_id="level-graph-2", slider_id=...)`)
        otherwise Dash will throw a duplicate-ID error at build time.

        `Output(cfg.graph_id, "figure")` means: write the callback's return
        value into the component whose id == cfg.graph_id, into its `figure`
        prop. `Input(cfg.slider_id, "value")` means: fire this callback
        whenever the component with id == cfg.slider_id changes its `value`
        prop, and pass that value as the first positional arg.

        Where the slider's "current value" lives
        ---------------------------------------
        The RangeSlider stores its current `[left_idx, right_idx]` purely in
        the browser's Dash component state — there is NO Python attribute on
        LevelDashApp that tracks it. Each time the user drags, Dash sends the
        new value to the server, calls `_cb(time_range, ...)`, and discards
        the value once the figure is returned. The server is stateless w.r.t.
        slider position.

        Initial render — does a graph appear before any user interaction?
        -----------------------------------------------------------------
        Yes. Two mechanisms:

          1. RangeSlider construction sets `value=[0, len(df) - 1]` (see
             `_build_section_children`) — the default range covers the whole
             df from start to end.
          2. Dash fires every callback ONCE on page load with the initial
             values of its Inputs. So `_cb([0, len(df)-1])` runs immediately,
             builds the full-range figure, and writes it into the Graph
             before the user touches anything.

          For sections with no inputs (`show_slider=False` and no dropdown),
          the figure is built eagerly inside `_build_section_children` using
          `section.time_window`, and no callback is registered — see the
          `else:` branch below.
        """
        cfg = section.cfg
        df = section.df

        # ── Case 1: slider AND stack-mode dropdown both present ──────────────
        # Callback has 2 Inputs → handler takes 2 args in the order declared.
        # On any change to either control, rebuild figure with both current
        # values. `replace(cfg, stack_mode=stack_mode)` makes a one-off cfg
        # copy with the new stack mode (cfg is frozen, can't mutate in place).
        if section.show_slider and cfg.show_stack_mode_control:
            @app.callback(
                Output(cfg.graph_id, "figure"),               # write target
                Input(cfg.slider_id, "value"),                # arg 1
                Input(cfg.stack_mode_control_id, "value"),    # arg 2
            )
            def _cb(time_range, stack_mode):
                # time_range = [left_idx, right_idx] from RangeSlider
                # stack_mode = "none" | "stack" | "stack_split_sign"
                return _build_level_figure(
                    df, replace(cfg, stack_mode=stack_mode), time_range=time_range
                )

        # ── Case 2: slider only ──────────────────────────────────────────────
        # Most common path. Single Input → single-arg handler.
        elif section.show_slider:
            @app.callback(Output(cfg.graph_id, "figure"), Input(cfg.slider_id, "value"))
            def _cb(time_range):
                # time_range arrives as [left_idx, right_idx]. On first load
                # Dash auto-fires this with the slider's default value
                # ([0, len(df)-1]) so the graph populates before any drag.
                return _build_level_figure(df, cfg, time_range=time_range)

        # ── Case 3: dropdown only, no slider ─────────────────────────────────
        elif cfg.show_stack_mode_control:
            @app.callback(
                Output(cfg.graph_id, "figure"),
                Input(cfg.stack_mode_control_id, "value"),
            )
            def _cb(stack_mode):
                return _build_level_figure(df, replace(cfg, stack_mode=stack_mode))

        # ── Case 4: no inputs → figure already injected at layout-build time.
        # See `_build_section_children`: the static branch builds the figure
        # with `time_range=section.time_window` and stuffs it into the Graph's
        # `figure=` prop directly. No callback to register.
        # else: pass

    # ─────────────────────────────────────────────────────────────────────────
    def _update_plot(self, time_range, stack_mode_override=None):
        """Rebuild the figure for the current slider position and (optionally) stack mode.

        Called by the Dash callback every time the slider or stack-mode dropdown changes.

        Parameters
        ----------
        time_range         : [left_idx, right_idx] emitted by the RangeSlider component
        stack_mode_override: when the stack-mode dropdown is present, its current value
                             is passed here so the figure is rebuilt with that mode.
        """
        # If a stack-mode override is supplied, create a temporary copy of cfg
        # with only stack_mode changed (replace() returns a new frozen instance).
        # Otherwise use self.cfg unchanged.
        cfg_for_plot = self.cfg if stack_mode_override is None else replace(self.cfg, stack_mode=stack_mode_override)

        # Delegate all rendering to the shared figure builder.
        return _build_level_figure(self.df, cfg_for_plot, time_range=time_range)

    # ─────────────────────────────────────────────────────────────────────────
    def run(self, *, debug: bool = False, port: Optional[int] = None, use_reloader: bool = False) -> None:
        """Build the app (if not already built) and start the Dash development server.

        Blocks the calling thread until the server is stopped (Ctrl-C).

        Parameters
        ----------
        debug        : enable Dash hot-reloading and error overlay
        port         : override cfg.port for this run only
        use_reloader : enable Werkzeug file watcher (usually off in notebooks)
        """
        # Safely build the app if not already done.
        if self.app is None:
            self.build()
        self.app.run(
            debug=debug,
            port=(self.cfg.port if port is None else port),
            use_reloader=use_reloader,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SCRIPT ENTRY POINT (manual testing only)
# ─────────────────────────────────────────────────────────────────────────────

# Optional: allow running this file directly as a script for quick manual testing
if __name__ == "__main__":
    # Put a tiny demo here if you want, but DON'T rely on notebook variables.
    # Example: load a CSV, create df, then:
    # cfg = TimeSeriesAppConfig(cols_of_interest=[...], reindex=False)
    # app = make_level_app(df, cfg)
    # run_app(app, debug=True, port=8050)
    pass


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC CONVENIENCE FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def make_level_figure(
    df,
    cols_of_interest=None,
    cfg: Optional[TimeSeriesAppConfig] = None,
    label_map=None,
    colour_map=None,
    opacity_map=None,
    colour_groups=None,
    color_map=None,
    reindex=True,
    figure_title="Levels over Selected Period",
    fig_height=800,
    title="Comparison of Levels",
    auto_label_map=True,
    auto_colour_map=True,
    colour_start="cyan",
    colour_end="purple",
    close_hour=15,
    close_minute=50,
    show_legend=True,
    x_tick_label_mode="full",
    x_tick_label_format=None,
    default_opacity=1.0,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font_color='white',
    title_font_color=None,
    axis_title_font_color=None,
    axis_tick_font_color=None,
    legend_colour='white',
    hover_label_font_color: Optional[str] = 'black',
    hover_label_bgcolor: Optional[str] = 'white',
    stack_mode="none",
    show_overall_line=False,
    overall_col=None,
    overall_label="Overall",
    overall_colour="black",
    overall_width=3.0,
    overall_opacity=1.0,
    show_stack_mode_control=False,
    stack_mode_control_id="stack-mode-control",
    show_plotly_stack_mode_buttons=False,
    plotly_stack_mode_buttons_x=0.0,
    plotly_stack_mode_buttons_y=1.18,
    time_window=None,
    *,
    base_cfg: Optional[BaseFigureConfig] = None,
    type_cfg: Optional[TimeSeriesFigureConfig] = None,
):
    """Build and return a Plotly figure without spinning up a Dash server.

    Supports two calling styles for backwards compatibility:

        # Old style — pass individual keyword args:
        make_level_figure(df, cols_of_interest=["col_a"], reindex=False)

        # New style — pass a pre-built config:
        cfg = TimeSeriesAppConfig(cols_of_interest=["col_a"], reindex=False)
        make_level_figure(df, cfg=cfg, time_window=(start_dt, end_dt))

        # Shorthand — cfg as the second positional argument:
        make_level_figure(df, cfg)

    Parameters
    ----------
    df           : DataFrame with a 'time' column (datetime) and the data columns.
    time_window  : optional (start, end) pair to pre-filter the data before plotting.
                   Can be integer row indices or datetime-like values.
    (all other parameters mirror TimeSeriesAppConfig fields)

    Returns
    -------
    go.Figure — ready to display with fig.show() or embed in a notebook cell.
    """

    # ── Detect shorthand: cfg passed as second positional argument ────────────
    # When called as make_level_figure(df, my_cfg), cols_of_interest receives
    # the TimeSeriesAppConfig object; remap it to the cfg parameter.
    if isinstance(cols_of_interest, TimeSeriesAppConfig):
        if cfg is not None:
            raise ValueError("Pass config only once: either cfg=... or second positional config")
        cfg = cols_of_interest
        # cols_of_interest is now stale — its value lives inside cfg.
        cols_of_interest = None

    # ── Build cfg from keyword args when no config / tiered cfg was supplied ──
    if cfg is None and base_cfg is None and type_cfg is None:
        if cols_of_interest is None:
            raise ValueError("Provide either cols_of_interest, cfg=TimeSeriesAppConfig(...), or base_cfg/type_cfg")

        # Accept both US spelling (color_map) and UK spelling (colour_map).
        resolved_colour_map = colour_map if colour_map is not None else color_map

        # Collect the flat keyword overrides into a dict so _resolve can apply
        # them on top of a default TimeSeriesAppConfig.
        flat_overrides = dict(
            cols_of_interest=cols_of_interest,
            reindex=reindex,
            label_map=label_map,
            colour_map=resolved_colour_map,
            opacity_map=opacity_map,
            colour_groups=colour_groups,
            auto_label_map=auto_label_map,
            auto_colour_map=auto_colour_map,
            colour_start=colour_start,
            colour_end=colour_end,
            default_opacity=default_opacity,
            title=title,
            figure_title=figure_title,
            fig_height=fig_height,
            close_hour=close_hour,
            close_minute=close_minute,
            show_legend=show_legend,
            x_tick_label_mode=x_tick_label_mode,
            x_tick_label_format=x_tick_label_format,
            paper_bgcolor=paper_bgcolor,
            plot_bgcolor=plot_bgcolor,
            font_color=font_color,
            title_font_color=title_font_color,
            axis_title_font_color=axis_title_font_color,
            axis_tick_font_color=axis_tick_font_color,
            stack_mode=stack_mode,
            show_overall_line=show_overall_line,
            overall_col=overall_col,
            overall_label=overall_label,
            overall_colour=overall_colour,
            overall_width=overall_width,
            overall_opacity=overall_opacity,
            show_stack_mode_control=show_stack_mode_control,
            stack_mode_control_id=stack_mode_control_id,
            show_plotly_stack_mode_buttons=show_plotly_stack_mode_buttons,
            plotly_stack_mode_buttons_x=plotly_stack_mode_buttons_x,
            plotly_stack_mode_buttons_y=plotly_stack_mode_buttons_y,
        )
        cfg = _resolve(
            single_cfg=None,
            base_cfg=None,
            type_cfg=None,
            composite_cls=TimeSeriesAppConfig,
            **flat_overrides,
        )
    else:
        # tiered or pre-built cfg path
        cfg = _resolve(
            single_cfg=cfg,
            base_cfg=base_cfg,
            type_cfg=type_cfg,
            composite_cls=TimeSeriesAppConfig,
        )

    # Resolve label/colour/opacity maps regardless of which calling style was used.
    cfg = _normalize_config(cfg)
    # Structural validation — raises ValueError early with a clear message.
    _validate_inputs(df, cfg)

    # ── Dispatch to the appropriate figure builder ────────────────────────────
    if cfg.show_plotly_stack_mode_buttons:
        # Embed all three stack modes in one figure with native Plotly buttons.
        return _build_level_figure_with_plotly_mode_buttons(df, cfg, time_range=time_window)
    # Default: build a single figure for the currently configured stack_mode.
    return _build_level_figure(df, cfg, time_range=time_window)


# ─────────────────────────────────────────────────────────────────────────────
# DISTRIBUTION FIGURE (Histogram + KDE)
# ─────────────────────────────────────────────────────────────────────────────

def make_distrib_figure(
    data,
    *,
    cfg: Optional[HistogramAppConfig] = None,
    base_cfg: Optional[BaseFigureConfig] = None,
    type_cfg: Optional[HistogramFigureConfig] = None,
    **overrides,
) -> go.Figure:
    """Build a Plotly histogram + KDE figure with optional summary-stat vertical lines.

    Two calling styles:
        # Pattern A — fully-built composite config:
        make_distrib_figure(data, cfg=HistogramAppConfig(hist_colour="magenta"))

        # Pattern B — tiered base + type + overrides:
        make_distrib_figure(
            data,
            base_cfg=BaseFigureConfig(font_color="white"),
            type_cfg=HistogramFigureConfig(hist_colour="cyan", kde_colour="blue"),
            vline_stats=("mean",),
        )
    """
    cfg = _resolve(
        single_cfg=cfg,
        base_cfg=base_cfg,
        type_cfg=type_cfg,
        composite_cls=HistogramAppConfig,
        **overrides,
    )

    # Font colour resolution (mirrors make_level_figure).
    title_font_color = cfg.title_font_color or cfg.font_color
    axis_title_font_color = cfg.axis_title_font_color or cfg.font_color
    axis_tick_font_color = cfg.axis_tick_font_color or cfg.font_color

    # Common layout block (shared across the empty-data guard and the normal path).
    def _apply_layout(fig: go.Figure) -> None:
        fig.update_layout(
            title=cfg.figure_title,
            xaxis_title=cfg.xaxis_title,
            yaxis_title=cfg.yaxis_title,
            showlegend=cfg.show_legend,
            legend=dict(font=dict(color=cfg.legend_colour)),
            hoverlabel=dict(font=dict(color=cfg.hover_label_font_color), bgcolor=cfg.hover_label_bgcolor),
            hovermode="closest",
            height=cfg.fig_height,
            paper_bgcolor=cfg.paper_bgcolor,
            plot_bgcolor=cfg.plot_bgcolor,
            font=(dict(color=cfg.font_color) if cfg.font_color is not None else None),
            title_font=(dict(color=title_font_color) if title_font_color is not None else None),
            xaxis=dict(
                title=dict(font=(dict(color=axis_title_font_color) if axis_title_font_color is not None else None)),
                tickfont=(dict(color=axis_tick_font_color) if axis_tick_font_color is not None else None),
                gridcolor=cfg.gridcolor,
                zerolinecolor=cfg.zerolinecolor,
                showgrid=cfg.xaxis_showgrid,
            ),
            yaxis=dict(
                title=dict(font=(dict(color=axis_title_font_color) if axis_title_font_color is not None else None)),
                tickfont=(dict(color=axis_tick_font_color) if axis_tick_font_color is not None else None),
                gridcolor=cfg.gridcolor,
                zerolinecolor=cfg.zerolinecolor,
                showgrid=cfg.yaxis_showgrid,
            ),
        )

    # ── Data prep ─────────────────────────────────────────────────────────────
    arr = np.asarray(data, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        fig = go.Figure()
        _apply_layout(fig)
        return fig

    fig = go.Figure()

    # ── Histogram ─────────────────────────────────────────────────────────────
    fig.add_trace(
        go.Histogram(
            x=arr,
            histnorm=cfg.histnorm,
            nbinsx=cfg.nbinsx,
            marker_color=cfg.hist_colour,
            opacity=cfg.hist_opacity,
            name=cfg.hist_name,
        )
    )

    # ── KDE overlay ───────────────────────────────────────────────────────────
    kde = gaussian_kde(arr, bw_method=cfg.kde_bandwidth)
    kde_x = np.linspace(arr.min(), arr.max(), cfg.kde_points)
    fig.add_trace(
        go.Scatter(
            x=kde_x,
            y=kde(kde_x),
            mode="lines",
            fill=cfg.kde_fill,
            line=dict(color=cfg.kde_colour),
            opacity=cfg.kde_opacity,
            name=cfg.kde_name,
        )
    )

    # ── Summary-stat vertical lines ───────────────────────────────────────────
    if cfg.vline_stats:
        stat_dispatch = {
            "min": lambda a: float(np.min(a)),
            "q1": lambda a: float(np.quantile(a, 0.25)),
            "median": lambda a: float(np.median(a)),
            "mean": lambda a: float(np.mean(a)),
            "q3": lambda a: float(np.quantile(a, 0.75)),
            "max": lambda a: float(np.max(a)),
        }
        vline_colour_map = cfg.vline_colour_map or {}
        for stat in cfg.vline_stats:
            if stat not in stat_dispatch:
                continue
            x_val = stat_dispatch[stat](arr)
            colour = vline_colour_map.get(stat, cfg.vline_default_colour)
            annotation_text = stat if cfg.vline_annotate else None
            fig.add_vline(
                x=x_val,
                line=dict(color=colour, dash=cfg.vline_dash, width=cfg.vline_width),
                annotation_text=annotation_text,
            )

    _apply_layout(fig)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# PORT-MANAGEMENT UTILITIES (Windows only — uses netstat / taskkill)
# ─────────────────────────────────────────────────────────────────────────────

def pids_on_port(port: int, listening_only: bool = True) -> Sequence[int]:
    """Return process IDs bound to a TCP port on Windows.

    Uses netstat (available by default on Windows) to avoid extra dependencies.

    Parameters
    ----------
    port
        TCP local port number to inspect.
    listening_only
        If True, include only LISTENING sockets (ignores ESTABLISHED / TIME_WAIT etc.).
    """
    if not isinstance(port, int) or port <= 0 or port > 65535:
        raise ValueError("port must be an integer in [1, 65535]")

    # Run netstat and capture its text output.
    # -a → show all connections, -n → numeric addresses, -o → include PID, -p tcp → TCP only.
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Failed to inspect sockets with netstat: {stderr}")

    pids = set()
    # Build the suffix we search for in the local address column (e.g. ":8050").
    target_suffix = f":{port}"

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        # netstat TCP lines start with "TCP"; skip headers and UDP lines.
        if not line.startswith("TCP"):
            continue

        parts = line.split()
        # Expected columns: Proto  LocalAddress  ForeignAddress  State  PID
        if len(parts) < 5:
            continue

        local_addr = parts[1]   # e.g. "0.0.0.0:8050"
        state = parts[3]        # e.g. "LISTENING"
        pid_text = parts[4]     # e.g. "12345"

        # Check whether this line's local address matches the target port.
        if not local_addr.endswith(target_suffix):
            continue
        # Optionally restrict to LISTENING sockets only.
        if listening_only and state.upper() != "LISTENING":
            continue

        try:
            pids.add(int(pid_text))
        except ValueError:
            # pid_text was not a valid integer — skip malformed lines.
            continue

    return sorted(pids)


def kill_processes_on_port(port: int, force: bool = True, dry_run: bool = False, listening_only: bool = True) -> Mapping[str, Any]:
    """Kill all processes bound to a TCP port on Windows using taskkill.

    Parameters
    ----------
    port           : TCP port to target.
    force          : pass /F to taskkill (force-terminate without prompting).
    dry_run        : if True, return the summary without actually killing anything.
    listening_only : if True, only target LISTENING sockets (see pids_on_port).

    Returns
    -------
    dict with keys: port, pids, killed, failed, dry_run
    """
    # Discover which PIDs are currently bound to the given port.
    pids = list(pids_on_port(port, listening_only=listening_only))

    # Initialise the result summary that callers can inspect.
    summary = {
        "port": port,
        "pids": pids,
        "killed": [],
        "failed": [],
        "dry_run": dry_run,
    }

    # In dry-run mode, or when no PIDs were found, return early.
    if dry_run or not pids:
        return summary

    for pid in pids:
        # Build the taskkill command; /F forces termination.
        cmd = ["taskkill", "/PID", str(pid)]
        if force:
            cmd.append("/F")

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            summary["killed"].append(pid)
        else:
            # Capture stderr/stdout so the caller can diagnose failures.
            summary["failed"].append(
                {
                    "pid": pid,
                    "stderr": (result.stderr or "").strip(),
                    "stdout": (result.stdout or "").strip(),
                }
            )

    return summary


def stop_dash_ports(
    ports: Optional[Sequence[int]] = None,
    *,
    port_start: int = 8050,
    port_end: int = 8060,
    force: bool = True,
    dry_run: bool = False,
    listening_only: bool = True,
) -> Mapping[str, Any]:
    """Stop processes listening on common Dash ports (convenience wrapper).

    By default targets the inclusive range [8050, 8060] — the ports Dash uses
    when multiple app instances are started in sequence.

    Parameters
    ----------
    ports
        Explicit list of ports to inspect. If None, uses inclusive range [port_start, port_end].
    port_start
        Start of the default port range (used when ``ports`` is None).
    port_end
        End of the default port range (used when ``ports`` is None).
    force
        Whether to force-terminate matched processes (taskkill /F).
    dry_run
        If True, only report what would be terminated without actually killing.
    listening_only
        If True, only target LISTENING sockets.

    Returns
    -------
    dict summarising all ports checked and any PIDs killed or failed.
    """
    # Build the list of ports to inspect.
    if ports is None:
        if port_start > port_end:
            raise ValueError("port_start must be <= port_end")
        port_list = list(range(int(port_start), int(port_end) + 1))
    else:
        port_list = [int(p) for p in ports]

    results = []         # per-port kill summaries
    killed_pids = set()  # deduplicated set of all successfully killed PIDs
    failed = []          # flat list of failure dicts across all ports

    # Attempt to kill processes on each port in turn.
    for port in port_list:
        result = kill_processes_on_port(
            port,
            force=force,
            dry_run=dry_run,
            listening_only=listening_only,
        )
        results.append(result)
        killed_pids.update(result.get("killed", []))
        failed.extend(result.get("failed", []))

    # Return a consolidated summary across all ports.
    return {
        "ports_checked": port_list,
        "dry_run": dry_run,
        "results": results,
        "killed_pid_count": len(killed_pids),
        "killed_pids": sorted(killed_pids),
        "failed": failed,
    }