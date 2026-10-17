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

    # ------------------------------------------------------------------------
    # THE IV PATH IS SHARED, so it is resolved ONCE here rather than inside the loop. It is the
    # same series for every configuration in a given window — the rule prices off the market, not
    # off its own strike — so drawing it per configuration would be four identical lines implying
    # four different ones.
    #
    # Hoisted ABOVE the loop for a second reason as well: the FLAT bands below need the panel's
    # vertical range to stack themselves against, and the trace itself must be added AFTER every
    # configuration so it lands at the BOTTOM of the legend. Plotly orders legend entries by the
    # order traces were added, and adding this one from inside the loop put "real IV" between the
    # first and second moneyness levels — reading as though it were one of them.
    # ------------------------------------------------------------------------
    iv_ref = None
    for res in results.values():
        iv_ref = _numeric(res["series"], "iv")
        if iv_ref is not None:
            break
    iv_lo = float(np.nanmin(iv_ref)) if iv_ref is not None else 0.0
    iv_hi = float(np.nanmax(iv_ref)) if iv_ref is not None else 1.0

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
        # PANEL 3 — the FLAT stretches for THIS configuration, as a filled band. A REGION rather
        # than two markers, because the eye reads a shaded span as a duration, which is what a
        # wait is. One masked trace with NaN gaps rather than N shapes: a 5,201-bar run would
        # otherwise add an unbounded number of layout objects.
        #
        # The IV line itself is NOT drawn here — it is shared, and it is added after the loop so
        # it sorts to the bottom of the legend. See the hoist above.
        # --------------------------------------------------------------------
        if iv_ref is not None and "state" in series.columns:
            flat = (series["state"] == "FLAT").to_numpy()
            if flat.any():
                # Stacked downward from the top edge so several configurations' bands are
                # readable at once instead of overprinting into one opaque line.
                level = iv_hi - flat_slot * 0.055 * max(iv_hi - iv_lo, 1e-6)
                fig.add_trace(go.Scatter(
                    x=series.index, y=np.where(flat, level, np.nan),
                    name=f"{label} FLAT", legendgroup=label, showlegend=False,
                    mode="lines", line=dict(color=colour, width=5), opacity=0.45,
                    connectgaps=False,
                    hovertemplate="%{x|%Y-%m-%d}<br>" + label
                                  + " unhedged<extra></extra>"), row=3, col=1)
                flat_slot += 1

    # ------------------------------------------------------------------------
    # THE SHARED IV PATH, added last so it sorts to the BOTTOM of the legend, under every
    # moneyness entry rather than between two of them.
    #
    # DRAWN IN INK, NOT IN A CATEGORICAL SLOT. It used to take CATEGORICAL[0] — the same blue
    # the UNDERLYING keeps on every figure — so the legend showed two different quantities in one
    # colour. It cannot simply take another slot either: `moneyness_colours` hands out slots 1
    # upwards, so any slot this took would collide with a long enough ladder. Ink says "ambient
    # context, not one of the series", which is exactly what the market's vol is here — and it
    # is unambiguous inside panel 3, where the only other marks are the per-configuration FLAT
    # bands in their own moneyness colours.
    # ------------------------------------------------------------------------
    if iv_ref is not None:
        fig.add_trace(go.Scatter(
            x=iv_ref.index, y=iv_ref.to_numpy(), name="real IV", legendgroup="iv",
            line=dict(color=theme.INK, width=1.3),
            hovertemplate="%{x|%Y-%m-%d}<br>iv %{y:.3f}<extra></extra>"), row=3, col=1)

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
        height=theme.STACKED_FIGURE_HEIGHT[3])
    # THE LEGEND GOES DOWN THE SIDE, not across the top. This figure originally copied
    # `fig_ladder`'s horizontal top legend, which is right for ITS seven entries and wrong here:
    # one entry per moneyness level plus the underlying, the real-IV path and the three lifecycle
    # shape keys is ten or more, so the legend wrapped onto a second row and rendered straight
    # through the title and its sub-line. A vertical legend grows DOWNWARD into margin reserved
    # for it, so the entry count cannot collide with the title however far the ladder is widened.
    theme.apply_side_legend(fig)
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


# ============================================================================
# TASK 4 — THE SURFACE FIGURES.
#
# All four read the SAME two axes in the same orientation: moneyness across,
# monetisation drawdown down. The raw heatmap and the fitted contour therefore
# sit on identical axes, which is the whole point of drawing both — the
# smoothing has to be legible as a CONTINUATION of the design points rather
# than as a separate object that happens to be nearby.
# ============================================================================

# The two swept parameters, named once. Every figure below and the sweep script read these, so a
# renamed column breaks in one place rather than four.
X_PARAM = "floor"
Y_PARAM = "monetise_drawdown"
X_LABEL = "moneyness (strike / spot)"
Y_LABEL = "monetisation drawdown"


def _mesh(fit, xs, ys):
    """Predicted surface on a rectangular mesh, shaped ``(len(ys), len(xs))`` for a heatmap.

    Plotly's ``z`` is row-major with rows on the Y axis, so the transpose matters and is done
    once here rather than at four call sites where three of them would eventually be wrong.
    """
    gx, gy = np.meshgrid(xs, ys, indexing="xy")
    z = np.asarray(fit.predict(gx.ravel(), gy.ravel()), dtype=float)
    return z.reshape(gx.shape)


def fig_grid_heatmap(scores, title="", trial_count=None):
    """THE RAW DESIGN POINTS — moneyness across, monetisation drawdown down, colour the score
    pooled over every window. No smoothing anywhere: this is what was actually simulated.

    POOLING ACROSS WINDOWS AVERAGES REGIMES WHOSE LEVELS DIFFER BY A FACTOR OF SIX (around -0.8
    through the GFC against around +5 through 2017), so this figure is dominated by the calm
    windows and should be read as "the pooled design", not as a verdict. The per-window panel
    below is where a moving optimum becomes visible, and it is the more informative figure of
    the two. Both are kept because AC-6 asks for the unsmoothed design points as their own
    object.
    """
    return fig_score_grid(scores, value_col="score", row=Y_PARAM, col=X_PARAM,
                          title=title or "Raw grid — floored Calmar pooled over all windows",
                          trial_count=trial_count, normalise=None,
                          colourbar_title="pooled<br>floored Calmar", text_format="{:.2f}")


def fig_window_surfaces(fits, optima, bounds, title="", trial_count=None, n_mesh=60):
    """ONE PANEL PER INDICATIVE WINDOW, each with its own fitted surface and its own optimum.

    THE MOST INFORMATIVE OUTPUT OF THE PLAN, and the reason pooling alone would be a mistake:
    four windows are four REGIMES, not four replicates of one quantity. The spread between the
    panels is SIGNAL. If the optimum sits in roughly the same place in each, a single parameter
    choice is defensible; if it moves, a single pooled optimum is the wrong object and 13-03
    should be validating a regime-conditional rule instead. Either way it is a finding, and it
    is only visible side by side.

    Each panel carries its OWN colour scale, for the same reason ``fig_score_grid`` normalises
    per column: a shared scale across a crisis panel and a calm panel shows which regime was
    benign and hides where each one's optimum is.
    """
    names = list(fits)
    cols = min(3, max(1, len(names)))
    rows = int(np.ceil(len(names) / cols))
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=names,
                        horizontal_spacing=0.07, vertical_spacing=0.12)

    (lox, hix), (loy, hiy) = bounds
    xs = np.linspace(lox, hix, n_mesh)
    ys = np.linspace(loy, hiy, n_mesh)

    for i, name in enumerate(names):
        r, c = divmod(i, cols)
        fig.add_trace(go.Heatmap(
            z=_mesh(fits[name], xs, ys), x=xs, y=ys, colorscale="RdBu",
            # One colour bar per panel would take half the figure; the scales differ per panel
            # anyway, so a single shared bar would be a lie. Dropped entirely and the optimum
            # marker plus the panel title carry the reading instead.
            showscale=False,
            hovertemplate=(f"{X_LABEL}: %{{x:.3f}}<br>{Y_LABEL}: %{{y:.3f}}"
                           "<br>fitted: %{z:.3f}<extra></extra>")), row=r + 1, col=c + 1)
        opt = optima[name]
        fig.add_trace(go.Scatter(
            x=[opt.point[0]], y=[opt.point[1]], mode="markers",
            marker=dict(color=theme.INK, symbol="x", size=11, line=dict(width=1)),
            showlegend=False,
            hovertemplate=(f"<b>{name} optimum</b><br>{X_LABEL} %{{x:.3f}}"
                           f"<br>{Y_LABEL} %{{y:.3f}}<br>fitted {opt.value:.3f}"
                           f"<br>curvature {opt.curvature:.1f}<extra></extra>")),
            row=r + 1, col=c + 1)

    trials = f" — {trial_count} trials" if trial_count is not None else ""
    fig.update_layout(
        title=(f"{title or 'Fitted surface per indicative window — x marks each optimum'}"
               f"<br><sub>IN-SAMPLE{trials}. Each panel has its OWN colour scale: a shared one "
               f"would show which regime was benign and hide where each optimum is. "
               f"{MARK_CAVEAT}.</sub>"),
        height=300 * rows + 140)
    fig.update_xaxes(title_text=X_LABEL)
    fig.update_yaxes(title_text=Y_LABEL)
    for ann in fig.layout.annotations:
        ann.font.size = 11
        ann.font.color = theme.INK
    return _inline_theme(fig)


def fig_pooled_contour(fit, scores, opt, bounds, title="", trial_count=None, n_mesh=80):
    """THE POOLED SURFACE AS A FILLED CONTOUR, WITH THE DESIGN POINTS DRAWN ON TOP.

    Same axes and same orientation as ``fig_grid_heatmap``, deliberately, so the two can be read
    one after the other. The design points are overlaid as open markers rather than left to the
    reader's memory of the previous figure: a contour that bulges where there is no design point
    underneath it is the smoother inventing structure, and that is visible here and nowhere else.
    """
    (lox, hix), (loy, hiy) = bounds
    xs = np.linspace(lox, hix, n_mesh)
    ys = np.linspace(loy, hiy, n_mesh)

    fig = go.Figure(go.Contour(
        z=_mesh(fit, xs, ys), x=xs, y=ys, colorscale="RdBu",
        contours=dict(showlabels=True, labelfont=dict(size=10, color=theme.INK)),
        colorbar=dict(title="fitted pooled<br>floored Calmar"),
        hovertemplate=(f"{X_LABEL}: %{{x:.3f}}<br>{Y_LABEL}: %{{y:.3f}}"
                       "<br>fitted: %{z:.3f}<extra></extra>")))

    pooled = scores.groupby([X_PARAM, Y_PARAM])["score"].mean().reset_index()
    fig.add_trace(go.Scatter(
        x=pooled[X_PARAM], y=pooled[Y_PARAM], mode="markers", name="design points",
        marker=dict(color=theme.INK, symbol="circle-open", size=7, line=dict(width=1.2)),
        customdata=pooled["score"],
        hovertemplate=(f"{X_LABEL}: %{{x:.3f}}<br>{Y_LABEL}: %{{y:.3f}}"
                       "<br>SIMULATED: %{customdata:.3f}<extra></extra>")))
    fig.add_trace(go.Scatter(
        x=[opt.point[0]], y=[opt.point[1]], mode="markers", name="in-sample optimum",
        marker=dict(color=theme.CATEGORICAL[1], symbol="x", size=13, line=dict(width=1)),
        hovertemplate=(f"<b>in-sample optimum</b><br>{X_LABEL} %{{x:.3f}}"
                       f"<br>{Y_LABEL} %{{y:.3f}}<extra></extra>")))

    trials = f" — {trial_count} trials" if trial_count is not None else ""
    fig.update_layout(
        title=(f"{title or 'Pooled fitted surface, on the same axes as the raw grid'}"
               f"<br><sub>IN-SAMPLE{trials}. Open circles are the SIMULATED design points; the "
               f"contour between them is fitted. A bulge with no point under it is the smoother "
               f"inventing structure. {MARK_CAVEAT}.</sub>"),
        xaxis_title=X_LABEL, yaxis_title=Y_LABEL, height=620)
    return _inline_theme(fig)


def fig_pooled_surface_3d(fit, opt, snap, bounds, title="", trial_count=None, n_mesh=60):
    """THE POOLED SURFACE IN THREE DIMENSIONS, with the continuous optimum and the NEAREST
    TRADEABLE STRIKE both marked.

    The pair of markers is the point of the figure. A surface's optimum is a real number — "0.914
    of spot" — and no exchange lists that strike. If the two markers sit at visibly different
    heights, the surface is too sharp for its own optimum to mean anything deployable, and that
    is the finding rather than a rounding footnote. If they are indistinguishable, the
    recommendation survives being rounded, which is the strongest form this result can take.
    """
    (lox, hix), (loy, hiy) = bounds
    xs = np.linspace(lox, hix, n_mesh)
    ys = np.linspace(loy, hiy, n_mesh)
    z = _mesh(fit, xs, ys)

    fig = go.Figure(go.Surface(
        z=z, x=xs, y=ys, colorscale="RdBu", opacity=0.92,
        colorbar=dict(title="fitted pooled<br>floored Calmar"),
        hovertemplate=(f"{X_LABEL}: %{{x:.3f}}<br>{Y_LABEL}: %{{y:.3f}}"
                       "<br>fitted: %{z:.3f}<extra></extra>")))

    snapped_value = opt.value - (snap.cost if np.isfinite(snap.cost) else 0.0)
    fig.add_trace(go.Scatter3d(
        x=[opt.point[0], snap.moneyness], y=[opt.point[1], opt.point[1]],
        z=[opt.value, snapped_value], mode="markers+text",
        text=[f"continuous {opt.point[0]:.4f}", f"strike {snap.strike:.0f} ({snap.moneyness:.3f})"],
        textposition="top center", textfont=dict(color=theme.INK, size=10),
        marker=dict(color=[theme.CATEGORICAL[1], theme.CATEGORICAL[3]], size=6,
                    symbol=["x", "diamond"]),
        name="optimum vs nearest tradeable strike",
        hovertemplate="%{text}<br>fitted %{z:.4f}<extra></extra>"))

    trials = f" — {trial_count} trials" if trial_count is not None else ""
    cost = f"{snap.cost:.4f}" if np.isfinite(snap.cost) else "n/a"
    fig.update_layout(
        title=(f"{title or 'Pooled fitted surface — continuous optimum and nearest strike'}"
               f"<br><sub>IN-SAMPLE{trials}. Snapping cost {cost} in floored-Calmar units. "
               f"Curvature at the optimum {opt.curvature:.1f}. {MARK_CAVEAT}.</sub>"),
        scene=dict(xaxis_title=X_LABEL, yaxis_title=Y_LABEL,
                   zaxis_title="fitted floored Calmar"),
        height=720)
    return _inline_theme(fig)


def fig_window_paths(window_name, scores, results, spot, reentry_iv=None, underlying="SPY"):
    """THE BEST, SECOND-BEST AND WORST configurations for ONE window, as PATHS.

    A cell in a heatmap is a number; the same cell as an equity path next to SPY through the GFC
    is an argument. This is the figure a human reads first.

    SECOND BEST is included, and not as padding: the gap between first and second IS the
    plateau-versus-spike question in path form. If the two are indistinguishable the surface is
    flat there, and that is readable without consulting a curvature number. If the best is
    visibly apart from the second on one window and not on another, the optimum is sharp in one
    regime and broad in the other — which a single pooled curvature would average away.

    Delegates the drawing to ``fig_moneyness_stack``, so these three paths come with the same
    three panels as every other path figure in the plan: equity with fills marked, value as a
    multiple of premium, and the vol gate. Selecting three configurations and then drawing them
    a DIFFERENT way would make this the one panel in the set that cannot be compared with the
    others.
    """
    rows = scores[(scores["window"] == window_name) & scores["score"].notna()]
    rows = rows.sort_values("score", ascending=False)
    if len(rows) < 2:
        raise ValueError(f"{window_name}: need at least 2 scored configurations, got {len(rows)}")

    picks = [("best", rows.iloc[0]), ("worst", rows.iloc[-1])]
    if len(rows) > 2:
        picks.insert(1, ("2nd", rows.iloc[1]))
    chosen = {f"{rank} — {r['config']} ({r['score']:.2f})": results[r["config"]]
              for rank, r in picks}

    kind = rows.iloc[0].get("kind", "")
    return fig_moneyness_stack(
        chosen, spot, reentry_iv=reentry_iv, underlying=underlying,
        title=f"{window_name} ({kind}) — best, second-best and worst by floored Calmar")


def fig_pooled_spline(fit, scores, bounds, title="", trial_count=None, n_mesh=80):
    """The pooled smooth drawn on the SAME axes and the SAME colour scale as the pooled raw grid.

    Separate from ``fig_pooled_contour`` only in that it is a heatmap rather than a contour, so
    it can be put directly beside ``fig_score_grid``'s pooled output and read as the same object
    with the gaps filled in. That adjacency is the argument: smoothing is a continuation of the
    design points, not a different claim about them.
    """
    (lox, hix), (loy, hiy) = bounds
    xs = np.linspace(lox, hix, n_mesh)
    ys = np.linspace(loy, hiy, n_mesh)
    pooled = scores.groupby([X_PARAM, Y_PARAM])["score"].mean()

    fig = go.Figure(go.Heatmap(
        z=_mesh(fit, xs, ys), x=xs, y=ys, colorscale="RdBu",
        # Pinned to the RANGE OF THE DESIGN POINTS, not to the fitted surface's own range, so
        # this figure and the raw pooled grid share a scale and a colour means one thing across
        # the pair. A fitted surface that overshoots the data then shows as saturated colour,
        # which is the correct signal.
        zmin=float(pooled.min()), zmax=float(pooled.max()),
        colorbar=dict(title="fitted pooled<br>floored Calmar"),
        hovertemplate=(f"{X_LABEL}: %{{x:.3f}}<br>{Y_LABEL}: %{{y:.3f}}"
                       "<br>fitted: %{z:.3f}<extra></extra>")))

    trials = f" — {trial_count} trials" if trial_count is not None else ""
    fig.update_layout(
        title=(f"{title or 'Pooled spline, on the raw grid’s axes and colour range'}"
               f"<br><sub>IN-SAMPLE{trials}. Colour range pinned to the SIMULATED points, so "
               f"saturation means the fit overshot the data. {MARK_CAVEAT}.</sub>"),
        xaxis_title=X_LABEL, yaxis_title=Y_LABEL, height=600)
    return _inline_theme(fig)
