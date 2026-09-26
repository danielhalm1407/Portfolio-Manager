# %% 1. Import libraries

# 1. Import libraries
# Imports

# ============================================================================
# THE LADDER PROBE (13-01.1) — one configuration, read as a picture.
#
# WHY THIS EXISTS. 13-01's monetisation mechanisms are proven on SYNTHETIC paths
# (see its implementation context section 4), where the exact case could be
# constructed bar by bar. They have never been seen on real data. A table of
# final values cannot tell you whether the trigger fired on the right BAR — it
# can only tell you that something fired at some point. A picture with every
# fill marked on the equity path can, and that is what this script draws.
#
# It is also the LENS for 13-01 Task 3's staged ladder: rung 3a (one struck
# hedge), 3b (rolling), 3c (monetising), 3d (the same configuration over the
# full history). Each rung changes ONE thing, so a wrong picture names its own
# cause. Run the rung, look at it, then move to the next.
#
# Cell-style (# %%) in the manner of research/option_overlay_probe.py, including
# its reload cell, so the figure can be iterated on inside a live session.
# ============================================================================

import importlib
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from portutils.portfolio.book import Book
from portutils.portfolio.narrow_ledger import NarrowLedger
from portutils.portfolio.rules import BuyAndHoldRule
from portutils.portfolio.simulator import PortfolioSimulator
from portutils.strategies.rules.options import (ProtectivePutRule, PutSpreadRule,
                                                RollingCollarRule)
from portutils.viz import theme

import portutils.strategies.scoring as scoring
from portutils.strategies.scoring import (MONETISE, OPEN, ROLL, classify_option_events,
                                          classify_option_fills)

# The loaders and the shared carry constants live in 13-01's batch runner and the probe figure
# module. Imported, never re-derived: a second copy of "which bars are the run window" is a
# second thing to get wrong.
import pipelines.option_monetisation_batch as omb
from pipelines.option_monetisation_batch import RESET_BARS, load_window
from pipelines.option_probe_figures import DIV_YIELD, RATE, UNDERLYING, _inline_theme

# The report renderer 10-01 built for the probe write-up, generalised (13-01 amendment,
# 2026-09-26) to serve THIS write-up too — one implementation, so the heading -> figure guard,
# the link rewriting and the CSS cannot drift between the two published reports. No cycle: this
# module imports pipelines.build_report, and build_report never imports this module back.
from pipelines.build_report import build_report

# %% Reload custom package

# Reload custom package

# Reload modules during development (re-import names after reload). The classifier is what
# actually gets iterated on here; the ledger and the rules underneath it are stable.
importlib.reload(scoring)
importlib.reload(omb)
from portutils.strategies.scoring import (MONETISE, OPEN, ROLL, classify_option_events,  # noqa: E402
                                          classify_option_fills)

# %% 2. Load the window

# 2. Load the window

# SPY closes and REAL implied vol on their intersection: 5,201 bars, 2006-01-09 to 2026-09-18.
# The pre-2006 price history is dropped loudly rather than silently — v2 cannot price it, which
# is a Phase 11 data limit and not a choice. Loaded ONCE; every rung below slices this.
SPOT_FULL, IV_FULL = load_window()

# The GFC window. Used for rungs 3a-3c because it is the one window in which every mechanism
# fires — a deep drawdown to trigger monetisation, a vol spike to hold the re-entry gate, and
# enough bars for several rolls — and because real IV starts 2006-01-09, so nothing earlier is
# covered at all.
GFC = ("2007-10-01", "2009-03-31")

# %% 3. run_one — the single code path every rung uses

# 3. run_one

def run_one(start=None, end=None, rule_cls=ProtectivePutRule, **rule_kwargs):
    """One configuration over one window. Returns everything needed to draw it.

    Every rung of the ladder calls THIS function with different arguments. A rung that worked
    through a different code path would prove nothing about the rung above it. `rule_cls` is the
    same argument in that spirit: the three structures are compared by swapping ONE parameter,
    never by three separate harnesses that could drift apart in their sizing or their capital.

    Returns ``(state, blotter, history, rule, elapsed)``:
      * ``state``   — the NARROW per-bar frame (equity and the TOTAL_* aggregates, ~15 columns
                      however many legs are struck)
      * ``blotter`` — every fill: ts, symbol, side, qty, price, notional
      * ``history`` — the rule's own per-bar read-outs (state, drawdown, multiple, iv), indexed
                      by ts
      * ``rule``    — the rule object itself, for its events log
    """
    # ------------------------------------------------------------------------
    # The window. Sliced from the already-loaded full series rather than reloaded, so switching
    # rungs in a session costs nothing.
    # ------------------------------------------------------------------------
    spot = SPOT_FULL.loc[start:end]
    iv = IV_FULL.loc[start:end]
    if len(spot) == 0:
        raise ValueError(f"empty window {start} -> {end}")

    # ------------------------------------------------------------------------
    # The rule. v2 pricing by default — real IV level AND current-spot moneyness, together —
    # because that is what 13-01 built and what every rung is meant to exercise. `iv_series` is
    # sliced to the SAME window: _iv_now() raises naming the bar if a bar is missing, which is
    # the behaviour we want, so it must be given exactly the bars the simulator will visit.
    # ------------------------------------------------------------------------
    kw = dict(underlying=UNDERLYING, reset_bars=RESET_BARS, rate=RATE, div_yield=DIV_YIELD,
              vol_mode="v2", iv_series=iv)
    kw.update(rule_kwargs)
    rule = rule_cls(**kw)

    # ------------------------------------------------------------------------
    # A FRESH Book and a FRESH NarrowLedger every call. PortfolioSimulator.run() resets rules but
    # NOT the book (simulator.py:48-50), so a reused book would carry the previous rung's dead
    # legs into this one — reinstating the very quadratic cost 13-01.1 exists to remove, and
    # compounding it across rungs.
    #
    # Starting capital = the window's first close, so every value reads in SPY price units and
    # the equity line is directly comparable to spot. Same convention as 16-03 and 13-01.
    # ------------------------------------------------------------------------
    sim = PortfolioSimulator(spot.to_frame(UNDERLYING),
                             [BuyAndHoldRule({UNDERLYING: 1.0}), rule],
                             book=Book(base_equity=float(spot.iloc[0])),
                             starting_capital=float(spot.iloc[0]),
                             ledger=NarrowLedger())
    t0 = time.perf_counter()
    state = sim.run()
    elapsed = time.perf_counter() - t0

    # The same loud guard 13-01's batch and overlay_runs use: legs proposed but never filled
    # means the hedged line would silently equal the unhedged one, which looks like a finding
    # and is a bug.
    if rule.events and not any(scoring.is_option_symbol(f.symbol, UNDERLYING) for f in sim.fills):
        raise RuntimeError("option legs were proposed but never filled")

    blotter = sim.blotter()
    # The rule's per-bar read-outs, keyed by TIMESTAMP rather than bar number so they join
    # straight onto the state frame and onto the blotter.
    history = pd.DataFrame(rule.history.values())
    if len(history):
        history = history.set_index("ts").sort_index()

    print(f"{len(spot):,} bars, {spot.index.min().date()} -> {spot.index.max().date()} | "
          f"{elapsed:.2f}s ({1000 * elapsed / len(spot):.2f} ms/bar) | "
          f"{len(state.columns)} ledger columns | {len(blotter)} fills | "
          f"{len(rule.events)} rule events")
    return state, blotter, history, rule, elapsed


# %% 4. fig_ladder — equity, spot, and every fill marked

# 4. fig_ladder

# Lifecycle marker styling. One entry per class, so adding a class is a one-line change and a
# missing class raises a KeyError rather than drawing an unlabelled grey dot.
_EVENT_STYLE = {
    OPEN:     dict(colour=theme.CATEGORICAL[2], symbol="triangle-up",   name="OPEN (struck)"),
    ROLL:     dict(colour=theme.CATEGORICAL[3], symbol="diamond",       name="ROLL (close + open)"),
    MONETISE: dict(colour=theme.CATEGORICAL[7], symbol="triangle-down", name="MONETISE (closed)"),
}


def fig_ladder(state, blotter, history, title="", reentry_iv=None, monetise_multiple=None):
    """SPY and strategy equity rebased to the window's first bar, with every fill marked.

    Four things have to be readable off ONE picture, which is why they are not four figures:
    what the strategy earned, what the market did, WHEN the hedge traded, and what the rule was
    thinking on the bar it traded. The first two are lines, the third is the markers, and the
    fourth is the hovertemplate.

    A FIFTH thing needs a panel of its own (13-01.1 finding 3). The re-entry gate is a
    comparison between a LEVEL (``reentry_iv``) and a SERIES (the real IV path), and a
    comparison is a picture, not a tooltip: the monetise/reopen thrash was diagnosed by reading
    fifteen hover boxes one at a time, when ``reentry_iv`` drawn across the IV path would have
    shown "the gate is nearly always open" at a glance. The lower panel shares the upper's
    x-axis, so a monetise marker lines up vertically with the vol that permitted it.

    And a SIXTH, which is the point of the whole exercise rather than a diagnostic: the long
    legs' value as a MULTIPLE OF THE PREMIUM PAID, in the middle panel. Finding 7 is the case
    where a put reached 1.97x and expired worthless; this panel is that ratio through time, so
    "the policy monetised at 5.3x" is read off a curve rather than taken on trust from a table.
    A reader can check a marker's hover against the series underneath it at the same x.

    Three panels, one shared x-axis, in the order a question is actually asked: what did the
    strategy do (equity), was the hedge worth anything when it acted (multiple), and was
    insurance cheap enough to replace (iv).

    ``reentry_iv`` and ``monetise_multiple`` are optional: pass the rung's levels to draw the
    gates, omit them on a rung that arms none and the panels show the paths alone.
    """
    # ------------------------------------------------------------------------
    # REBASE both series to 1.0 at the window's first bar. Without this, equity (which starts at
    # the first close, e.g. 128.76) and spot sit on the same axis at the same level and the
    # DIVERGENCE — the only thing the figure is for — is invisible inside the common level.
    # ------------------------------------------------------------------------
    spot = SPOT_FULL.loc[state.index]
    spot_rb = spot / float(spot.iloc[0])
    eq_rb = state["equity"] / float(state["equity"].iloc[0])

    # Three rows, ONE shared x-axis. row_heights favours the equity panel — the lower two are
    # read as context for a marker above them, never on their own, so they need to be legible
    # rather than large. shared_xaxes couples the zoom, which is what makes "line up the
    # monetise with the multiple it fired at and the vol that let it back in" a physical act
    # rather than an act of memory.
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.52, 0.24, 0.24], vertical_spacing=0.05,
                        subplot_titles=("equity and the market",
                                        "long-leg value as a multiple of premium paid",
                                        "implied vol and the re-entry gate"))
    fig.add_trace(go.Scatter(
        x=spot_rb.index, y=spot_rb.to_numpy(), name=f"{UNDERLYING} (rebased)",
        line=dict(color=theme.CATEGORICAL[0], width=1.6),
        hovertemplate="%{x|%Y-%m-%d}<br>SPY %{y:.3f}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=eq_rb.index, y=eq_rb.to_numpy(), name="hedged equity (rebased)",
        line=dict(color=theme.CATEGORICAL[1], width=1.8),
        hovertemplate="%{x|%Y-%m-%d}<br>equity %{y:.3f}<extra></extra>"), row=1, col=1)

    # ------------------------------------------------------------------------
    # THE MARKERS. Classified through scoring.classify_option_events — the SHARED definition, so
    # this figure and 13-01's Task 3 tables cannot disagree about what a roll is.
    # ------------------------------------------------------------------------
    events = classify_option_events(blotter, UNDERLYING)
    for kind, style in _EVENT_STYLE.items():
        rows = events[events["event"] == kind]
        if not len(rows):
            continue
        # Markers sit ON the equity line, not on a separate axis: the question being asked is
        # "what did equity do at the moment this traded", and an event rug underneath would
        # force the eye to travel to answer it.
        ts = pd.DatetimeIndex(rows["ts"])
        y = eq_rb.reindex(ts).to_numpy()

        # ----------------------------------------------------------------
        # THE HOVERTEXT is the whole point of persisting the rule history alongside the blotter.
        # Without it a marker is a dot on a line; with it the same dot reads "monetised at an
        # 11.4% drawdown with IV at 0.41", which is what makes a rung inspectable at all.
        # `reindex` rather than a join: every bar has a history row, so an event bar always
        # resolves — but reindex degrades to NaN instead of dropping the marker if that ever
        # stops being true. It did once: until 2026-09-22 the rule recorded history only on bars
        # holding legs, so the MONETISE bar (which clears its legs before the record runs) had
        # no row and these markers hovered "drawdown NaN, iv NaN" — on exactly the events the
        # figure exists to explain.
        # ----------------------------------------------------------------
        h = history.reindex(ts) if len(history) else pd.DataFrame(index=ts)
        custom = np.column_stack([
            rows["n_open"].to_numpy(), rows["n_close"].to_numpy(),
            rows["opened_premium"].to_numpy(), rows["closed_value"].to_numpy(),
            _col(h, "state", ts, ""), _col(h, "drawdown", ts, np.nan),
            _col(h, "multiple", ts, np.nan), _col(h, "iv", ts, np.nan),
        ])
        fig.add_trace(go.Scatter(
            x=ts, y=y, mode="markers", name=style["name"],
            marker=dict(color=style["colour"], symbol=style["symbol"], size=9,
                        line=dict(color=theme.INK, width=0.6)),
            customdata=custom,
            hovertemplate=(
                "<b>" + kind + "</b>  %{x|%Y-%m-%d}<br>"
                "equity %{y:.3f}<br>"
                "opened %{customdata[0]:.0f} leg(s) for %{customdata[2]:.2f}<br>"
                "closed %{customdata[1]:.0f} leg(s) for %{customdata[3]:.2f}<br>"
                "state %{customdata[4]}<br>"
                "drawdown %{customdata[5]:.1%}<br>"
                "multiple %{customdata[6]:.2f}x<br>"
                "iv %{customdata[7]:.3f}"
                "<extra></extra>")), row=1, col=1)

    # ------------------------------------------------------------------------
    # PANEL 2 — WHAT THE HEDGE WAS WORTH, as a multiple of what it cost. This is the panel that
    # turns the policy from an assertion into something checkable: a monetise marker in panel 1
    # is a dot, but the same bar read against this curve says "it closed at 5.3x premium".
    #
    # The series is the rule's own `multiple` (_long_value / _long_premium), NOT recomputed
    # here. Recomputing would let the figure and the trigger disagree about the one number the
    # trigger fires on — and a figure that disagrees with the thing it depicts is worse than no
    # figure. It is None while FLAT (no premium has been paid, so the ratio is undefined rather
    # than zero), which plots as a GAP: the breaks in this curve are the unhedged stretches, and
    # they should line up with the FLAT band in the panel below.
    # ------------------------------------------------------------------------
    mult_path = pd.to_numeric(history["multiple"], errors="coerce") if "multiple" in history \
        else None
    if mult_path is not None and mult_path.notna().any():
        fig.add_trace(go.Scatter(
            x=mult_path.index, y=mult_path.to_numpy(), name="value / premium",
            line=dict(color=theme.CATEGORICAL[5], width=1.5), connectgaps=False,
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}x premium<extra></extra>"), row=2, col=1)
        # BREAK-EVEN. Above this line the legs are worth more than they cost; below it the
        # premium is not yet earned back. Drawn on every rung because it is a property of the
        # ratio, not of any configuration's parameters.
        fig.add_hline(y=1.0, row=2, col=1,
                      line=dict(color=theme.INK, width=1.0, dash="dash"),
                      annotation_text="break-even (1.0x)", annotation_position="top left")
        # The multiple TRIGGER level, where one is armed. On a drawdown-triggered rung this is
        # absent by design: drawing a threshold the rung never tested would invite the reader to
        # explain the marks with a rule that was not running.
        if monetise_multiple is not None:
            fig.add_hline(y=float(monetise_multiple), row=2, col=1,
                          line=dict(color=theme.CATEGORICAL[4], width=1.2, dash="dot"),
                          annotation_text=f"monetise_multiple {float(monetise_multiple):.2f}x",
                          annotation_position="bottom left")
        # The monetise bars marked ON this curve as well as on equity. The question this panel
        # answers is "how far above its cost was the hedge when the policy took the money", and
        # that is a point on this line, not on the equity line above.
        mon = events[events["event"] == MONETISE] if len(events) else events
        if len(mon):
            mts = pd.DatetimeIndex(mon["ts"])
            fig.add_trace(go.Scatter(
                x=mts, y=mult_path.reindex(mts).to_numpy(), mode="markers",
                name="monetised at", showlegend=False,
                marker=dict(color=_EVENT_STYLE[MONETISE]["colour"],
                            symbol=_EVENT_STYLE[MONETISE]["symbol"], size=9,
                            line=dict(color=theme.INK, width=0.6)),
                hovertemplate="monetised %{x|%Y-%m-%d}<br>%{y:.2f}x premium<extra></extra>"),
                row=2, col=1)

    # ------------------------------------------------------------------------
    # PANEL 3 — THE RE-ENTRY GATE, 13-01.1's finding 3. The IV the rule actually priced and
    # decided with, taken from the rule's own history rather than re-read from the IV parquet:
    # a panel drawn from a different source than the rule used could disagree with it, and a
    # figure that disagrees with the thing it depicts is worse than no figure.
    # ------------------------------------------------------------------------
    iv_path = pd.to_numeric(history["iv"], errors="coerce") if "iv" in history else None
    if iv_path is not None and iv_path.notna().any():
        fig.add_trace(go.Scatter(
            x=iv_path.index, y=iv_path.to_numpy(), name="real IV",
            line=dict(color=theme.CATEGORICAL[2], width=1.4),
            hovertemplate="%{x|%Y-%m-%d}<br>iv %{y:.3f}<extra></extra>"), row=3, col=1)

        # The FLAT stretches, drawn as a filled band across the IV panel. This is the answer to
        # "why is it not hedged here", and it has to be a REGION rather than two markers: the
        # eye reads a shaded span as a duration, which is what a wait is.
        if "state" in history:
            flat = (history["state"] == "FLAT").to_numpy()
            lo, hi = float(np.nanmin(iv_path)), float(np.nanmax(iv_path))
            # Plotted as a masked series at the panel's top edge rather than as N shapes: one
            # trace with NaN gaps costs one legend entry and one draw, where a shape per stretch
            # would add an unbounded number of layout objects on a 5,201-bar run.
            band = np.where(flat, hi, np.nan)
            fig.add_trace(go.Scatter(
                x=iv_path.index, y=band, name="FLAT (unhedged)",
                mode="lines", line=dict(color=theme.CATEGORICAL[3], width=6),
                opacity=0.35, connectgaps=False,
                hovertemplate="%{x|%Y-%m-%d}<br>unhedged<extra></extra>"), row=3, col=1)

        # The gate LEVEL. Drawn only when one is armed — a dashed line at a level the rung never
        # tested would invite the reader to explain the path with a rule that was not running.
        if reentry_iv is not None:
            fig.add_hline(y=float(reentry_iv), row=3, col=1,
                          line=dict(color=theme.CATEGORICAL[4], width=1.2, dash="dot"),
                          annotation_text=f"reentry_iv {float(reentry_iv):.3f}",
                          annotation_position="top left")

    fig.update_layout(
        title=title or "Option ladder probe — every fill marked",
        hovermode="closest",
        # Plotly's default figure height is 450px regardless of row count — it sizes the FIGURE,
        # not the three panels inside it. Left unset, this 3-row figure rendered with each panel
        # squeezed into a third of 450px, and the published report then stretches the SAME figure
        # to ~1600px wide (build_report._CSS's `.figure` rule) while its height stays untouched —
        # exactly the crunched look flagged on the put-spread panel. `theme.STACKED_FIGURE_HEIGHT`
        # is the shared table `fig_overlay_values` and `fig_put_paths_market` already read this
        # from; this is the one stacked figure in the repo that had drifted from it.
        height=theme.STACKED_FIGURE_HEIGHT[3],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    fig.update_yaxes(title_text="rebased to window start (1.0)", row=1, col=1)
    fig.update_yaxes(title_text="x premium paid", row=2, col=1)
    fig.update_yaxes(title_text="implied vol", row=3, col=1)
    fig.update_xaxes(title_text=None, row=3, col=1)
    # Subplot titles are annotations, so they do not inherit the axis font. Sized down here so
    # they read as panel labels rather than three competing headlines under the real title.
    # The COLOUR is set for the same reason: apply_export_theme stamps `font`, `title_font`,
    # the legend and both axis families, but never annotations — so a panel label left on
    # Plotly's default near-black would vanish into the dark export surface.
    for ann in fig.layout.annotations:
        ann.font.size = 11
        ann.font.color = theme.INK
    # Ink and grid applied inline, backgrounds left transparent — the standing convention, so the
    # figure reads correctly in the interactive window without baking a dark template into it.
    _inline_theme(fig)
    return fig


def _col(frame, name, index, fill):
    """One history column as an array aligned to `index`, tolerant of it being absent.

    `iv` is None under v1 and `multiple` is None before any premium has been paid, so a column
    can be entirely missing or entirely null. A hovertemplate referencing a missing customdata
    slot renders as a literal "%{customdata[7]}", which looks like a bug in the figure rather
    than an absent number — so every slot is always present, filled if need be.
    """
    if name not in frame.columns:
        return np.full(len(index), fill, dtype=object if isinstance(fill, str) else float)
    col = frame[name]
    return col.to_numpy() if isinstance(fill, str) else pd.to_numeric(col, errors="coerce").to_numpy()


# %% 5. RUNG 3a — one struck hedge, no roll reached

# 5. RUNG 3a

# ============================================================================
# The simplest thing that can be wrong. `reset_bars` is set longer than the
# window, so the calendar never reaches a roll: exactly ONE put is struck, held,
# and settled at the end. A roll defect cannot hide inside this rung.
#
# WHAT TO LOOK FOR
#   * exactly one OPEN marker, near the window's start, at ~0.90 of that bar's spot
#   * equity diverging ABOVE spot as SPY falls — the put paying
#   * the tooltip's `iv` being REAL implied vol (0.2-0.8 through the GFC), not the
#     frozen v1 0.16
#   * a blotter with one option buy and nothing else unaccounted for
# ============================================================================

state_a, blot_a, hist_a, rule_a, _ = run_one(*GFC, floor=0.90, reset_bars=10_000)
fig_ladder(state_a, blot_a, hist_a,
           title="RUNG 3a — one protective put, struck once, GFC 2007-10 to 2009-03").show()
print(blot_a.to_string(index=False))

# %% 6. RUNG 3b — the same put, ROLLING every 63 bars

# 6. RUNG 3b

# ============================================================================
# Adds rolling and NOTHING else, so anything new in the picture is the roll.
#
# WHAT TO LOOK FOR
#   * ROLL markers as close-plus-open PAIRS on the same bar, ~63 bars apart
#   * each roll re-striking at 0.90 of the CURRENT spot, so the strikes walk DOWN
#     as SPY falls (read the symbols in the blotter — the strike is in the name)
#   * the equity step at each roll explainable as premium paid minus the value of
#     the leg closed, with nothing left over
# ============================================================================

state_b, blot_b, hist_b, rule_b, _ = run_one(*GFC, floor=0.90)
fig_ladder(state_b, blot_b, hist_b,
           title="RUNG 3b — protective put ROLLED every 63 bars, GFC").show()
print(blot_b.to_string(index=False))

# %% 7. RUNG 3c — rolling, with the drawdown monetisation trigger armed

# 7. RUNG 3c

# ============================================================================
# Adds the trigger and NOTHING else. The levels are 13-01's illustrative ones —
# hand-picked to exercise the mechanism, not selected, and not a claim.
#
# WHAT TO LOOK FOR
#   * a MONETISE marker at the FIRST bar at or past the 10% drawdown — confirm it
#     against the tooltip's own `drawdown` on that bar
#   * the book going FLAT and STAYING flat while the gate holds: check `state` and
#     `iv` in the tooltips through the vol spike
#   * the reopen happening on the gate (iv back to 0.18) or on max_flat_bars (126),
#     and being the one you intended
#   * versus 3b: is the difference in equity plausibly the monetisation? A trigger
#     that HURTS through the GFC is a finding; one that fires on the wrong bar is a bug
# ============================================================================

state_c, blot_c, hist_c, rule_c, _ = run_one(
    *GFC, floor=0.90, monetise_drawdown=0.10, reentry_iv=0.18, max_flat_bars=126)
fig_ladder(state_c, blot_c, hist_c,
           title="RUNG 3c — rolled put with monetise_drawdown=0.10, GFC").show()
print(blot_c.to_string(index=False))
print(rule_c.events_frame().to_string(index=False))

# %% 8. RUNG 3d — the same configuration over the FULL history

# 8. RUNG 3d

# ============================================================================
# 5,201 bars, 2006-01-09 to 2026-09-18, ~82 rolls. Expect SECONDS: this is the
# measurement 13-01's AC-6 records and 13-02's AC-1 multiplies. If it is minutes,
# something has reintroduced the per-leg ledger columns and the ladder stops here
# rather than climbing on a broken constant.
#
# This is also the first picture in which a SLOWLY ACCUMULATING defect is visible
# at all — drift in the roll cadence, strikes walking somewhere they should not,
# a trigger that stops firing after some number of periods.
# ============================================================================

state_d, blot_d, hist_d, rule_d, elapsed_d = run_one(
    floor=0.90, monetise_drawdown=0.10, reentry_iv=0.18, max_flat_bars=126)
print(f"\nSINGLE-RUN CONSTANT: {elapsed_d:.2f}s over {len(state_d):,} bars "
      f"({1000 * elapsed_d / len(state_d):.2f} ms/bar) — record this in the SUMMARY")
fig_ladder(state_d, blot_d, hist_d,
           title="RUNG 3d — the same configuration, 2006-2026 (IN-SAMPLE)").show()
print(classify_option_events(blot_d, UNDERLYING)["event"].value_counts().to_string())

# %% 9. RUNG 3e — the three structures over the GFC, and the figures written out

# 9. RUNG 3e — THE ANALYSIS

# ============================================================================
# THE AMENDED DELIVERABLE (13-01, amendment 2026-09-22). The GFC window is the
# headline and the full history the appendix, because a table of final values
# over twenty years cannot show a policy DECIDING and fifteen months of picture
# can. Every structure is run through the SAME run_one with one parameter
# changed — the rule class — so a difference between two panels is a difference
# between two structures and not between two harnesses.
#
# The monetisation policy is held FIXED across all three at 13-01's illustrative
# levels. This is deliberately NOT a parameter study: the levels were hand-picked,
# they are in-sample, and the moment anyone starts choosing between these rows on
# these numbers they are doing 13-02's work on evidence that cannot support it.
# What the comparison CAN say is whether the mechanism behaves sensibly on a
# structure that is not a plain long put.
#
# Writes one self-contained HTML per configuration, plus ONE rendered report that embeds all
# four full-width at the section discussing them — the prose lives in
# research/validation/option_monetisation_gfc.md and is RENDERED, never retyped, through the same
# build_report 10-01 built for the probe write-up (13-01 amendment, 2026-09-26). The standalone
# per-configuration pages stay: opening one by double-clicking the file needs no kernel and no
# report around it. Every page references the single vendored plotly.min.js at docs/assets/
# rather than inlining ~3.5 MB per file or pulling the library from a CDN — so the pages are tens
# of KB AND render with no network connection. (They used to use include_plotlyjs="cdn"; the
# 2026-09-26 amendment moved the library up to docs/assets/ to be shared with the probing
# section, at which point depending on a CDN bought nothing.)
# ============================================================================

# Resolved from the batch pipeline rather than recomputed from __file__: this script is
# executed cell by cell in a session, where __file__ may not be defined at all, and the
# batch already owns the one definition of where the project root is.
DOCS_DIR = omb.PROJECT_ROOT / "docs" / "validation"

# The per-configuration pages sit in a figures/ directory BESIDE the section's index page, the same
# shape as docs/probing/figures/ — so a section is one unit and a third section is a directory
# rather than a new layout (13-01 amendment, 2026-09-26).
FIGURES_DIR = DOCS_DIR / "figures"

# The ONE vendored plotly.min.js at docs/assets/, spelled relative to a page in
# docs/validation/figures/. Replaces include_plotlyjs="cdn": a page that needs a network connection
# to draw its own evidence is not a self-contained deliverable, and the library is committed anyway
# for the probing section. A page written at docs/validation/index.html needs "../assets/..."
# instead — one level less — which is why this is a constant per output directory and not a global.
PLOTLY_SRC = "../../assets/plotly.min.js"

# The illustrative policy, one definition shared by every structure below so they cannot drift.
GFC_POLICY = dict(monetise_drawdown=0.10, reentry_iv=0.18, max_flat_bars=126)

# floor=0.90 throughout. spread_width and cap are 12-01's defaults, carried as-is: this plan
# makes no claim about them, and choosing them here would be the parameter study it is not.
GFC_CONFIGS = {
    "protective_put": dict(
        rule_cls=ProtectivePutRule, floor=0.90, **GFC_POLICY,
        _label="Protective put — monetise at 10% drawdown, re-enter under 0.18 IV"),
    "put_spread": dict(
        rule_cls=PutSpreadRule, floor=0.90, spread_width=0.80, **GFC_POLICY,
        _label="Put spread 0.90/0.80 — same monetisation policy"),
    "collar": dict(
        rule_cls=RollingCollarRule, floor=0.90, cap=1.28, **GFC_POLICY,
        _label="Rolling collar 0.90/1.28 — same monetisation policy"),
    # The control. Same structure as the first row with NO policy armed, so the difference
    # between these two panels is the monetisation and nothing else.
    "blind_roll_control": dict(
        rule_cls=ProtectivePutRule, floor=0.90,
        _label="Protective put, BLIND ROLL — no monetisation (the control)"),
}

# AC-6's blotter artefact, persisted for the GFC-window configurations this script already runs.
# `outputs/` is gitignored (.gitignore:37) so this is scratch, not a committed deliverable — the
# COMMITTED evidence is the report's embedded figures; this is what 13-02/13-03 or a later session
# would read back without re-running the sim. Nested under "gfc/" rather than sharing
# `omb.BLOTTER_DIR` directly: that constant is the FULL-HISTORY batch's own directory (13-01 Task
# 3), and the two runs use different window, different capital base and (for three of these four
# configs) different rule classes — a `protective_put.csv` from one run silently overwriting the
# other's would be indistinguishable from a real result once written.
GFC_BLOTTER_DIR = omb.OUT_DIR / "gfc" / "blotter"

# A single-config FULL-HISTORY smoke test, kept separate from both `GFC_BLOTTER_DIR` above and
# from `omb.SERIES_DIR`/`omb.BLOTTER_DIR` (the batch's own 7-config named set). Those two already
# don't collide on filenames with each other or with this — but an ad hoc single-config run is
# not the batch's canonical AC-6 deliverable, and giving it its own directory means it can never
# be mistaken for one, however it is later named.
FULL_OUT_DIR = omb.OUT_DIR / "full"
FULL_SERIES_DIR = FULL_OUT_DIR / "series"
FULL_BLOTTER_DIR = FULL_OUT_DIR / "blotter"

# ONE shared, APPENDED log of every measured run — GFC-window and full-history alike. Runtime was
# previously only ever printed to stdout and lost to terminal scrollback the moment the session
# ended; this is the retrievable record the user asked for. Appended rather than overwritten: the
# log's whole point is to answer "how long did X take, and when did we last measure it", which an
# overwrite would destroy for every row but the last run's.
RUNTIME_LOG = omb.OUT_DIR / "runtime_log.csv"


def _log_runtime(name, window_label, n_bars, elapsed):
    """Append one measured run to RUNTIME_LOG. See RUNTIME_LOG's own comment for why appended."""
    row = pd.DataFrame([{
        "configuration": name,
        "window": window_label,
        "bars": n_bars,
        "elapsed_s": round(elapsed, 4),
        "ms_per_bar": round(1000 * elapsed / n_bars, 4),
        # Wall-clock, not the run's own window — this is WHEN it was measured, so a stale
        # measurement (superseded by a later engine fix) can be told apart from a fresh one
        # without opening the file and comparing it against git log by hand.
        "measured_at": pd.Timestamp.now().isoformat(timespec="seconds"),
    }])
    RUNTIME_LOG.parent.mkdir(parents=True, exist_ok=True)
    row.to_csv(RUNTIME_LOG, mode="a", header=not RUNTIME_LOG.exists(), index=False)


def export_full_history_probe(name, out_series_dir=FULL_SERIES_DIR,
                              out_blotter_dir=FULL_BLOTTER_DIR):
    """Run ONE named GFC_CONFIGS configuration over the FULL real-IV history (2006-01-09 to
    2026-09-18, ~5,201 bars) and persist it exactly like the GFC window does: series, blotter,
    and a runtime row. This is deliberately ONE configuration, not the batch's whole named set —
    a single full-history run costs ~1s (AC-6's own measurement), so running configs one at a
    time and inspecting each before the next is cheap, where the user has explicitly asked NOT to
    run the whole batch blind.

    `name` must be a key in GFC_CONFIGS — the SAME config dict the GFC window already runs, so
    "protective put over the whole window" is provably the same rule, the same floor and the
    same monetisation policy as the GFC panel already published, with only the window changed.
    """
    cfg = dict(GFC_CONFIGS[name])
    cfg.pop("_label")
    # No start/end -> run_one slices SPOT_FULL/IV_FULL with `.loc[None:None]`, i.e. the full
    # window already loaded at import time — the same series rung 3d in this file's cells uses.
    state, blot, hist, rule, elapsed = run_one(**cfg)

    out_series_dir.mkdir(parents=True, exist_ok=True)
    out_blotter_dir.mkdir(parents=True, exist_ok=True)
    # omb.build_series_frame is the SAME join the batch's own named-set run uses — one
    # implementation, so a full-history series produced here cannot disagree in shape with one
    # the batch script produces for a different configuration.
    series = omb.build_series_frame(state, rule, SPOT_FULL.loc[state.index])
    series.to_parquet(out_series_dir / f"{name}.parquet")
    # AC-6's own definition of the blotter (sim.blotter()'s shape), matching the GFC blotters
    # already persisted above rather than the batch script's rule.events_frame() (see that
    # constant's own note on the two NOT being the same artefact).
    blot.to_csv(out_blotter_dir / f"{name}.csv", index=False)
    _log_runtime(name, "full-history", len(state), elapsed)

    print(f"{name} over the full history: {len(state):,} bars, {state.index.min().date()} -> "
          f"{state.index.max().date()} | {elapsed:.2f}s ({1000 * elapsed / len(state):.2f} ms/bar)")
    print(f"  series  -> {out_series_dir / f'{name}.parquet'}")
    print(f"  blotter -> {out_blotter_dir / f'{name}.csv'} ({len(blot)} fills)")
    print(f"  runtime -> {RUNTIME_LOG}")
    return series, blot, elapsed


def load_persisted_full_run(name, series_dir=FULL_SERIES_DIR, blotter_dir=FULL_BLOTTER_DIR,
                            runtime_log=RUNTIME_LOG):
    """Reload one persisted full-history run from disk — NO simulator, no live session state.

    This is the "look back on a persisted artefact" reader: everything it returns comes from the
    three files `export_full_history_probe` wrote, read fresh. It exists so inspecting a run does
    not depend on still having the in-memory objects from whenever it was produced — the whole
    point of persisting is that a LATER session (or a later cell in this one) can read it back.

    Returns ``(series, blot, runtime_row)``:
      * ``series``      — the joined per-bar frame (equity, ret, spot, spot_ret, and the rule's
                          own state/drawdown/multiple/iv/gate_open/flat_bars/net_value/
                          net_premium), exactly `build_series_frame`'s shape
      * ``blot``         — the bare fill ledger (ts, symbol, side, qty, price, notional)
      * ``runtime_row``  — the MOST RECENT matching row from RUNTIME_LOG (bars, elapsed_s,
                          ms_per_bar, measured_at), so a reload always reports how long the run
                          that produced these files actually took

    `series` alone is enough to redraw the full three-panel figure `fig_ladder` builds for a live
    run: it carries every column `fig_ladder` reads off `state` (just `equity`) AND every column
    it reads off `history` (state/drawdown/multiple/iv), because `build_series_frame` is what
    joined the rule's per-bar history into `state` in the first place. Passing the SAME frame as
    both `state=` and `history=` reconstructs the identical picture with no re-simulation — see
    the cell below.
    """
    series = pd.read_parquet(series_dir / f"{name}.parquet")
    blot = pd.read_csv(blotter_dir / f"{name}.csv", parse_dates=["ts"])
    runtime = pd.read_csv(runtime_log)
    matches = runtime[(runtime["configuration"] == name) & (runtime["window"] == "full-history")]
    if len(matches) == 0:
        raise FileNotFoundError(
            f"no full-history runtime row logged for {name!r} — was export_full_history_probe "
            f"ever run for it? (checked {runtime_log})")
    # LAST match, not first: the log is append-only, so a config run twice (e.g. after an engine
    # fix) has an earlier, superseded row above a later, current one — the same staleness trap
    # the 2026-09-20 series files fell into, guarded against here rather than repeated.
    runtime_row = matches.iloc[-1]
    return series, blot, runtime_row


def premium_financing_summary(blot, underlying=UNDERLYING):
    """How much of the long leg's premium the short leg financed, averaged across every
    OPEN/ROLL event in a run — for a TWO-LEG structure (put spread, collar) only.

    `classify_option_events` aggregates a bar's fills together, so its `opened_premium` is the
    GROSS of both legs and cannot answer "what did the short leg pay for" at all. This uses the
    per-FILL classification underneath it instead: for these rules an OPENING fill with side=+1
    is always the long leg (the only way a position moves further from flat on a BUY) and an
    OPENING fill with side=-1 is always the short leg — see `classify_option_fills`'s own
    docstring for why that distinction is reliable here.

    Returns ``None`` for a single-leg structure (protective_put, blind_roll_control — no short
    leg exists to finance anything) or a dict: ``n`` (opening events averaged over),
    ``long_premium`` (mean $ paid for the long leg per event), ``short_credit`` (mean $ received
    for the short leg per event), ``pct_financed`` (``short_credit / long_premium``, as a
    percentage of what the long leg alone would have cost).
    """
    fills = classify_option_fills(blot, underlying)
    opens = fills[fills["opening"]]
    long_prem = opens.loc[opens["side"] == 1, "notional"]
    short_prem = opens.loc[opens["side"] == -1, "notional"]
    if len(long_prem) == 0 or len(short_prem) == 0:
        return None
    long_mean = float(long_prem.mean())
    short_mean = float(short_prem.mean())
    return {
        "n": len(long_prem),
        "long_premium": long_mean,
        "short_credit": short_mean,
        "pct_financed": short_mean / long_mean * 100.0,
    }


# The write-up build_report renders, and the heading -> figure map it splices figures into.
# Headings are the INTERFACE here — a renamed "## Protective put — ..." in the Markdown without
# the matching update here fails the build (build_report._split_sections), on purpose: a figure
# under the wrong heading is worse than no figure.
GFC_WRITE_UP = omb.PROJECT_ROOT / "research" / "validation" / "option_monetisation_gfc.md"
GFC_FIGURE_AT = {
    "Protective put":  "protective_put",
    "Put spread":      "put_spread",
    "Rolling collar":  "collar",
    "The control":     "blind_roll_control",
}


def export_gfc_analysis(out_dir=DOCS_DIR, window=GFC, show=False, figures_dir=None):
    """Run every GFC configuration, write a figure per configuration plus an index.

    Returns the summary frame it also prints, so a session can keep working with it.
    """
    figures_dir = FIGURES_DIR if figures_dir is None else figures_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    GFC_BLOTTER_DIR.mkdir(parents=True, exist_ok=True)
    summary, written = [], []
    # Kept alongside `written` (the standalone-page records) rather than replacing it: the
    # standalone pages stay so a figure can be opened by double-clicking a file with no kernel,
    # and THIS dict is what gets handed to build_report to embed the same figures inline, full
    # width, in the report below. One figure object serves both — it is never re-rendered twice.
    figs = {}
    # The premium-financing sentences for the two TWO-LEG structures, spliced into the write-up's
    # prose (see the extra_html call below). Built alongside `figs` rather than after the loop:
    # `premium_financing_summary` needs `blot`, which only exists per-config inside this loop.
    financing_html = {}
    for name, cfg in GFC_CONFIGS.items():
        cfg = dict(cfg)
        label = cfg.pop("_label")
        state, blot, hist, rule, elapsed = run_one(*window, **cfg)
        # Retrievable runtime — same log `export_full_history_probe` appends to, so the GFC-window
        # and full-history measurement of the SAME configuration sit in one place to compare.
        _log_runtime(name, f"GFC {window[0]} to {window[1]}", len(state), elapsed)

        # The gates are passed to the figure ONLY where the configuration armed them, so a
        # panel never draws a threshold the run did not test.
        fig = fig_ladder(state, blot, hist, title=f"{label}  ·  GFC {window[0]} to {window[1]}",
                         reentry_iv=cfg.get("reentry_iv"),
                         monetise_multiple=cfg.get("monetise_multiple"))
        if show:
            fig.show()
        # ----------------------------------------------------------------
        # THE EXPORT THEME. `fig_ladder` ends with `_inline_theme`, which stamps INK and GRID
        # but deliberately leaves the backgrounds TRANSPARENT — correct in the interactive
        # window, where VS Code's own dark theme shows through. A SAVED page has no dark div
        # behind it, so the browser paints its own white and a light-ink figure becomes
        # unreadable. `apply_export_theme` swaps the two backgrounds to the opaque dark surface;
        # it is the same call `pipelines/option_probe_figures.main()` makes before every
        # `write_html`, and it is not optional here for exactly that reason.
        #
        # `show=True` above is left on the INLINE figure on purpose: the order means a session
        # that both shows and writes sees the transparent version and saves the opaque one.
        # ----------------------------------------------------------------
        theme.apply_export_theme(fig)
        # Widen the legend gap for the full-width render. No-op unless the figure carries the
        # inline secondary-axis legend x, which this one does not — kept for parity with the
        # probe pipeline so the two export paths cannot drift.
        theme.apply_export_spacing(fig)
        path = figures_dir / f"{name}.html"
        # `full_html=True` (the default) means Plotly writes the <body>, and its <body> has no
        # background of its own: the figure div is dark but the margin around it is browser
        # white. `theme.darken_page` injects the one CSS rule that makes the PAGE dark rather
        # than just the plot rectangle — shared with the probe pipeline's index so both surfaces
        # are the same #111.
        fig.write_html(path, include_plotlyjs=PLOTLY_SRC)
        path.write_text(theme.darken_page(path.read_text(encoding="utf-8")), encoding="utf-8")
        written.append((name, label, path))
        figs[name] = fig

        # THE BLOTTER, persisted. AC-6 defines it exactly as `sim.blotter()` already returns it —
        # ts, symbol, side, qty, price, notional — which is the SAME frame this loop already holds
        # as `blot` to draw the figure's markers. Nothing is re-derived; the sim was already run
        # once per config, and this is that run's fill ledger written to disk rather than
        # discarded after the chart is drawn.
        blot.to_csv(GFC_BLOTTER_DIR / f"{name}.csv", index=False)

        # THE FINANCING SENTENCE. `None` for a single-leg structure (protective_put,
        # blind_roll_control) — there is no short leg to write a sentence about, and the
        # placeholder for those configs is simply never populated (see `_split_sections`'s own
        # note: an UNMATCHED placeholder is only an error for a FIGURE, not for extra_html).
        financing = premium_financing_summary(blot)
        if financing is not None:
            financing_html[f"<!--STAT:{name}_financing-->"] = (
                f"<p>Across the {financing['n']} times this structure opened over the window, "
                f"the long put cost <b>${financing['long_premium']:.2f}</b> on average and the "
                f"short leg financed <b>${financing['short_credit']:.2f}</b> of that — "
                f"<b>{financing['pct_financed']:.0f}%</b> of the long leg's own cost.</p>")

        ev = classify_option_events(blot, UNDERLYING)["event"].value_counts()
        mons = [e for e in rule.events if e["action"] == "monetise"]
        eq = state["equity"]
        summary.append({
            "configuration": name,
            # Final value in SPY price units, against buy-and-hold over the same window.
            "final_value": round(float(eq.iloc[-1]), 3),
            "return": round(float(eq.iloc[-1] / eq.iloc[0] - 1), 4),
            "max_drawdown": round(float((eq / eq.cummax() - 1).min()), 4),
            "rolls": int(ev.get(ROLL, 0)),
            "monetisations": len(mons),
            # The number the amended plan is read for: how far above its cost the hedge was
            # when the policy took the money.
            "best_multiple": (round(max(m["multiple"] for m in mons), 2) if mons else None),
            # How much of the window it spent with no hedge on, which is the cost of waiting.
            "flat_share": round(float((hist["state"] == "FLAT").mean()), 4) if len(hist) else 0.0,
        })

    table = pd.DataFrame(summary).set_index("configuration")

    # ------------------------------------------------------------------------
    # THE REPORT. Was a hand-rolled link list; is now a rendered write-up with each figure
    # embedded full-width at the section that discusses it — the same shape 10-01 chose for the
    # probe, generalised so both reports run through ONE implementation (13-01 amendment,
    # 2026-09-26). The summary table is GENERATED here and spliced in, never typed into the
    # Markdown, so no number in it can go stale relative to the run that produced it.
    #
    # `figs` already carries `apply_export_theme`/`apply_export_spacing` from the loop above;
    # `build_report` applies both again to every figure it is handed, which is a no-op here and
    # is what lets it also be called with figures that have NOT been through that step yet.
    # ------------------------------------------------------------------------
    build_report(
        figures=figs,
        write_up=GFC_WRITE_UP,
        out_path=out_dir / "index.html",
        figure_at=GFC_FIGURE_AT,
        # NOT `PLOTLY_SRC` — that constant is relative to a page in figures_dir (two levels below
        # docs/), and this report sits at out_dir/index.html, one level below docs/. The same trap
        # build_report's own docstring names as "the single most likely thing to get wrong when a
        # page moves": it bit here on the first pass (script tag pointed at a directory that does
        # not exist, so the vendored library never loaded).
        plotly_src="../assets/plotly.min.js",
        title="Option monetisation over the GFC — Portfolio Manager",
        heading="Option monetisation over the GFC",
        header_note=("Figures are fully interactive — hover, zoom and legend toggles all work, "
                     "with no server. Generated from {source_link} by "
                     "<code>python -m research.validation.option_ladder_probe</code> on {built}."),
        footer_note=("Portfolio Manager — thematic-fundamental research engine. "
                     "Priced with REAL implied vol from IBKR, not a synthetic surface. "
                     "IN-SAMPLE, one window, illustrative levels — see “What this does and "
                     "does not support” above for what that does and does not carry."),
        extra_html={"<!--TABLE:summary-->": f'<div class="table-wrap">{table.to_html()}</div>',
                   **financing_html},
    )

    print(f"\nwritten to {out_dir}:")
    for n, _, path in written:
        print(f"  figures/{path.name}")
    print("  index.html")
    print()
    print(table.to_string())
    return table

# %% 10. RUNG 3f — one configuration, the FULL real-IV history, persisted

# 10. RUNG 3f — full-history smoke test

# ============================================================================
# TRACTABILITY CHECK, run cell by cell. The GFC window (rungs 3a-3e) is 377 bars; this runs the
# SAME configuration over the full 5,201-bar history and writes it to disk, so a session can
# confirm "how long does one config actually take over the whole window" without touching the
# batch script's 7-config named set at all — a single run measured well under 2 seconds (AC-6),
# so running several one at a time, inspecting each before starting the next, costs nothing like
# the multi-hour risk of a blind batch run.
#
# Change NAME to any key in GFC_CONFIGS to probe a different structure the same way.
# ============================================================================

NAME = "protective_put"
full_series, full_blot, full_elapsed = export_full_history_probe(NAME)

# %% 11. Reload that run from disk — no re-run, no live session state required

# 11. Reload a persisted full-history run

# ============================================================================
# THE "LOOK BACK ON WHAT WE PERSISTED" CELL. Everything below is read fresh from the three files
# the cell above wrote — it does not touch `full_series`/`full_blot` from above, on purpose, so
# this cell also works stood alone at the START of a fresh session, days later, with nothing run
# first except the imports and Cell 2 (which only loads price/IV data, never re-simulates).
#
# `fig_ladder` is the SAME three-panel builder every other rung and the published report use —
# reused here rather than duplicated, because `series` (from `load_persisted_full_run`) already
# carries every column it reads off BOTH its `state` argument (equity) and its `history` argument
# (state/drawdown/multiple/iv): `build_series_frame` joined the rule's per-bar diagnostics into
# `state` when this was first persisted, so passing the ONE reloaded frame as both arguments
# reconstructs the identical picture.
# ============================================================================

reloaded_series, reloaded_blot, reloaded_runtime = load_persisted_full_run(NAME)
print(f"reloaded {NAME}: {reloaded_runtime['bars']:,} bars, "
      f"{reloaded_runtime['elapsed_s']:.2f}s ({reloaded_runtime['ms_per_bar']:.2f} ms/bar), "
      f"measured {reloaded_runtime['measured_at']}")
# EQUITY max drawdown (peak-to-trough of the strategy's own value), NOT `reloaded_series
# ["drawdown"]` — that column is the RULE's own trigger reference, which RESETS on every reopen
# (the 2026-09-21 decision), so its minimum understates a genuine multi-episode drawdown. Same
# computation export_gfc_analysis's summary table uses, so the two numbers are comparable.
eq = reloaded_series["equity"]
print(f"final equity {eq.iloc[-1]:.2f}, "
      f"max drawdown {float((eq / eq.cummax() - 1).min()):.1%}, {len(reloaded_blot)} fills")

fig_ladder(reloaded_series, reloaded_blot, reloaded_series,
          title=f"{NAME} — persisted full-history run, reloaded from disk, "
                f"no re-simulation").show()

# %% 12. RUNG 3g — the other two structures, full history, runtime only

# 12. RUNG 3g — put spread and collar, full-history tractability

# ============================================================================
# Same tractability check as Cell 10, for the two other STRUCTURES (different rule classes, not
# just different parameters) — put spread and collar. `blind_roll_control` is deliberately left
# out here: it is `protective_put` with the monetisation policy switched off, the SAME rule class
# Cell 10 already timed, not a third structure. Runtime only — no reload/replot cell for either,
# per instruction; `load_persisted_full_run` is there if that changes later, unchanged from Cell
# 11's use of it.
# ============================================================================

for _name in ("put_spread", "collar"):
    export_full_history_probe(_name)

# %%
