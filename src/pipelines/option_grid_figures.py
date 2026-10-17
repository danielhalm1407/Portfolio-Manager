"""
13-02 — FIGURE BUILDERS FOR THE MONEYNESS SWEEP.

THE PATTERN, AND WHY IT IS THIS WAY
-----------------------------------
Copied deliberately from ``option_probe_figures.py`` — read its module docstring first. Every
``fig_*`` function below takes frames and returns a figure with the INLINE theme already applied
(``_inline_theme``, imported from that module so there is one definition of it). A research
script imports the same builders and calls ``.show()``; an export path stamps
``theme.apply_export_theme`` immediately before ``write_html``. One implementation serves the
interactive window and the published page, so the two cannot drift — which is the whole reason
``apply_export_theme`` is a post-hoc stamp rather than a ``theme=`` argument threaded through
every signature.

THE STACKED PANEL IS THE SAME SHAPE AS THE HEDGE-STRUCTURES PROBE'S, ON PURPOSE
-------------------------------------------------------------------------------
``fig_moneyness_stack`` below draws the same three panels as
``research/validation/option_hedge_structures_probe.fig_ladder``: equity against the market with
every fill marked, the long leg's value as a multiple of the premium paid, and implied vol with
the re-entry gate and the FLAT stretches. That layout answers the four questions a reader asks
in the order they ask them, and a second layout for the same questions would make two published
pages incomparable at a glance.

It is a SEPARATE function rather than a shared one because the two differ in what COLOUR means.
In the probe, one configuration is drawn and colour distinguishes the EVENT CLASS (a roll is
yellow, a monetisation is red). Here several configurations are drawn at once and colour must
distinguish the MONEYNESS, so the event class has to move onto the marker SHAPE alone. Trying to
serve both off one function would mean a colour argument that silently changes the meaning of
the legend. The SHAPES are shared — see ``EVENT_SYMBOL`` — so a triangle-down means the same
thing on both pages.

WHAT IS NOT HERE YET
--------------------
Task 4's fitted-surface figures (the pooled contour, the 3-D surface) need Task 3's smoothers,
which do not exist. They land beside these when they do.

EVERY FIGURE IS IN-SAMPLE, and says so in its own title. No figure here may be captioned as a
validated, selected or recommended parameter set.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from portutils.strategies import scoring
from portutils.strategies.scoring import MONETISE, OPEN, ROLL, classify_option_events
from portutils.viz import theme

from pipelines.option_probe_figures import _inline_theme

# The no-bid/ask caveat, stated on the face of every figure rather than in a report nobody opens.
# Monetisations fill at the model mark, which flatters the configurations that trade MOST — so a
# ladder figure in which the busiest line wins is not yet evidence that it would.
MARK_CAVEAT = "fills at the model mark — no bid/ask, no slippage"

# ============================================================================
# MARKER SHAPE PER LIFECYCLE CLASS. Shape ONLY — no colour, because in this
# module colour is spent on the moneyness and a marker's colour therefore has
# to match the line it sits on.
#
# These are the SAME three shapes `option_hedge_structures_probe._EVENT_STYLE`
# uses, so a triangle-down means "closed" on both published surfaces. They are
# duplicated rather than imported because that module is a research script with
# module-level simulator state at import time: importing it from a pipeline
# would load a parquet and run code as a side effect of drawing a chart.
# ============================================================================
EVENT_SYMBOL = {
    OPEN: "triangle-up",
    ROLL: "diamond",
    MONETISE: "triangle-down",
}


def moneyness_colours(labels, offset=1):
    """Deterministic label -> colour, stable across every figure in a session.

    ``offset=1`` leaves ``CATEGORICAL[0]`` for the UNDERLYING, which appears on every panel and
    must keep one colour throughout — a strike that borrowed SPY's blue in one figure and not in
    the next would make two panels of the same run look like two different runs.

    Never cycles. CATEGORICAL's order is a measured guarantee about adjacent-pair separation
    (see theme.py), so a ninth series wrapping back to slot 0 would become a twin of the
    underlying rather than a new colour. Past the end the labels go MUTED grey, which reads as
    "not individually tracked" instead of lying about identity.
    """
    out = {}
    for i, label in enumerate(labels):
        slot = i + offset
        out[label] = theme.CATEGORICAL[slot] if slot < len(theme.CATEGORICAL) else theme.GRID
    return out


def _rebase(series):
    """A series divided by its own first finite value, so every line starts at 1.0.

    Rebasing to the WINDOW's first bar (not to the full history's) is the same convention
    ``scoring.max_drawdown`` uses for its running peak, and for the same reason: a window that
    opens mid-decline must be read on its own terms, or it is not comparable to any other window.
    """
    seg = pd.Series(series).astype(float)
    first = seg.dropna()
    if first.empty or first.iloc[0] == 0:
        return seg
    return seg / float(first.iloc[0])


def _numeric(frame, column):
    """One column as a numeric Series, or None when absent or entirely null.

    ``multiple`` is None before any premium has been paid and ``iv`` is None under v1 pricing, so
    a column can exist and be entirely empty. Returning None lets the caller skip the trace
    rather than draw a flat line at zero, which would read as "the hedge was worthless" instead
    of "there was no hedge".
    """
    if column not in frame.columns:
        return None
    col = pd.to_numeric(frame[column], errors="coerce")
    return col if col.notna().any() else None


def fig_moneyness_stack(results, spot, title="", reentry_iv=None, underlying="SPY",
                        colours=None, show_events=True):
    """Three stacked panels, several moneyness levels at once, one shared x-axis.

    Parameters
    ----------
    results : dict
        label -> ``{"series": frame, "blotter": frame}`` (anything ``run_config`` returns works
        directly). ``series`` is ``build_series_frame``'s shape, so it already carries ``equity``
        AND the rule's per-bar ``state`` / ``multiple`` / ``iv`` — one frame feeds all three
        panels and nothing is re-derived from the simulator.
    reentry_iv : float or None
        Drawn as a dotted level on the vol panel ONLY when a configuration armed one. A threshold
        drawn on a run that never tested it invites the reader to explain the path with a rule
        that was not running.
    show_events : bool
        Marker overlay. Left on for monetising configurations and worth turning off for a
        blind-roll panel crowded with rolls and nothing else.

    THE THREE PANELS, in the order the question is actually asked:

    1. **equity and the market** — what each strike earned, against the thing it hedges, with
       every fill marked. Colour is the MONEYNESS; shape is the lifecycle class.
    2. **long-leg value as a multiple of premium paid** — the panel the moneyness question turns
       on. A blind roll's curve decaying back under 1.0 before every expiry IS finding 7 drawn
       rather than asserted: the hedge was worth more than it cost at some point and the rule
       held it to zero anyway. Colour matches panel 1, so a line is the same strike in both.
    3. **implied vol and the re-entry gate** — one real-IV path (it is the same series for every
       configuration; drawing it per strike would be four identical lines), plus a FLAT band per
       configuration showing when THAT strike sat unhedged.

    The x-axis is shared, so lining a monetisation up with the multiple it fired at and the vol
    that let it back in is a physical act rather than an act of memory.
    """
    labels = list(results)
    colours = moneyness_colours(labels) if colours is None else colours

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.52, 0.24, 0.24], vertical_spacing=0.05,
                        subplot_titles=("equity and the market",
                                        "long-leg value as a multiple of premium paid",
                                        "implied vol and the re-entry gate"))

    # ------------------------------------------------------------------------
    # PANEL 1 — THE UNDERLYING FIRST. "Did this beat holding the index" is the question a reader
    # asks before any comparison between strikes, so the benchmark is drawn before the strategies
    # and keeps CATEGORICAL[0] on every figure in the set.
    # ------------------------------------------------------------------------
    spot = pd.Series(spot).astype(float)
    fig.add_trace(go.Scatter(
        x=spot.index, y=_rebase(spot).to_numpy(), name=f"{underlying} only",
        line=dict(color=theme.CATEGORICAL[0], width=2.2),
        hovertemplate="%{x|%Y-%m-%d}<br>" + underlying + " %{y:.3f}<extra></extra>"), row=1, col=1)

    iv_drawn = False
    # The FLAT bands are stacked at descending heights near the top of the vol panel, one per
    # configuration. Computed as a fraction of the panel's own range rather than at fixed levels,
    # because real IV spans 0.07 to 0.80 over the full history and 0.15 to 0.80 over the GFC.
    flat_slot = 0

    for label in labels:
        res = results[label]
        series = res["series"]
        colour = colours[label]
        equity = _rebase(series["equity"])

        fig.add_trace(go.Scatter(
            x=equity.index, y=equity.to_numpy(), name=label, legendgroup=label,
            line=dict(color=colour, width=1.6),
            hovertemplate="%{x|%Y-%m-%d}<br>" + label + " %{y:.3f}<extra></extra>"), row=1, col=1)

        # --------------------------------------------------------------------
        # THE MARKERS. Classified through scoring.classify_option_events — the SHARED definition,
        # so this figure, the hedge-structures probe and 13-01's tables cannot disagree about
        # what a roll is. Markers sit ON the equity line: the question is "what did equity do at
        # the moment this traded", and an event rug underneath would force the eye to travel.
        # --------------------------------------------------------------------
        events = classify_option_events(res.get("blotter"), underlying) if show_events \
            else pd.DataFrame()
        for kind, symbol in EVENT_SYMBOL.items():
            rows = events[events["event"] == kind] if len(events) else events
            if not len(rows):
                continue
            ts = pd.DatetimeIndex(rows["ts"])
            fig.add_trace(go.Scatter(
                x=ts, y=equity.reindex(ts).to_numpy(), mode="markers",
                # showlegend=False: with four strikes x three classes the legend would carry
                # twelve entries saying almost nothing. The three SHAPE keys are added once at
                # the end instead, in neutral ink.
                name=f"{label} {kind}", legendgroup=label, showlegend=False,
                marker=dict(color=colour, symbol=symbol, size=9,
                            line=dict(color=theme.INK, width=0.6)),
                hovertemplate=("<b>" + kind + "</b> " + label + "  %{x|%Y-%m-%d}"
                               "<br>equity %{y:.3f}<extra></extra>")), row=1, col=1)

        # --------------------------------------------------------------------
        # PANEL 2 — the rule's OWN `multiple` (long value / premium paid), never recomputed here.
        # Recomputing would let the figure and the trigger disagree about the one number the
        # trigger fires on, and a figure that disagrees with the thing it depicts is worse than
        # no figure. It is None while FLAT, which plots as a GAP — the breaks ARE the unhedged
        # stretches, and they line up with the bands in the panel below.
        # --------------------------------------------------------------------
        mult = _numeric(series, "multiple")
        if mult is not None:
            fig.add_trace(go.Scatter(
                x=mult.index, y=mult.to_numpy(), name=label, legendgroup=label, showlegend=False,
                line=dict(color=colour, width=1.4), connectgaps=False,
                hovertemplate="%{x|%Y-%m-%d}<br>" + label
                              + " %{y:.2f}x premium<extra></extra>"), row=2, col=1)
            # The monetise bars marked ON this curve as well as on equity: "how far above its
            # cost was the hedge when the policy took the money" is a point on THIS line.
            mon = events[events["event"] == MONETISE] if len(events) else events
            if len(mon):
                mts = pd.DatetimeIndex(mon["ts"])
                fig.add_trace(go.Scatter(
                    x=mts, y=mult.reindex(mts).to_numpy(), mode="markers",
                    name=f"{label} monetised", legendgroup=label, showlegend=False,
                    marker=dict(color=colour, symbol=EVENT_SYMBOL[MONETISE], size=9,
                                line=dict(color=theme.INK, width=0.6)),
                    hovertemplate="monetised " + label
                                  + " %{x|%Y-%m-%d}<br>%{y:.2f}x premium<extra></extra>"),
                    row=2, col=1)

        # --------------------------------------------------------------------
        # PANEL 3 — the IV the rule actually priced and decided with, taken from the rule's own
        # history rather than re-read from the IV parquet. Drawn ONCE: it is the same series for
        # every configuration in a given window, and four identical lines would imply otherwise.
        # --------------------------------------------------------------------
        iv = _numeric(series, "iv")
        if iv is not None and not iv_drawn:
            fig.add_trace(go.Scatter(
                x=iv.index, y=iv.to_numpy(), name="real IV", legendgroup="iv",
                line=dict(color=theme.CATEGORICAL[0], width=1.3),
                hovertemplate="%{x|%Y-%m-%d}<br>iv %{y:.3f}<extra></extra>"), row=3, col=1)
            iv_drawn = True

        # The FLAT stretches as a filled band. A REGION rather than two markers, because the eye
        # reads a shaded span as a duration — which is what a wait is. One masked trace with NaN
        # gaps rather than N shapes: a 5,201-bar run would otherwise add an unbounded number of
        # layout objects.
        if iv is not None and "state" in series.columns:
            flat = (series["state"] == "FLAT").to_numpy()
            if flat.any():
                lo, hi = float(np.nanmin(iv)), float(np.nanmax(iv))
                # Stacked downward from the top edge so several configurations' bands are
                # readable at once instead of overprinting into one opaque line.
                level = hi - flat_slot * 0.055 * max(hi - lo, 1e-6)
                fig.add_trace(go.Scatter(
                    x=series.index, y=np.where(flat, level, np.nan),
                    name=f"{label} FLAT", legendgroup=label, showlegend=False,
                    mode="lines", line=dict(color=colour, width=5), opacity=0.45,
                    connectgaps=False,
                    hovertemplate="%{x|%Y-%m-%d}<br>" + label
                                  + " unhedged<extra></extra>"), row=3, col=1)
                flat_slot += 1

    # BREAK-EVEN on panel 2. Drawn on every figure because it is a property of the ratio, not of
    # any configuration's parameters: above it the legs are worth more than they cost.
    fig.add_hline(y=1.0, row=2, col=1, line=dict(color=theme.INK, width=1.0, dash="dash"),
                  annotation_text="break-even (1.0x)", annotation_position="top left")
    if reentry_iv is not None:
        fig.add_hline(y=float(reentry_iv), row=3, col=1,
                      line=dict(color=theme.CATEGORICAL[4], width=1.2, dash="dot"),
                      annotation_text=f"reentry_iv {float(reentry_iv):.3f}",
                      annotation_position="top left")

    # The three SHAPE keys, once, in neutral ink — the legend entry the per-configuration markers
    # deliberately do not carry. Zero-length traces so they add a key and no data.
    if show_events:
        for kind, symbol in EVENT_SYMBOL.items():
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers", name=kind, legendgroup="events",
                marker=dict(color=theme.INK, symbol=symbol, size=9),
                hoverinfo="skip"), row=1, col=1)

    fig.update_layout(
        title=(f"{title}<br><sub>IN-SAMPLE. Rebased to the window's first bar. "
               f"Colour = moneyness, shape = lifecycle. {MARK_CAVEAT}.</sub>"),
        hovermode="closest",
        # Plotly sizes the FIGURE, not the panels — left unset this 3-row figure renders with
        # each panel squeezed into a third of 450px. theme.STACKED_FIGURE_HEIGHT is the shared
        # table every stacked figure in the repo reads this from.
        height=theme.STACKED_FIGURE_HEIGHT[3],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    fig.update_yaxes(title_text="rebased to window start (1.0)", row=1, col=1)
    fig.update_yaxes(title_text="x premium paid", row=2, col=1)
    fig.update_yaxes(title_text="implied vol", row=3, col=1)
    # Subplot titles are annotations, so they inherit neither the axis font nor apply_export_theme's
    # stamp — a label left on Plotly's default near-black vanishes into the dark export surface.
    for ann in fig.layout.annotations:
        ann.font.size = 11
        ann.font.color = theme.INK
    return _inline_theme(fig)


def fig_score_grid(scores, value_col="score", row="config", col="window", title="",
                   trial_count=None, normalise="column", colourbar_title=None,
                   text_format="{:.2f}", midpoint=None):
    """The coloured score grid — one cell per (row, col) pair, text the raw value.

    ==========================================================================
    COLOUR IS NORMALISED WITHIN EACH COLUMN BY DEFAULT, AND THAT IS THE POINT.
    A single colour scale across the whole grid compares a GFC cell with a calm
    2017 cell, and those are not comparable: every configuration scores around
    -0.8 through the GFC and around +5 through 2017, so a shared scale paints
    the entire crisis column one shade of red and the entire calm column one
    shade of blue. The grid then shows which REGIME was benign — which nobody
    needs a grid to learn — and hides the only thing it was drawn for, which is
    which configuration did better WITHIN a regime.
    Normalising per column puts each window on its own 0-1 scale, so colour
    answers "best to worst inside this window" and the printed number carries
    the magnitude. ``normalise=None`` restores one shared scale for the cases
    where cross-column magnitude IS the question (the SPY-relative grid, where
    1.0 means the same thing in every column).
    ==========================================================================

    Floor-bound cells are MARKED WITH AN ASTERISK, never with a different colour scale. Changing
    the scale would make a floored cell look like a score — the one thing the flag exists to
    prevent — whereas an asterisk beside the number says "this denominator was set by the floor,
    not by the strategy" without altering the reader's sense of magnitude.
    """
    pivot = scores.pivot_table(index=row, columns=col, values=value_col, aggfunc="mean",
                               sort=False)
    floored = scores.pivot_table(index=row, columns=col, values="floor_bound", aggfunc="max",
                                 sort=False) if "floor_bound" in scores.columns else None

    raw = pivot.to_numpy(dtype=float)
    if normalise == "column":
        # Per-column min-max onto 0-1. A column whose values are all equal would divide by zero;
        # it becomes 0.5 (uniform mid-tone), which is the honest rendering of "nothing to choose
        # between these".
        lo = np.nanmin(raw, axis=0)
        hi = np.nanmax(raw, axis=0)
        span = np.where(np.isclose(hi, lo), np.nan, hi - lo)
        z = np.where(np.isnan(span), 0.5, (raw - lo) / span)
        colourscale, zmid = "RdBu", None
        bar_title = colourbar_title or "rank within<br>column (0-1)"
    else:
        z = raw
        colourscale = "RdBu"
        # Zero is the only meaningful anchor a Calmar has; 1.0 is the anchor for a ratio against
        # the index. Passed explicitly rather than guessed from the column name.
        zmid = 0.0 if midpoint is None else midpoint
        bar_title = colourbar_title or value_col

    # Cell text: the RAW value in every case, with an asterisk where the floor bound. Built as
    # strings rather than left to `texttemplate`, because the asterisk is conditional per cell and
    # because under column normalisation `z` is no longer the number a reader wants to see.
    text = []
    for i, r in enumerate(pivot.index):
        line = []
        for j, cc in enumerate(pivot.columns):
            v = raw[i, j]
            if not np.isfinite(v):
                line.append("")
                continue
            star = "*" if (floored is not None and bool(floored.loc[r, cc])) else ""
            line.append(text_format.format(v) + star)
        text.append(line)

    fig = go.Figure(go.Heatmap(
        z=z, x=[str(c) for c in pivot.columns], y=[str(r) for r in pivot.index],
        text=text, texttemplate="%{text}",
        # `customdata` carries the RAW value so the hover never shows the normalised one — a
        # tooltip reading "0.73" for a cell printed "-0.86" would look like a bug.
        customdata=raw,
        colorscale=colourscale, zmid=zmid, colorbar=dict(title=bar_title),
        hovertemplate=(f"{row}: %{{y}}<br>{col}: %{{x}}<br>"
                       f"{value_col}: %{{customdata:.3f}}<extra></extra>")))

    trials = f" — {trial_count} trials" if trial_count is not None else ""
    note = ("colour normalised WITHIN each column, so it ranks inside a window and not across "
            "windows; the printed number is the raw value"
            if normalise == "column" else "one shared colour scale across the whole grid")
    fig.update_layout(
        title=(f"{title}<br><sub>IN-SAMPLE{trials}. {note}. Floor "
               f"{scoring.DRAWDOWN_FLOOR:.0%}; * = denominator set by the floor, so that cell is "
               f"NOT a Calmar. {MARK_CAVEAT}.</sub>"),
        height=120 + 42 * max(len(pivot.index), 3))
    return _inline_theme(fig)
