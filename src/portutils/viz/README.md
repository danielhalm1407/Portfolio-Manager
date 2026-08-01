# `viz/` — figures that look like one system

Plotting helpers shared by notebooks, the Dash app and exported reports.

| module | what |
|---|---|
| [`theme.py`](theme.py) | **the single source of truth for colour.** `ticker_colour_map()`, `apply_export_theme()` |
| [`panel.py`](panel.py) | `PanelBuilder` — data-loading plus Plotly theming for portfolio notebooks |
| [`dash_timeseries_app.py`](dash_timeseries_app.py) | configurable time-series and histogram apps: `TimeSeriesAppConfig`, `HistogramAppConfig`, `ColourGroupConfig`, `GradientColourMap`, `AutoLabelMap` |

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
