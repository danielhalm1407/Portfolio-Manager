"""
SCORING — window definitions, the ranking metric, and the option lifecycle classifier.

RIGHT NOW THIS MODULE HOLDS ONE FUNCTION. ``classify_option_events`` was written under 13-01.1,
which needed it for the ladder probe; the indicative windows and the floored-Calmar metric are
13-02 Task 1's work and land in this same file. The module is created early, and deliberately
nearly empty, so the classifier has a permanent home from the start rather than living in a
research script and being moved later — the same reasoning
``strategies/__init__.py`` records for ``instruments/``.

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

import pandas as pd

# The three lifecycle classes a bar can carry. Named constants rather than bare strings so a
# typo in a figure's colour map is an AttributeError rather than a silently unmatched category.
OPEN = "OPEN"
ROLL = "ROLL"
MONETISE = "MONETISE"


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

    # Option fills only, in strict bar order. `kind="stable"` so two fills on the same bar keep
    # the order the simulator executed them in (it sorts symbols for determinism), which matters
    # because the running position below is order-sensitive within a bar.
    opt = blotter[blotter["symbol"].apply(is_option_symbol, underlying=underlying)]
    if len(opt) == 0:
        return pd.DataFrame(columns=["ts", "event", "n_open", "n_close",
                                     "closed_value", "opened_premium"])
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
        records.append({"ts": row.ts, "symbol": row.symbol, "opening": opening,
                        "notional": abs(row.notional)})

    fills = pd.DataFrame(records)

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
