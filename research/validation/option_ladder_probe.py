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
from portutils.strategies.rules.options import ProtectivePutRule
from portutils.viz import theme

import portutils.strategies.scoring as scoring
from portutils.strategies.scoring import MONETISE, OPEN, ROLL, classify_option_events

# The loaders and the shared carry constants live in 13-01's batch runner and the probe figure
# module. Imported, never re-derived: a second copy of "which bars are the run window" is a
# second thing to get wrong.
import pipelines.option_monetisation_batch as omb
from pipelines.option_monetisation_batch import RESET_BARS, load_window
from pipelines.option_probe_figures import DIV_YIELD, RATE, UNDERLYING, _inline_theme

# %% Reload custom package

# Reload custom package

# Reload modules during development (re-import names after reload). The classifier is what
# actually gets iterated on here; the ledger and the rules underneath it are stable.
importlib.reload(scoring)
importlib.reload(omb)
from portutils.strategies.scoring import MONETISE, OPEN, ROLL, classify_option_events  # noqa: E402

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

def run_one(start=None, end=None, **rule_kwargs):
    """One configuration over one window. Returns everything needed to draw it.

    Every rung of the ladder calls THIS function with different arguments. A rung that worked
    through a different code path would prove nothing about the rung above it.

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
    rule = ProtectivePutRule(**kw)

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
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    fig.update_yaxes(title_text="rebased to window start (1.0)", row=1, col=1)
    fig.update_yaxes(title_text="x premium paid", row=2, col=1)
    fig.update_yaxes(title_text="implied vol", row=3, col=1)
    fig.update_xaxes(title_text=None, row=3, col=1)
    # Subplot titles are annotations, so they do not inherit the axis font. Sized down here so
    # they read as panel labels rather than three competing headlines under the real title.
    for ann in fig.layout.annotations:
        ann.font.size = 11
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
