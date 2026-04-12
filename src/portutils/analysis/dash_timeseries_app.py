# dash_timeseries_app.py

# imports

from __future__ import annotations
from dataclasses import dataclass, replace
try:
    from dash import Dash, html, dcc, Input, Output
except ImportError:  # Allow standalone Plotly figure use without Dash installed.
    Dash = None
    html = None
    dcc = None
    Input = None
    Output = None


import plotly.graph_objects as go

import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
import subprocess
from typing import Mapping, Sequence, Optional, Any




DEFAULT_LABEL_MAP = {
    "spx_level_yf": "S&P 500 Index<br>Level YF",
    "spx_period_return_yf": "S&P 500 Index<br>Return YF",
    "es_level_yf": "ES Futures<br>Settlement YF",
    "es_period_return_yf": "ES Futures<br>Return YF",
    "spx_level": "S&P 500 Index<br>Level CIQ",
    "spx_period_return": "S&P 500 Index<br>Return CIQ",
    "spy_period_return": "SPY ETF<br>Return CIQ",
}

DEFAULT_COLOUR_MAP = {
    "spx_level_yf": "purple",
    "es_level_yf": "blue",
    "spx_level": "cyan",
}


@dataclass(frozen=True)
class ColourGroupConfig:
    cols: Sequence[str]
    colour: Optional[str] = None
    opacity: Optional[float] = None
    start: Optional[str] = None
    end: Optional[str] = None
    name: str = "gradient"


@dataclass(frozen=True)
class LevelAppConfig:
    reindex: bool = True
    cols_of_interest: Optional[Sequence[str]] = None

    # allow user to input pre-made label map, generate automatically, or 
    # stick with defaults (if enters none)
    label_map: Mapping[str, str] = None  # filled in by normalize_config
    auto_label_map: bool = True
    
    # allow user to input pre-made colour map, generate automatically, or 
    # stick with defaults (if enters none)
    colour_map: Mapping[str, str] = None  # filled in by normalize_config
    opacity_map: Mapping[str, float] = None  # filled in by normalize_config
    colour_groups: Optional[Sequence[Any]] = None
    auto_colour_map: bool = True
    colour_start: str = "cyan"
    colour_end: str = "purple"
    default_opacity: float = 1.0

    

    # default UI settings
    title: str = "Comparison of Levels"
    figure_title: str = "Levels Reindexed to 100"
    graph_id: str = "level-plot"
    slider_id: str = "time-range-slider"
    num_marks: int = 20
    fig_height: int = 800
    port: int = 8050
    close_hour: int = 15
    close_minute: int = 50

    # background colours — transparent by default so the figure blends with the notebook theme
    paper_bgcolor: str = "rgba(0,0,0,0)"
    plot_bgcolor: str = "rgba(0,0,0,0)"

    # typography colours (set to white for dark backgrounds)
    font_color: Optional[str] = None
    title_font_color: Optional[str] = None
    axis_title_font_color: Optional[str] = None
    axis_tick_font_color: Optional[str] = None

    # plotting switches
    show_legend: bool = True
    x_tick_label_mode: str = "full"  # supported: full, year_month
    x_tick_label_format: Optional[str] = None  # optional explicit strftime format

    # stacked contribution plotting options
    # stack_mode supported values:
    # - "none": draw independent lines (default)
    # - "stack": standard stacked contributions
    # - "stack_split_sign": stack positives above zero and negatives below zero
    stack_mode: str = "none"
    show_overall_line: bool = False
    overall_col: Optional[str] = None
    overall_label: str = "Overall"
    overall_colour: str = "white"
    overall_width: float = 3.0
    overall_opacity: float = 1.0

    # optional UI control for toggling stack mode in a single chart
    show_stack_mode_control: bool = False
    stack_mode_control_id: str = "stack-mode-control"

    # optional Plotly-native controls to switch stack mode without Dash
    show_plotly_stack_mode_buttons: bool = False
    plotly_stack_mode_buttons_x: float = 0.0
    plotly_stack_mode_buttons_y: float = 1.18


def _format_time_label(ts, cfg: LevelAppConfig) -> str:
    """Format datetime labels for axis ticks and slider marks."""
    if cfg.x_tick_label_format:
        return ts.strftime(cfg.x_tick_label_format)

    if cfg.x_tick_label_mode == "year_month":
        return ts.strftime("%Y-%m")

    # default "full"
    return ts.strftime("%y-%m-%d %H:%M")

class AutoLabelMap:
    """
    Create a dict mapping column names -> labels
    """

    def __init__(self, cols, start="cyan", end="purple", name="gradient"):
        self.cols = list(cols)

        # filled by run()
        self.label_map = None

    def run(self):
        # for each column of interest, create a label by replacing underscores 
        # with spaces, titleizing words, and putting a line break at most every 16 
        # characters, if not, right at the last space before 16 chars
        label_map = {}
        for col in self.cols:
            words = col.replace("_", " ").title().split(" ")
            label = ""
            current_line = ""
            for word in words:
                if len(current_line) + len(word) + 1 <= 16:
                    if current_line:
                        current_line += " " + word
                    else:
                        current_line = word
                else:
                    if label:
                        label += "<br>" + current_line
                    else:
                        label = current_line
                    current_line = word
            if current_line:
                if label:
                    label += "<br>" + current_line
                else:
                    label = current_line
            label_map[col] = label
        
        self.label_map = label_map
        return self.label_map

class GradientColourMap:
    """
    Create a dict mapping column names -> hex colours using a linear gradient.

    Usage:
        gen = GradientColourMap(cols, start="cyan", end="purple")
        gen.run()
        colour_map = gen.colour_map
    """

    def __init__(self, cols, start="cyan", end="purple", name="gradient"):
        self.cols = list(cols)
        self.start = start
        self.end = end
        self.name = name

        # filled by run()
        self.colour_map = None

    def run(self):
        n = len(self.cols)
        if n == 0:
            self.colour_map = {}
            return self.colour_map

        cmap = mcolors.LinearSegmentedColormap.from_list(self.name, [self.start, self.end])
        positions = np.linspace(0, 1, n)
        colours = [mcolors.to_hex(cmap(p)) for p in positions]

        self.colour_map = dict(zip(self.cols, colours))
        return self.colour_map


def _normalise_colour_group(group: Any, idx: int, cols_of_interest: Optional[Sequence[str]]) -> ColourGroupConfig:
    """Accept dicts or ColourGroupConfig instances for grouped colour definitions."""
    if isinstance(group, ColourGroupConfig):
        spec = group
    elif isinstance(group, dict):
        cols = group.get("cols", group.get("columns"))
        spec = ColourGroupConfig(
            cols=cols,
            colour=group.get("colour", group.get("color")),
            opacity=group.get("opacity", group.get("alpha")),
            start=group.get("start"),
            end=group.get("end"),
            name=group.get("name", f"group_{idx}"),
        )
    else:
        raise ValueError("colour_groups items must be ColourGroupConfig objects or dicts")

    if spec.cols is None or len(spec.cols) == 0:
        raise ValueError("Each colour group must define a non-empty 'cols' list")

    if cols_of_interest is not None:
        missing = [c for c in spec.cols if c not in cols_of_interest]
        if missing:
            raise ValueError(f"These colour-group columns are missing from cols_of_interest: {missing}")

    if spec.colour is None and spec.start is None and spec.end is None:
        raise ValueError("Each colour group must define either 'colour' or a 'start'/'end' gradient")

    return spec


def _build_group_colour_map(cfg: LevelAppConfig) -> Mapping[str, str]:
    """Build colours for any explicitly configured groups."""
    if not cfg.colour_groups:
        return {}

    colour_map = {}
    for idx, raw_group in enumerate(cfg.colour_groups):
        group = _normalise_colour_group(raw_group, idx, cfg.cols_of_interest)

        if group.colour is not None:
            colour_map.update({col: group.colour for col in group.cols})
            continue

        start = cfg.colour_start if group.start is None else group.start
        end = start if group.end is None else group.end
        colour_map.update(
            GradientColourMap(group.cols, start=start, end=end, name=group.name).run()
        )

    return colour_map


def _build_group_opacity_map(cfg: LevelAppConfig) -> Mapping[str, float]:
    """Build opacity values for any explicitly configured groups."""
    if not cfg.colour_groups:
        return {}

    opacity_map = {}
    for idx, raw_group in enumerate(cfg.colour_groups):
        group = _normalise_colour_group(raw_group, idx, cfg.cols_of_interest)
        if group.opacity is not None:
            opacity_map.update({col: float(group.opacity) for col in group.cols})
    return opacity_map


def _build_colour_map(cfg: LevelAppConfig) -> Mapping[str, str]:
    """Resolve colours using defaults/auto generation, then group overrides, then explicit overrides."""
    if cfg.auto_colour_map and cfg.cols_of_interest:
        colour_map = GradientColourMap(
            cfg.cols_of_interest,
            start=cfg.colour_start,
            end=cfg.colour_end,
            name="auto_gradient",
        ).run()
    else:
        colour_map = {c: DEFAULT_COLOUR_MAP[c] for c in (cfg.cols_of_interest or []) if c in DEFAULT_COLOUR_MAP}

    colour_map.update(_build_group_colour_map(cfg))

    if cfg.colour_map is not None:
        colour_map.update(cfg.colour_map)

    return colour_map


def _build_opacity_map(cfg: LevelAppConfig) -> Mapping[str, float]:
    """Resolve opacities using default, then group overrides, then explicit overrides."""
    opacity_map = {
        col: float(cfg.default_opacity)
        for col in (cfg.cols_of_interest or [])
    }

    opacity_map.update(_build_group_opacity_map(cfg))

    if cfg.opacity_map is not None:
        opacity_map.update({k: float(v) for k, v in cfg.opacity_map.items()})

    return opacity_map


def _filter_df_by_time_window(df, time_window=None):
    """Filter dataframe by either slider-style integer range or explicit datetime window."""
    if time_window is None:
        return df.copy()

    if isinstance(time_window, (list, tuple)) and len(time_window) == 2:
        left, right = time_window
        if isinstance(left, (int, np.integer)) and isinstance(right, (int, np.integer)):
            return df.iloc[left:right + 1].copy()

        start = pd.to_datetime(left)
        end = pd.to_datetime(right)
        return df[(df["time"] >= start) & (df["time"] <= end)].copy()

    raise ValueError("time_window must be None or a 2-item tuple/list of integer indices or datetime-like bounds")





def _normalize_config(cfg: LevelAppConfig) -> LevelAppConfig:
    # Avoid mutable defaults and allow user override while keeping sane defaults
    # take user input label map if provided
    if cfg.label_map is not None:
        label_map = cfg.label_map
    # if no label_map is explicitly provided...
    else:
        # if user want it to be auto-generated, do so
        if cfg.auto_label_map and cfg.cols_of_interest:
            label_map = AutoLabelMap(
                cfg.cols_of_interest
            ).run()
        # else, stick with defaults
        else:
            label_map = DEFAULT_LABEL_MAP

    colour_map = _build_colour_map(cfg)
    opacity_map = _build_opacity_map(cfg)
    # return new config with updated maps
    # the **{ **... } syntax unpacks the original config's fields
        # where the first ** unpacks the dict,
        # and the second ** allows us to override specific fields
    # note that the __dict__ attribute of a dataclass instance gives a dict of its fields
    return LevelAppConfig(**{**cfg.__dict__, "label_map": label_map, "colour_map": colour_map, "opacity_map": opacity_map})


def _validate_inputs(df, cfg: LevelAppConfig) -> None:
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


def _build_level_figure(df, cfg: LevelAppConfig, time_range=None):
    filtered_df = _filter_df_by_time_window(df, time_range)

    if len(filtered_df) == 0:
        return go.Figure()

    num_ticks = min(20, len(filtered_df))
    tick_positions = np.linspace(0, len(filtered_df) - 1, num_ticks, dtype=int)

    fig = go.Figure()
    y_axis_title_value = "Level"

    transformed_series = {}
    for col in cfg.cols_of_interest:
        series = filtered_df[col]
        first_value = series.iloc[0]

        if cfg.reindex:
            y = (series / first_value) * 100
            y_axis_title_value = "Level (Base 100)"
        else:
            y = series

        transformed_series[col] = y

    if cfg.stack_mode == "none":
        for col in cfg.cols_of_interest:
            y = transformed_series[col]

            fig.add_trace(
                go.Scatter(
                    x=filtered_df.index,
                    y=y,
                    mode="lines",
                    name=cfg.label_map.get(col, col),
                    line=dict(width=2, color=cfg.colour_map.get(col)),
                    opacity=cfg.opacity_map.get(col, cfg.default_opacity),
                )
            )
    elif cfg.stack_mode == "stack":
        for col in cfg.cols_of_interest:
            y = transformed_series[col]

            fig.add_trace(
                go.Scatter(
                    x=filtered_df.index,
                    y=y,
                    mode="lines",
                    stackgroup="stacked",
                    name=cfg.label_map.get(col, col),
                    line=dict(width=1.0, color=cfg.colour_map.get(col)),
                    opacity=cfg.opacity_map.get(col, cfg.default_opacity),
                )
            )
    else:  # cfg.stack_mode == "stack_split_sign"
        for col in cfg.cols_of_interest:
            y = transformed_series[col]
            y_pos = y.clip(lower=0)
            y_neg = y.clip(upper=0)

            has_pos = bool((y_pos > 0).any())
            has_neg = bool((y_neg < 0).any())
            show_legend_once = True

            if has_pos:
                fig.add_trace(
                    go.Scatter(
                        x=filtered_df.index,
                        y=y_pos,
                        mode="lines",
                        stackgroup="positive",
                        name=cfg.label_map.get(col, col),
                        legendgroup=col,
                        showlegend=show_legend_once,
                        line=dict(width=0.0, color=cfg.colour_map.get(col)),
                        opacity=cfg.opacity_map.get(col, cfg.default_opacity),
                    )
                )
                show_legend_once = False

            if has_neg:
                fig.add_trace(
                    go.Scatter(
                        x=filtered_df.index,
                        y=y_neg,
                        mode="lines",
                        stackgroup="negative",
                        name=cfg.label_map.get(col, col),
                        legendgroup=col,
                        showlegend=show_legend_once,
                        line=dict(width=0.0, color=cfg.colour_map.get(col)),
                        opacity=cfg.opacity_map.get(col, cfg.default_opacity),
                    )
                )

    if cfg.show_overall_line:
        if cfg.overall_col is not None:
            overall_series = filtered_df[cfg.overall_col]
            if cfg.reindex:
                first_value = overall_series.iloc[0]
                overall_y = (overall_series / first_value) * 100
            else:
                overall_y = overall_series
        else:
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

    close_times = filtered_df[
        (filtered_df["time"].dt.hour == cfg.close_hour) &
        (filtered_df["time"].dt.minute == cfg.close_minute)
    ]
    for idx in close_times.index:
        fig.add_vline(x=idx, line=dict(color="grey", dash="dash", width=1), opacity=0.5)

    title_font_color = cfg.title_font_color or cfg.font_color
    axis_title_font_color = cfg.axis_title_font_color or cfg.font_color
    axis_tick_font_color = cfg.axis_tick_font_color or cfg.font_color

    fig.update_layout(
        title=cfg.figure_title,
        xaxis_title="Time",
        yaxis_title=y_axis_title_value,
        showlegend=cfg.show_legend,
        hovermode="x unified",
        height=cfg.fig_height,
        paper_bgcolor=cfg.paper_bgcolor,
        plot_bgcolor=cfg.plot_bgcolor,
        font=(dict(color=cfg.font_color) if cfg.font_color is not None else None),
        title_font=(dict(color=title_font_color) if title_font_color is not None else None),
        xaxis=dict(
            tickmode="array",
            tickvals=[filtered_df.index[i] for i in tick_positions],
            ticktext=[_format_time_label(filtered_df["time"].iloc[i], cfg) for i in tick_positions],
            tickangle=-90,
            title=dict(font=(dict(color=axis_title_font_color) if axis_title_font_color is not None else None)),
            tickfont=(dict(color=axis_tick_font_color) if axis_tick_font_color is not None else None),
        ),
        yaxis=dict(
            title=dict(font=(dict(color=axis_title_font_color) if axis_title_font_color is not None else None)),
            tickfont=(dict(color=axis_tick_font_color) if axis_tick_font_color is not None else None),
        ),
    )
    return fig


def _build_level_figure_with_plotly_mode_buttons(df, cfg: LevelAppConfig, time_range=None):
    """Build one Plotly figure containing all stack modes and in-figure toggle buttons."""
    mode_order = ["none", "stack", "stack_split_sign"]
    mode_label = {
        "none": "Lines",
        "stack": "Stacked",
        "stack_split_sign": "Split +/-",
    }

    active_mode = cfg.stack_mode if cfg.stack_mode in mode_order else "none"
    mode_figs = {
        mode: _build_level_figure(df, replace(cfg, stack_mode=mode), time_range=time_range)
        for mode in mode_order
    }

    combined_data = []
    mode_trace_indices = {}
    trace_idx = 0

    for mode in mode_order:
        start_idx = trace_idx
        for tr in mode_figs[mode].data:
            tr.visible = (mode == active_mode)
            combined_data.append(tr)
            trace_idx += 1
        mode_trace_indices[mode] = list(range(start_idx, trace_idx))

    fig = go.Figure(data=combined_data, layout=mode_figs[active_mode].layout)

    buttons = []
    for mode in mode_order:
        visible = [False] * len(combined_data)
        for idx in mode_trace_indices[mode]:
            visible[idx] = True
        buttons.append(
            dict(
                label=mode_label[mode],
                method="update",
                args=[{"visible": visible}],
            )
        )

    fig.update_layout(
        updatemenus=[
            dict(
                type="buttons",
                direction="right",
                buttons=buttons,
                x=cfg.plotly_stack_mode_buttons_x,
                y=cfg.plotly_stack_mode_buttons_y,
                xanchor="left",
                yanchor="top",
                showactive=True,
                active=mode_order.index(active_mode),
            )
        ]
    )

    return fig


class LevelDashApp:
    """
    Stateful builder for a Dash app.

    - __init__: store df + config as attributes (self.df, self.cfg, ...)
    - build(): create Dash app, layout, callbacks
    - _update_plot(): callback method that uses attributes
    """

    def __init__(self, df, config: LevelAppConfig):
        self.df = df
        self.cfg = _normalize_config(config)
        self._validate_inputs()

        # These get filled when you build the app
        self.app: Optional[Any] = None


    def _validate_inputs(self) -> None:
        df = self.df
        cfg = self.cfg

        if "time" not in df.columns:
            raise ValueError("df must contain a 'time' column.")
        # If you use pandas, this check can be improved with pandas.api.types
        if not np.issubdtype(df["time"].dtype, np.datetime64):
            raise ValueError("df['time'] must be datetime-like (convert via pd.to_datetime).")
        if len(df) < 2:
            raise ValueError("df must have at least 2 rows.")
        if cfg.cols_of_interest is None or len(cfg.cols_of_interest) == 0:
            raise ValueError("cols_of_interest must be provided (non-empty).")

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

    def build(self) -> Any:
        """Create the Dash app, attach layout + callbacks, return the Dash app."""
        if Dash is None:
            raise ImportError("dash is required to build the app. Install it to use LevelDashApp.")

        cfg = self.cfg
        df = self.df

        app = Dash(__name__)
        self.app = app

        mark_positions = np.linspace(0, len(df) - 1, cfg.num_marks, dtype=int)

        layout_children = [html.H3(cfg.title)]

        if cfg.show_stack_mode_control:
            layout_children.extend(
                [
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
                ]
            )

        layout_children.extend(
            [
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
                    value=[0, len(df) - 1],
                    tooltip={"placement": "bottom", "always_visible": True},
                ),
                html.Br(),
                dcc.Graph(id=cfg.graph_id, style={"height": f"{cfg.fig_height}px"}),
            ]
        )

        app.layout = html.Div(layout_children)

        # Callback defined inside build, but calls a method that uses self.df/self.cfg
        if cfg.show_stack_mode_control:
            @app.callback(
                Output(cfg.graph_id, "figure"),
                Input(cfg.slider_id, "value"),
                Input(cfg.stack_mode_control_id, "value"),
            )
            def _callback(time_range, selected_stack_mode):
                return self._update_plot(time_range, stack_mode_override=selected_stack_mode)
        else:
            @app.callback(Output(cfg.graph_id, "figure"), Input(cfg.slider_id, "value"))
            def _callback(time_range):
                return self._update_plot(time_range)

        return app

    def _update_plot(self, time_range, stack_mode_override=None):
        cfg_for_plot = self.cfg if stack_mode_override is None else replace(self.cfg, stack_mode=stack_mode_override)
        return _build_level_figure(self.df, cfg_for_plot, time_range=time_range)

    # safely build the app if not already done before running
            
    def run(self, *, debug: bool = False, port: Optional[int] = None, use_reloader: bool = False) -> None:
        if self.app is None:
            self.build()
        self.app.run(
            debug=debug,
            port=(self.cfg.port if port is None else port),
            use_reloader=use_reloader,
        )


# Optional: allow running this file directly as a script for quick manual testing
if __name__ == "__main__":
    # Put a tiny demo here if you want, but DON'T rely on notebook variables.
    # Example: load a CSV, create df, then:
    # cfg = LevelAppConfig(cols_of_interest=[...], reindex=False)
    # app = make_level_app(df, cfg)
    # run_app(app, debug=True, port=8050)
    pass



def make_level_figure(
    df,
    cols_of_interest=None,
    cfg: Optional[LevelAppConfig] = None,
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
    font_color=None,
    title_font_color=None,
    axis_title_font_color=None,
    axis_tick_font_color=None,
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
):
    # Backward compatibility:
    # - old style: make_level_figure(df, cols_of_interest=..., ...)
    # - new style: make_level_figure(df, cfg=my_cfg, time_window=(...))
    # - shorthand : make_level_figure(df, my_cfg, time_window=(...))
    if isinstance(cols_of_interest, LevelAppConfig):
        if cfg is not None:
            raise ValueError("Pass config only once: either cfg=... or second positional config")
        cfg = cols_of_interest
    
    # in the rare case where user has not already defined a configuration, or wishes to 
    # override just a few parameters without defining a full config, allow passing those few parameters directly and build the config internally
    if cfg is None:
        if cols_of_interest is None:
            raise ValueError("Provide either cols_of_interest or cfg=LevelAppConfig(...)")

        resolved_colour_map = colour_map if colour_map is not None else color_map

        cfg = LevelAppConfig(
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

    cfg = _normalize_config(cfg)
    _validate_inputs(df, cfg)
    if cfg.show_plotly_stack_mode_buttons:
        return _build_level_figure_with_plotly_mode_buttons(df, cfg, time_range=time_window)
    return _build_level_figure(df, cfg, time_range=time_window)


def pids_on_port(port: int, listening_only: bool = True) -> Sequence[int]:
    """Return process IDs bound to a TCP port on Windows.

    Parameters
    ----------
    port
        TCP local port number to inspect.
    listening_only
        If True, include only LISTENING sockets.
    """
    if not isinstance(port, int) or port <= 0 or port > 65535:
        raise ValueError("port must be an integer in [1, 65535]")

    # netstat is available by default on Windows and avoids extra dependencies.
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
    target_suffix = f":{port}"
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("TCP"):
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        local_addr = parts[1]
        state = parts[3]
        pid_text = parts[4]

        if not local_addr.endswith(target_suffix):
            continue
        if listening_only and state.upper() != "LISTENING":
            continue

        try:
            pids.add(int(pid_text))
        except ValueError:
            continue

    return sorted(pids)


def kill_processes_on_port(port: int, force: bool = True, dry_run: bool = False, listening_only: bool = True) -> Mapping[str, Any]:
    """Kill processes bound to a TCP port on Windows.

    Returns a summary dictionary so callers can inspect what happened.
    """
    pids = list(pids_on_port(port, listening_only=listening_only))

    summary = {
        "port": port,
        "pids": pids,
        "killed": [],
        "failed": [],
        "dry_run": dry_run,
    }

    if dry_run or not pids:
        return summary

    for pid in pids:
        cmd = ["taskkill", "/PID", str(pid)]
        if force:
            cmd.append("/F")

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            summary["killed"].append(pid)
        else:
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
    """Stop processes listening on common Dash ports.

    Parameters
    ----------
    ports
        Explicit list of ports to inspect. If None, uses inclusive range [port_start, port_end].
    port_start
        Start of default port range when ``ports`` is None.
    port_end
        End of default port range when ``ports`` is None.
    force
        Whether to force terminate matched processes.
    dry_run
        If True, only report what would be terminated.
    listening_only
        If True, only target LISTENING sockets.
    """
    if ports is None:
        if port_start > port_end:
            raise ValueError("port_start must be <= port_end")
        port_list = list(range(int(port_start), int(port_end) + 1))
    else:
        port_list = [int(p) for p in ports]

    results = []
    killed_pids = set()
    failed = []

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

    return {
        "ports_checked": port_list,
        "dry_run": dry_run,
        "results": results,
        "killed_pid_count": len(killed_pids),
        "killed_pids": sorted(killed_pids),
        "failed": failed,
    }
