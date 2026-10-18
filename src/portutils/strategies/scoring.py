"""
SCORING — window definitions, the ranking metric, and the option lifecycle classifier.

``classify_option_events`` was written under 13-01.1, which needed it for the ladder probe. The
indicative windows and the floored-Calmar metric below are 13-02 Task 1's work and landed in this
same file (2026-10-17), which is what the module was created early and nearly empty FOR — so the
classifier had a permanent home from the start rather than living in a research script and being
moved later. Same reasoning ``strategies/__init__.py`` records for ``instruments/``.

WHY THE CLASSIFIER IS LIBRARY CODE AND NOT PART OF THE PROBE
------------------------------------------------------------
Two consumers need to agree on what a "roll" is: 13-01's Task 3 tables, and 13-02's per-window
path panels. Two definitions that drift apart is precisely the kind of defect that stays invisible
until a figure contradicts a table, and then costs a session to find. One definition, imported by
both.

WHY THE BLOTTER IS THE SOURCE AND NOT THE LEDGER
-------------------------------------------------
Under ``NarrowLedger`` (13-01.1) the per-bar state frame carries no per-leg columns at all, so
there is no ``"SPY P 450.00 @b2520_position"`` to read a lifecycle off. The blotter is the
replacement, and it is the better source anyway: it is what the book ACTUALLY traded, at the price
it actually traded at, rather than a snapshot that has to be differenced to recover the same fact.

Pure: no IO, no simulator, no plotting. Everything here takes frames and returns frames.
"""

import numpy as np
import pandas as pd

# The three lifecycle classes a bar can carry. Named constants rather than bare strings so a
# typo in a figure's colour map is an AttributeError rather than a silently unmatched category.
OPEN = "OPEN"
ROLL = "ROLL"
MONETISE = "MONETISE"

# ============================================================================
# THE INDICATIVE WINDOW SET (13-02 Task 1, AC-4).
#
# WHY THE CALM WINDOWS ARE NOT OPTIONAL AND NOT DECORATION.
# Every drawdown window is a window in which MORE protection wins, because the
# premium drag never gets a chance to hurt inside it — the hedge pays and the
# carry it cost over the preceding quiet years happened outside the slice. So a
# crisis-only window set does not rank hedges: it selects, mechanically, for the
# most expensive hedge available. The calm windows are what make the ranking a
# trade-off rather than a tautology, which is why AC-4 requires BOTH kinds and
# why the headline table is sorted by the WORSE of the two ranks (see
# `rank_configs`) instead of by their average — a combination that tops the
# crisis set and sits at the bottom of the calm set has not found anything.
#
# WHY THESE DATES. The three drawdown windows are the three the v0.4 claim rests
# on and the three 11-01's COVERAGE.md confirms are covered. Nothing before
# 2006-01-09 appears at all: real OPTION_IMPLIED_VOLATILITY history starts there
# (11-01), so v2 pricing cannot reach 2000 and the window set must not pretend
# it can. The calm windows are chosen as long stretches with no drawdown deeper
# than roughly 10%, one per decade of the usable history, so the carry cost is
# measured in more than one rate and vol environment.
#
# KNOWN DUPLICATION, flagged rather than fixed. `option_monetisation_batch.py`
# carries its own `DRAWDOWN_WINDOWS` with the same three date pairs, for its own
# per-episode P&L columns. The dates are identical today. It is NOT pointed at
# this mapping because that file is 13-01's shipped engine demonstration and
# re-pointing it would put this plan's edit inside its output path for no gain
# here. If a date ever changes, change both.
# ============================================================================

DRAWDOWN = "drawdown"
CALM = "calm"

INDICATIVE_WINDOWS = {
    # --- Drawdown regimes: the loss events the insurance exists for. ---
    "GFC 2007-2009": ("2007-10-01", "2009-03-31", DRAWDOWN),
    "COVID 2020": ("2020-02-01", "2020-03-31", DRAWDOWN),
    "Rates 2022": ("2022-01-01", "2022-10-31", DRAWDOWN),
    # --- Calm regimes: where the premium drag is the only thing happening. ---
    "Calm 2013-2015": ("2013-01-01", "2015-06-30", CALM),
    "Calm 2017": ("2017-01-01", "2017-12-31", CALM),
    "Calm 2023-2024": ("2023-01-01", "2024-12-31", CALM),
}

# The Calmar denominator floor. ARBITRARY, and surfaced as such everywhere it is used: a calm
# window whose max drawdown is 3% would otherwise produce an enormous and meaningless ratio that
# dominates any surface fit, so the denominator is floored — but a score SET BY THE FLOOR is not a
# Calmar, which is why `calmar` returns `floor_bound` alongside it and why the share of
# floor-bound rows is a mandatory output (AC-3). Never raise this silently to make a grid look
# better; report the share instead.
DRAWDOWN_FLOOR = 0.02


def is_option_symbol(symbol, underlying):
    """True for a synthetic option leg on ``underlying``, false for the underlying itself.

    ``OptionLeg.symbol`` is ``f"{underlying} {right} {strike:.2f} @b{expiry_bar}"``
    (``instruments/options.py:101``), so every leg starts with the underlying followed by a
    space while the underlying's own ticker never contains one. That is the same test the
    existing "legs proposed but never filled" guards use, kept identical here on purpose: a
    second, cleverer symbol test that disagreed with those guards would make a run that passed
    the guard produce an empty event table.
    """
    return symbol != underlying and symbol.startswith(f"{underlying} ")


def classify_option_fills(blotter, underlying):
    """Per-FILL classification: is this fill opening or closing a leg, and which side of the
    market was it on. Extracted from ``classify_option_events`` (2026-09-26) so a caller that
    needs the LEG-LEVEL detail — e.g. "what did the long put cost against what the short leg
    financed", which the bar-level table below cannot answer because it sums both legs
    together — reuses the exact same opening/closing test rather than re-deriving it a second
    way that could disagree.

    Parameters
    ----------
    blotter : DataFrame
        ``PortfolioSimulator.blotter()`` — one row per fill, with ``ts``, ``symbol``, ``side``,
        ``qty``, ``price`` and ``notional``.
    underlying : str
        See ``classify_option_events``.

    Returns
    -------
    DataFrame
        One row per option FILL (not per bar), with ``ts``, ``symbol``, ``side`` (+1 buy / -1
        sell, carried through unchanged — this is what tells a long leg's OPEN from a short
        leg's, since a long leg always opens on a BUY and a short leg always opens on a SELL),
        ``opening`` (bool) and ``notional`` (absolute cash magnitude of this one fill). Empty
        (zero rows, same columns) if the blotter carries no option fills at all.
    """
    # ============================================================================
    # HOW OPEN AND CLOSE ARE TOLD APART — and why `side` cannot do it ALONE.
    #
    # A long put is BOUGHT to open and SOLD to close; a collar's short call is
    # SOLD to open and BOUGHT to close. So the sign of the fill says nothing about
    # which end of the lifecycle it is — the two structures would classify in
    # opposite directions off the same rule.
    #
    # What does determine it is whether the fill moves the position AWAY from flat
    # or TOWARDS it. That needs the running position per symbol, which the blotter
    # gives us as a cumulative sum in bar order. Hence one pass, in time order,
    # carrying a running signed quantity per leg. `side` IS kept in the per-fill
    # result, though — once "opening" is known, side tells long from short: an
    # OPENING fill with side=+1 is a long leg (the only way a position moves
    # further from flat on a buy), an OPENING fill with side=-1 is a short leg.
    # ============================================================================
    empty = pd.DataFrame(columns=["ts", "symbol", "side", "opening", "notional"])
    if blotter is None or len(blotter) == 0:
        return empty

    # Option fills only, in strict bar order. `kind="stable"` so two fills on the same bar keep
    # the order the simulator executed them in (it sorts symbols for determinism), which matters
    # because the running position below is order-sensitive within a bar.
    opt = blotter[blotter["symbol"].apply(is_option_symbol, underlying=underlying)]
    if len(opt) == 0:
        return empty
    opt = opt.sort_values("ts", kind="stable")

    running = {}
    records = []
    for row in opt.itertuples(index=False):
        before = running.get(row.symbol, 0.0)
        # `side` is +1 buy / -1 sell and `qty` is the absolute magnitude, so the signed delta is
        # their product — the same convention Book.apply_fill uses.
        after = before + row.side * row.qty
        running[row.symbol] = after
        # Strictly further from flat is an OPEN; strictly closer is a CLOSE. A fill that flips
        # the sign outright (never produced by these rules, which always flatten before
        # re-striking) counts as a close, because the leg it belonged to is gone.
        opening = abs(after) > abs(before)
        records.append({"ts": row.ts, "symbol": row.symbol, "side": row.side,
                        "opening": opening, "notional": abs(row.notional)})
    return pd.DataFrame(records)


def classify_option_events(blotter, underlying):
    """Per-bar option lifecycle events, derived from the trade blotter alone.

    Parameters
    ----------
    blotter : DataFrame
        ``PortfolioSimulator.blotter()`` — one row per fill, with ``ts``, ``symbol``, ``side``,
        ``qty``, ``price`` and ``notional``.
    underlying : str
        The ticker the legs are written on. Fills in the underlying itself are ignored: the
        buy-and-hold leg trades on bar zero and never again, and it is not part of any hedge
        lifecycle.

    Returns
    -------
    DataFrame
        One row per BAR on which an option fill happened, with ``ts``, ``event`` (OPEN / ROLL /
        MONETISE), ``n_open``, ``n_close``, ``closed_value`` and ``opened_premium``.
    """
    # ============================================================================
    # HOW OPEN AND CLOSE ARE TOLD APART — and why `side` cannot do it.
    #
    # A long put is BOUGHT to open and SOLD to close; a collar's short call is
    # SOLD to open and BOUGHT to close. So the sign of the fill says nothing about
    # which end of the lifecycle it is — the two structures would classify in
    # opposite directions off the same rule.
    #
    # What does determine it is whether the fill moves the position AWAY from flat
    # or TOWARDS it. That needs the running position per symbol, which the blotter
    # gives us as a cumulative sum in bar order. Hence one pass, in time order,
    # carrying a running signed quantity per leg.
    # ============================================================================
    if blotter is None or len(blotter) == 0:
        return pd.DataFrame(columns=["ts", "event", "n_open", "n_close",
                                     "closed_value", "opened_premium"])

    fills = classify_option_fills(blotter, underlying)
    if len(fills) == 0:
        return pd.DataFrame(columns=["ts", "event", "n_open", "n_close",
                                     "closed_value", "opened_premium"])

    # ============================================================================
    # BAR-LEVEL CLASSIFICATION. A bar carrying both ends of the lifecycle is a ROLL
    # — the expiring legs settle and the next period's legs are struck on the same
    # bar, which is exactly what RollCalendar produces every `reset_bars`.
    #
    # KNOWN AMBIGUITY, stated rather than hidden: a monetisation followed by an
    # immediate re-open on the SAME bar is indistinguishable from a roll here,
    # because the blotter records the same two things. In practice the re-entry
    # gate and `max_flat_bars` mean a reopen is at least one bar after the
    # monetise, so the two do not collide — but if a configuration is ever run
    # with the gate fully open, this classifier will read those bars as rolls.
    # The rule's own `events` log distinguishes them; the blotter cannot.
    # ============================================================================
    out = []
    for ts, grp in fills.groupby("ts", sort=True):
        n_open = int(grp["opening"].sum())
        n_close = int((~grp["opening"]).sum())
        if n_open and n_close:
            event = ROLL
        elif n_close:
            event = MONETISE
        else:
            event = OPEN
        out.append({
            "ts": ts,
            "event": event,
            "n_open": n_open,
            "n_close": n_close,
            # Cash magnitudes, so a tooltip can say what the bar was worth without re-deriving
            # it from the fills. Absolute values: direction is already carried by the event.
            "closed_value": float(grp.loc[~grp["opening"], "notional"].sum()),
            "opened_premium": float(grp.loc[grp["opening"], "notional"].sum()),
        })
    return pd.DataFrame(out)


# ============================================================================
# THE METRIC (13-02 Task 1, AC-3). Pure: every function below takes a series and
# returns numbers. No simulator, no IO — which is what lets the tests build a
# V-shape by hand and assert the peak-reset convention directly.
#
# FLOORED CALMAR IS THE SINGLE TARGET VARIABLE. It is the only quantity that
# ranks the grid, that the response surface is fitted to, and that any optimum is
# taken over. This OVERRULES CONTEXT.md section 6, which named the strategy-over-
# SPY drawdown ratio primary and Calmar merely "supporting" — see the override
# table at the top of 13-02-PLAN.md. The reason is structural rather than a
# preference: a response surface has ONE height, so two ranking keys that
# disagree do not produce two answers, they produce an unstated judgement call
# that no output records.
# ============================================================================

def _equity_series(obj):
    """Accept either a bare equity Series or a persisted per-bar frame, and return the Series.

    The sweep persists a FRAME (``build_series_frame``: equity, ret, spot, spot_ret and the
    rule's read-outs), while the tests and any hand-built case are naturally a Series. Taking
    both here means no caller has to remember which it is holding, and — more to the point — a
    frame is never silently scored on the wrong column.
    """
    if isinstance(obj, pd.DataFrame):
        if "equity" not in obj.columns:
            raise KeyError(f"frame has no 'equity' column; got {list(obj.columns)}")
        return obj["equity"].astype(float)
    return pd.Series(obj).astype(float)


def max_drawdown(equity):
    """Deepest peak-to-trough fall, as a positive fraction, with the running peak starting at
    the series' OWN first bar.

    WHY THE PEAK RESETS AT THE SLICE START. The alternative — carrying the peak in from before
    the window — makes a window that opens mid-decline read as calm, because its "peak" sits
    above anything inside it and the fall is attributed to bars the window does not contain.
    Comparability across windows is the entire point of the metric, so every window is scored
    as if the overlay's drawdown clock started on its first bar. The consequence is accepted
    and stated beside every result (AC-3): a sliced window scores the overlay as if it had
    already been running, not starting flat.

    This is the same formula ``option_monetisation_batch.max_drawdown`` uses, kept
    character-for-character identical on purpose — 13-01's comparison table and 13-02's grid
    disagreeing about what a drawdown is would be invisible until a figure contradicted a table.
    """
    eq = _equity_series(equity)
    if eq.empty:
        return float("nan")
    # Running maximum to date. `cummax` from the first bar is what implements the reset above.
    peak = eq.cummax()
    # Fractional fall below the running peak, bar by bar; the deepest is the max drawdown.
    # `replace(0, nan)` guards a zero peak, which would otherwise divide by zero rather than
    # raise — an equity path through zero is a bug upstream, and NaN makes it visible here.
    return float(((peak - eq) / peak.replace(0, np.nan)).max())


def window_return(equity):
    """Total return across the series, last over first minus one. Not annualised: the windows
    differ in length by a factor of thirty, and annualising a two-month COVID slice would turn a
    −30% fall into a number no reader can hold next to a two-year calm window."""
    eq = _equity_series(equity)
    if len(eq) < 2 or eq.iloc[0] == 0:
        return float("nan")
    return float(eq.iloc[-1] / eq.iloc[0] - 1.0)


def calmar(equity, drawdown_floor=DRAWDOWN_FLOOR):
    """Floored Calmar: window return over ``max(window max drawdown, drawdown_floor)``.

    Returns ``(score, floor_bound)``. ``floor_bound`` is True when the floor — not the strategy's
    own drawdown — set the denominator, which means the number is NOT a Calmar and must not be
    read as one. It is returned rather than logged because a caller that never sees it cannot
    report the share of floor-bound rows that AC-3 requires.

    ==========================================================================
    MEASURED LIMITATION — THE RANKING INVERTS WHEN THE NUMERATOR IS NEGATIVE.
    Found 2026-10-17 on the first real ladder run (build-order step 1, the GFC
    window). With a NEGATIVE window return, dividing by a larger drawdown moves
    the ratio TOWARDS zero, so a deeper drawdown scores better. Measured, blind
    roll over 2007-10 to 2009-03:
        0.80x  ret -0.540  dd 0.595  ->  -0.908   (ranked BEST)
        0.95x  ret -0.424  dd 0.445  ->  -0.953   (ranked WORST)
    The 0.95x configuration lost less AND fell less, and the metric ranked it
    last. So on a losing window this score is non-monotone in both of its own
    inputs and the ordering it produces is not meaningful.
    This is NOT fixed here. AC-3 fixes floored Calmar as 13-02's single target
    variable, and quietly substituting a different formula would discard a
    recorded decision — the same reason 12-01's ``cap = 1.28`` became a swept
    parameter rather than a silent edit. It is raised at 13-02's human-verify
    checkpoint instead. Any caller ranking on a window whose return is negative
    must state this, which is why ``score_grid`` carries ``spy_ret`` on every
    row: the sign of the numerator is visible without recomputing it.

    RESOLVED 2026-10-18 by 13-02.1, at the checkpoint, by REPLACING the target
    variable rather than editing this function: ``relative_score`` below is
    now the ``score`` column, and this floored Calmar survives unchanged as the
    descriptive ``calmar_floored`` column so 13-02's published numbers can
    still be reproduced and compared.
    ==========================================================================
    """
    eq = _equity_series(equity)
    dd = max_drawdown(eq)
    ret = window_return(eq)
    if not np.isfinite(dd) or not np.isfinite(ret):
        return float("nan"), False
    # The floor binds whenever the realised drawdown is shallower than it. Recorded as a flag on
    # the row, so a cell that scored well because it barely moved is distinguishable from one
    # that scored well because it earned.
    floor_bound = bool(dd < drawdown_floor)
    return float(ret / max(dd, drawdown_floor)), floor_bound


def wealth_over_drawdown(equity, drawdown_floor=DRAWDOWN_FLOOR):
    """``(1 + R) / max(MDD, floor)`` — growth of a unit of wealth per unit of drawdown suffered.

    The intermediate step between floored Calmar and ``relative_score``, and the one that removes
    the inversion: ``1 + R`` is a wealth multiple and is POSITIVE for any loss short of total
    ruin, so a larger drawdown can only ever lower the ratio. It is still not comparable ACROSS
    windows (a benign window scores high whatever the hedge did), which is what dividing through
    by the underlying's own ratio then fixes.
    """
    eq = _equity_series(equity)
    dd = max_drawdown(eq)
    ret = window_return(eq)
    if not np.isfinite(dd) or not np.isfinite(ret):
        return float("nan")
    return float((1.0 + ret) / max(dd, drawdown_floor))


def relative_score(equity, spy, drawdown_floor=DRAWDOWN_FLOOR):
    """THE TARGET VARIABLE (13-02.1, decision D1 option (c), 2026-10-18).

        score = [ (1 + R) / (1 + R_spy) ]  /  [ max(MDD, f) / max(MDD_spy, f) ]

    Relative wealth over relative drawdown, both measured against the underlying on the SAME
    window slice. 1.0 is "no better than holding SPY"; above 1.0 the configuration ended richer,
    fell less, or both.

    WHY THIS FORM. Floored Calmar inverted on losing windows (see ``calmar``): a negative return
    over a larger drawdown moves towards zero, so 0.80x losing 54% with a 59.5% drawdown outranked
    0.95x losing 42% with a 44.5% drawdown. Here both factors are POSITIVE, so the score is
    increasing in the configuration's return and decreasing in its drawdown on every window,
    winning or losing. Dividing through by SPY's own pair puts every window on one scale centred on
    1.0, so a crisis and a calm stretch can be read, coloured and pooled together.

    Option (a), the pure drawdown ratio ``MDD / MDD_spy``, was rejected as the TARGET because it
    ignores return entirely and would let a calm window pick whatever shaved the most drawdown
    regardless of the premium burned. It is kept as the descriptive ``dd_vs_spy`` column.

    The floor ``f`` is applied to BOTH drawdowns, so a window where SPY barely dipped cannot make
    the denominator explode.
    """
    eq, ref = _equity_series(equity), _equity_series(spy)
    dd, dd_spy = max_drawdown(eq), max_drawdown(ref)
    ret, ret_spy = window_return(eq), window_return(ref)
    if not all(np.isfinite(v) for v in (dd, dd_spy, ret, ret_spy)) or ret_spy <= -1.0:
        return float("nan")
    wealth = (1.0 + ret) / (1.0 + ret_spy)
    drawdown = max(dd, drawdown_floor) / max(dd_spy, drawdown_floor)
    return float(wealth / drawdown)


def premium_in_window(blotter, underlying, start, end, spot):
    """Premium PAID on opening option fills inside the window, as a fraction of average spot.

    Derived through ``classify_option_fills`` — the one shared opening/closing test — rather
    than from the fill's ``side``, because a long put opens on a BUY and a collar's short call
    opens on a SELL, so side classifies the two structures in opposite directions (see that
    function's header). Returns NaN when there is no blotter, which is the honest answer for the
    unhedged reference row rather than a zero that would read as "free".

    CAREFUL: this is premium paid INSIDE the window, not premium attributable to it. A window
    shorter than the 63-bar roll cadence can contain no opening fill at all and will report 0.0
    while carrying a position bought before the window opened — measured on the COVID window
    (2020-02 to 2020-03, ~40 bars), where every ladder configuration reports exactly 0.0. That
    zero means "nothing was bought in these bars", NOT "this hedge was free". Apportioning
    premium across a leg's life would be a different quantity and is not what AC-3's descriptive
    column asks for, so the ambiguity is documented rather than papered over.
    """
    if blotter is None or len(blotter) == 0:
        return float("nan")
    fills = classify_option_fills(blotter, underlying)
    if len(fills) == 0:
        return float("nan")
    seg = fills[(fills["ts"] >= pd.Timestamp(start)) & (fills["ts"] <= pd.Timestamp(end))]
    opened = seg.loc[seg["opening"], "notional"].sum()
    mean_spot = float(_equity_series(spot).mean())
    if mean_spot == 0:
        return float("nan")
    # Premium as a fraction of spot, so it is comparable across windows whose index level
    # differs by a factor of five between 2008 and 2024.
    return float(opened / mean_spot)


def score_grid(series_by_config, spy, windows=None, drawdown_floor=DRAWDOWN_FLOOR,
               blotter_by_config=None, underlying="SPY"):
    """Tidy one-row-per-(config, window) score frame.

    Parameters
    ----------
    series_by_config : dict
        config label -> persisted per-bar frame (or bare equity Series). Scored by SLICING:
        the simulator is never re-run per window, which is the property that makes the grid and
        13-03's CPCV affordable at all.
    spy : Series
        The underlying's own closes, for the reference row AC-3 requires beside every result.
    windows : dict or None
        name -> (start, end, kind). Defaults to ``INDICATIVE_WINDOWS``.
    blotter_by_config : dict or None
        config label -> ``sim.blotter()``, used only for the descriptive premium column. Absent
        means that column is NaN; the score itself does not depend on it.

    Returns
    -------
    DataFrame
        Columns: ``config``, ``window``, ``kind``, ``score``, ``floor_bound``, ``ret``,
        ``max_dd``, ``n_bars``, the SPY reference triple (``spy_score``, ``spy_ret``,
        ``spy_max_dd``), and the two DESCRIPTIVE columns below.
    """
    windows = INDICATIVE_WINDOWS if windows is None else windows
    blotter_by_config = blotter_by_config or {}
    spy_eq = _equity_series(spy)

    rows = []
    for window, (start, end, kind) in windows.items():
        spy_seg = spy_eq.loc[start:end]
        # The reference row, computed on the SAME bounds as every strategy row. Computed once per
        # window rather than once per config: it does not depend on the configuration, and
        # recomputing it per row invites a slice that silently differs.
        spy_calmar, spy_floor = calmar(spy_seg, drawdown_floor)
        spy_dd = max_drawdown(spy_seg)
        # SPY against itself under relative_score is 1.0 by construction — computed rather than
        # typed, so a slicing bug that made the reference disagree with itself would show.
        spy_score = relative_score(spy_seg, spy_seg, drawdown_floor)
        spy_wod = wealth_over_drawdown(spy_seg, drawdown_floor)

        for config, series in series_by_config.items():
            seg = _equity_series(series).loc[start:end]
            if len(seg) < 2:
                # A window outside the configuration's own coverage. Skipped loudly in the frame
                # as a NaN row rather than dropped, so a missing window cannot be mistaken for a
                # window that scored badly.
                rows.append({"config": config, "window": window, "kind": kind,
                             "score": float("nan"), "floor_bound": False,
                             "ret": float("nan"), "max_dd": float("nan"), "n_bars": len(seg)})
                continue
            # Floored Calmar is still computed (descriptive since 13-02.1); the score is
            # relative_score against SPY on the identical slice.
            calmar_floored, floor_bound = calmar(seg, drawdown_floor)
            score = relative_score(seg, spy_seg, drawdown_floor)
            strat_dd = max_drawdown(seg)
            premium = premium_in_window(blotter_by_config.get(config), underlying,
                                        start, end, spy_seg)
            rows.append({
                "config": config,
                "window": window,
                "kind": kind,
                "n_bars": len(seg),
                # THE TARGET VARIABLE. Everything that sorts, fits or optimises reads this one.
                # Since 13-02.1 it is relative_score (relative wealth over relative drawdown).
                "score": score,
                "floor_bound": floor_bound,
                "ret": window_return(seg),
                "max_dd": strat_dd,
                # The build-up the page walks through before the score: MDD alone, R alone,
                # then (1+R)/MDD — each with SPY's own value beside it.
                "wealth_over_dd": wealth_over_drawdown(seg, drawdown_floor),
                # 13-02's original target, kept descriptive so its numbers remain reproducible.
                "calmar_floored": calmar_floored,
                "spy_score": spy_score,
                "spy_floor_bound": spy_floor,
                "spy_ret": window_return(spy_seg),
                "spy_max_dd": spy_dd,
                "spy_wealth_over_dd": spy_wod,
                "spy_calmar_floored": spy_calmar,
                # ------------------------------------------------------------------
                # DESCRIPTIVE COLUMNS — SHOWN BESIDE EVERY RESULT, RANKING NOTHING.
                #
                # WHY THE DEMOTION IS COMMENTED HERE AND NOT ONLY IN THE PLAN.
                # CONTEXT.md section 6 called `dd_vs_spy` the PRIMARY metric and
                # floored Calmar merely supporting. 13-02 reverses that, and this
                # is exactly the kind of decision the next reader silently reverses
                # back if they happen to find the context file first — so the reason
                # lives at the definition: a response surface is fitted to ONE
                # height, and a second ranking key that disagrees with the first
                # turns selection into a judgement call that no output records.
                #
                # They are COMPUTED, not dropped, because "it kept 60% of the loss"
                # and "it bought 4% of drawdown reduction per 1% of premium" are the
                # interpretable sentences a Calmar cannot say. They are read; they
                # do not rank.
                # ------------------------------------------------------------------
                "dd_vs_spy": (strat_dd / spy_dd) if (np.isfinite(spy_dd) and spy_dd > 0)
                             else float("nan"),
                # The target variable expressed RELATIVE to the index on the same window —
                # "how much of SPY's own floored Calmar did this configuration deliver".
                # Descriptive like the rest of this block: it ranks identically to `score`
                # inside a window (the divisor is constant there), so it adds no second
                # ordering, only a magnitude a reader can hold across windows whose levels
                # differ by a factor of six.
                #
                # READ THE SIGN BEFORE THE NUMBER. When `spy_score` is NEGATIVE — which it is
                # on every drawdown window — a ratio ABOVE 1.0 means the configuration did
                # WORSE than the index, not better, because both sides are losses. The ratio
                # is only "higher is better" on a window where the index itself made money.
                #
                # SINCE 13-02.1 the score is relative_score, for which spy_score is exactly 1.0
                # and every value is positive, so this column equals `score` and the sign trap
                # above no longer arises. Kept so downstream readers of the column do not break;
                # the warning is retained because it still describes `calmar_floored / spy`.
                "score_vs_spy": (score / spy_score) if (np.isfinite(spy_score)
                                                        and spy_score != 0) else float("nan"),
                "premium_pct_spot": premium,
                "dd_reduction_per_premium": ((spy_dd - strat_dd) / premium)
                                            if (np.isfinite(premium) and premium > 0)
                                            else float("nan"),
            })
    return pd.DataFrame(rows)


def spy_reference_rows(scores, label="SPY (index)"):
    """The index's own score, reshaped as ordinary configuration ROWS so it can sit inside a
    grid rather than beside it.

    ``score_grid`` already carries ``spy_score`` on every row, but as a COLUMN it is invisible in
    a heatmap — and the first question anyone asks of a hedge grid is "which of these actually
    beat just holding the thing". Concatenating this makes the benchmark a row the eye compares
    against, in the same colours and on the same scale as every configuration above it.

    Scored once per window, not per configuration, because it does not depend on the
    configuration — and recomputing it per row would invite a slice that silently differed.
    """
    cols = ["window", "kind", "spy_score", "spy_floor_bound", "spy_ret", "spy_max_dd",
            "spy_wealth_over_dd", "spy_calmar_floored"]
    have = [c for c in cols if c in scores.columns]
    one = scores[have].drop_duplicates(subset=["window"]).copy()
    one = one.rename(columns={"spy_score": "score", "spy_floor_bound": "floor_bound",
                              "spy_ret": "ret", "spy_max_dd": "max_dd",
                              "spy_wealth_over_dd": "wealth_over_dd",
                              "spy_calmar_floored": "calmar_floored"})
    one["config"] = label
    # The index against itself is 1.0 by construction. Stated rather than left NaN, because a
    # blank benchmark cell in the relative grid reads as missing data when it is in fact the
    # definition of the scale.
    one["score_vs_spy"] = 1.0
    one["dd_vs_spy"] = 1.0
    return one


def rank_configs(scores):
    """Per-configuration ranks WITHIN the drawdown set and WITHIN the calm set, sorted by the
    worse of the two (AC-4).

    The aggregate inside each set is the MEAN floored Calmar across that set's windows. Stated
    rather than assumed: the windows in a set are different regimes and not replicates of one
    quantity, so the mean is a summary for ranking purposes only — the spread between them is
    signal, and it is what the per-window surfaces (Task 4 figure 2) exist to show.

    Sorted by the WORSE of the two ranks, never by their average, because an average lets a
    combination that tops the crisis set and sits at the bottom of the calm set come out near the
    middle and look like a compromise. It is not a compromise; it is a hedge that only works if
    the crisis arrives on schedule.
    """
    out = scores.dropna(subset=["score"])
    # Mean score per (config, kind), pivoted so each kind is its own column.
    agg = out.groupby(["config", "kind"])["score"].mean().unstack()
    for kind in (DRAWDOWN, CALM):
        if kind not in agg.columns:
            agg[kind] = float("nan")
    # Rank 1 = best. `ascending=False` because a HIGHER floored Calmar is better.
    agg["rank_drawdown"] = agg[DRAWDOWN].rank(ascending=False, method="min")
    agg["rank_calm"] = agg[CALM].rank(ascending=False, method="min")
    # The headline key. `max` of the two ranks IS "the worse of the two", since rank 1 is best.
    agg["worst_rank"] = agg[["rank_drawdown", "rank_calm"]].max(axis=1)
    # Share of this configuration's rows whose denominator was set by the floor, carried onto the
    # headline table so a top row that is mostly floor-bound cannot be read as a result.
    agg["floor_bound_share"] = out.groupby("config")["floor_bound"].mean()
    return agg.sort_values(["worst_rank", DRAWDOWN], ascending=[True, False])
