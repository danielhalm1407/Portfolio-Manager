"""
POLICY-ANCHORED BACKCAST — reconstructing the book further back than TWS will tell us.

The problem: TWS serves only ~7 days of executions, so a year of realised/unrealised
history cannot be read from the broker. The naive fallback — "assume today's position was
always held" — is a counterfactual dressed as history, and realised P&L before the window
is simply absent from a positions snapshot (an average cost describes only the units that
SURVIVED; it carries no record of what was closed).

The approach here: a policy assumption is not a guess when it is **anchored on real data at
one end** and **checkable against real data at the other**.

    step 1  ANCHOR    roll the real fills backwards off today's positions -> q(T-7d) exactly
    step 2  HORIZON   choose how far back to go (default 3 months, per-symbol overridable)
    step 3  BACKWARD  walk positions back under a NAMED policy
    step 4  FORWARD   replay the implied trades through a real Book -> realised/unrealised
    step 5  FALSIFY   check the replay lands on IBKR's actual position and average cost

Step 5 is what separates this from a plausible-looking fiction. If the assumed policy is
wrong, the forward replay will not reproduce the account we can actually observe, and the
result says so instead of quietly reporting a fabricated path.

THE TWO POLICIES
    buy_and_hold  — units constant going back. NOT an approximation: if no trades happened,
                    this is exactly what the book was. The only question is whether the
                    no-trade premise holds, and step 5 answers it.
    constant_mix  — weights sat at target on every bar, so equity steps backwards by the
                    exact recursion  E(t-1) = E(t) / (1 + sum_i w_i * r_i(t))  and units
                    follow as  u_i(t) = w_i * E(t) / p_i(t). Deterministic; no iteration,
                    no fitting, no optimisation.

KNOWN LIMITS, stated rather than buried:
  * A symbol with no price history over the horizon cannot be backcast and is skipped.
  * The avgCost check is a CONSISTENCY test, not a proof: offsetting trades could in
    principle land on the same average cost by coincidence. It can refute an assumption
    confidently; it can only support one.
  * **THE CHECK'S POWER DEPENDS ON PRICE DISPERSION.** Two policies produce different cost
    bases only to the extent that prices moved over the horizon. On a quiet, range-bound
    window, buy-and-hold and constant-mix land on average costs within ~1% of each other —
    inside the tolerance IB's commission-inclusive figure forces us to allow — and the check
    then CANNOT tell them apart. Measured on synthetic paths: ~1.2% separation at 0.8% daily
    vol versus ~5-8% at 2%. This is why ``separation()`` exists and why ``run()`` reports
    ``indeterminate`` instead of ``consistent`` when the candidate policies are
    indistinguishable on the data at hand. A "pass" that could not have failed is not
    evidence, and is not reported as though it were.
  * Dividends, splits, financing and FX are not modelled, so a long horizon over a
    dividend-paying holding will drift for reasons that have nothing to do with the policy.
"""

import numpy as np
import pandas as pd

from .book import Book
from .fills import Fill

BUY_AND_HOLD = "buy_and_hold"
CONSTANT_MIX = "constant_mix"


def resolve_start_dates(symbols, anchor_ts, months=3, held_since=None, index=None):
    """Per-symbol backcast start date.

    `held_since` is the "I know I have held this since roughly then" override; anything not
    named there falls back to `months` before the anchor. Dates are snapped forward onto the
    price index so the walk always starts on a real bar.
    """
    held_since = held_since or {}
    default_start = pd.Timestamp(anchor_ts) - pd.DateOffset(months=months)
    out = {}
    for sym in symbols:
        start = pd.Timestamp(held_since.get(sym, default_start))
        if index is not None and len(index):
            # Snap to the first available bar at or after the requested start. A symbol
            # whose start precedes the price history simply begins where the data begins.
            later = index[index >= start]
            start = later[0] if len(later) else index[-1]
        out[sym] = start
    return out


def _position_path_buy_and_hold(prices, anchor_qty, start_dates, anchor_ts):
    # Units are constant from each symbol's start date to the anchor. The position is zero
    # before the start date, which is what "I first held it then" means.
    idx = prices.loc[:anchor_ts].index
    path = pd.DataFrame(0.0, index=idx, columns=list(anchor_qty))
    for sym, qty in anchor_qty.items():
        if sym not in prices.columns:
            continue
        path.loc[path.index >= start_dates.get(sym, idx[0]), sym] = qty
    return path


def _position_path_constant_mix(prices, anchor_qty, weights, start_dates, anchor_ts):
    # Walk equity BACKWARDS from the anchor using the exact constant-mix recursion, then
    # read units off it. Deriving equity first is what makes this deterministic: under the
    # policy, weights are known at every bar, so the only unknown is the equity level, and
    # each step back is a single division.
    idx = prices.loc[:anchor_ts].index
    rets = prices.loc[idx].pct_change().fillna(0.0)
    syms = [s for s in anchor_qty if s in prices.columns]
    if not syms:
        return pd.DataFrame(index=idx)

    # Anchor equity is the marked value of the real position at the anchor bar.
    equity = pd.Series(index=idx, dtype=float)
    equity.iloc[-1] = sum(anchor_qty[s] * float(prices.loc[anchor_ts, s]) for s in syms)

    w = {s: float(weights.get(s, 0.0)) for s in syms}
    for i in range(len(idx) - 1, 0, -1):
        # Portfolio return on bar i under the assumed weights.
        r_p = sum(w[s] * float(rets.iloc[i][s]) for s in syms)
        # Invert E(t) = E(t-1) * (1 + r_p). A -100% bar would be a division by zero, so it
        # is guarded rather than allowed to produce an inf that silently poisons the path.
        equity.iloc[i - 1] = equity.iloc[i] / (1.0 + r_p) if (1.0 + r_p) != 0 else np.nan

    path = pd.DataFrame(0.0, index=idx, columns=syms)
    for s in syms:
        units = (equity * w[s]) / prices.loc[idx, s].astype(float)
        active = idx >= start_dates.get(s, idx[0])
        path.loc[active, s] = units[active]
    return path


def position_path(prices, anchor_qty, anchor_ts, policy=BUY_AND_HOLD, weights=None,
                  months=3, held_since=None):
    """Steps 2-3: the implied position path from each symbol's start date to the anchor."""
    prices = prices.sort_index()
    anchor_ts = pd.Timestamp(anchor_ts)
    start_dates = resolve_start_dates(anchor_qty, anchor_ts, months, held_since,
                                      index=prices.loc[:anchor_ts].index)
    if policy == BUY_AND_HOLD:
        return _position_path_buy_and_hold(prices, anchor_qty, start_dates, anchor_ts)
    if policy == CONSTANT_MIX:
        if not weights:
            raise ValueError("constant_mix backcast needs the target weights it assumes")
        return _position_path_constant_mix(prices, anchor_qty, weights, start_dates, anchor_ts)
    raise ValueError(f"unknown backcast policy {policy!r}")


def fills_from_position_path(path, prices):
    """Turn a position path into the trades that would have produced it.

    Each bar's change in units is one fill at that bar's price. This is what allows the
    backcast to produce a realised/unrealised split at all: the split is a property of
    TRADES, not of positions, so the positions have to be converted back into trades and run
    through the accounting engine.
    """
    fills = []
    order_id = 0
    for sym in path.columns:
        deltas = path[sym].diff()
        # The first bar is an opening trade of the full initial size, not a NaN.
        deltas.iloc[0] = path[sym].iloc[0]
        for ts, delta in deltas.items():
            if not np.isfinite(delta) or abs(delta) < 1e-12:
                continue
            px = float(prices.loc[ts, sym])
            if not np.isfinite(px) or px <= 0:
                continue
            order_id += 1
            fills.append(Fill(order_id=order_id, ts=ts,
                              side=1 if delta > 0 else -1, qty=abs(delta), price=px,
                              source="backcast", symbol=sym))
    # Chronological order matters: VWAP and realised P&L both depend on fill sequence.
    fills.sort(key=lambda f: (f.ts, f.order_id))
    return fills


def replay(fills, prices, base_equity=0.0):
    """Step 4: run the implied fills through a real ``Book``.

    Uses the same ``apply_fill`` as every other path in this package, so the backcast's
    realised/unrealised numbers are produced by the one accounting engine rather than by a
    parallel calculation that could disagree with it.
    """
    book = Book(base_equity=base_equity)
    by_ts = {}
    for f in fills:
        by_ts.setdefault(f.ts, []).append(f)

    rows = {}
    for ts in prices.index:
        for f in by_ts.get(ts, []):
            book.apply_fill(f)
        marks = {s: float(prices.loc[ts, s]) for s in prices.columns
                 if np.isfinite(prices.loc[ts, s])}
        snap = book.snapshot(marks)
        row = {}
        for sym, fields in snap.items():
            for k, v in fields.items():
                row[f"{sym}_{k}"] = v
        row["equity"] = book.equity(marks)
        rows[ts] = row
    return book, pd.DataFrame.from_dict(rows, orient="index")


def check(book, portfolio_df, qty_tol=1e-4, avg_rel_tol=0.02):
    """Step 5: FALSIFY the assumption.

    Compares the backcast's end state against what IBKR actually reports today. A pass means
    the assumed policy is CONSISTENT with the real account; a fail means it is wrong and the
    reconstruction should not be believed.

    Deliberately asymmetric tolerances: quantity must match tightly (both sides are counting
    the same shares), while average entry gets a relative band because IB folds commissions
    into its average cost and we do not.

    Returns (ok, DataFrame). Never raises on a mismatch — the caller is expected to REPORT
    the failure, which is the whole point of running the check.
    """
    from .ibkr_sync import reconcile   # local import keeps this module free of a hard dep

    rec = reconcile(book, portfolio_df, tol=qty_tol)
    rec["qty_ok"] = rec["position_diff"].abs() < qty_tol
    rec["avg_ok"] = np.where(
        rec["ib_avg_entry"].abs() > 0,
        (rec["avg_entry_diff"].abs() / rec["ib_avg_entry"].abs().replace(0, np.nan))
        <= avg_rel_tol,
        rec["our_avg_entry"].abs() < qty_tol,
    )
    # Surface the SIZE of the cost-basis gap, not just the pass/fail. A symbol sitting at
    # 1.9% against a 2% tolerance passed by a hair and deserves a second look; a binary
    # column would hide that entirely.
    rec["avg_entry_rel_diff"] = np.where(
        rec["ib_avg_entry"].abs() > 0,
        rec["avg_entry_diff"].abs() / rec["ib_avg_entry"].abs().replace(0, np.nan),
        np.nan,
    )
    rec["verdict"] = np.where(rec["qty_ok"] & rec["avg_ok"], "consistent", "INCONSISTENT")
    return bool(rec["qty_ok"].all() and rec["avg_ok"].all()), rec


def separation(prices, portfolio_df, weights, exec_df=None, months=3, held_since=None,
               base_equity=0.0, multipliers=None, policies=(BUY_AND_HOLD, CONSTANT_MIX)):
    """Can this data tell the candidate policies apart at all?

    Runs the backcast under each policy and compares the average entry costs they PREDICT
    against each other. If two policies imply cost bases within the check's tolerance, then
    a "consistent" verdict for either one is meaningless — the data simply cannot
    discriminate, and saying "consistent" would overstate what was learned.

    This is the honest companion to ``check``: that one asks "does the assumption fit?",
    this one asks "could it have failed?". Both answers are needed before believing a
    reconstruction.

    Returns a DataFrame of predicted avg_entry per symbol per policy, plus the pairwise
    relative separation.
    """
    preds = {}
    for policy in policies:
        out = run(prices, portfolio_df, exec_df=exec_df, policy=policy, weights=weights,
                  months=months, held_since=held_since, base_equity=base_equity,
                  multipliers=multipliers, _assess=False)
        preds[policy] = {s: out["book"].position(s).avg_entry for s in out["anchor_qty"]}

    df = pd.DataFrame(preds)
    if df.shape[1] >= 2:
        lo, hi = df.min(axis=1), df.max(axis=1)
        # Relative spread between the most- and least-extreme predictions for each symbol.
        df["separation"] = (hi - lo).abs() / hi.abs().replace(0, np.nan)
    return df


def run(prices, portfolio_df, exec_df=None, policy=BUY_AND_HOLD, weights=None,
        months=3, held_since=None, base_equity=0.0, multipliers=None,
        avg_rel_tol=0.02, _assess=True):
    """The whole five-step backcast, end to end.

    Returns a dict with the anchor quantities, the implied position path, the per-bar state
    frame, the resulting book, and the falsification verdict. `provenance` marks every bar
    so a caller can shade the modelled segment and never present it as measured history.

    ``verdict`` is one of:
      ``consistent``    — the assumption fits, AND the data could have rejected it
      ``INCONSISTENT``  — the assumption does not fit; do not believe the reconstruction
      ``indeterminate`` — it fits, but the candidate policies are indistinguishable on this
                          data, so the fit is not evidence for anything (see ``separation``)

    ``_assess`` is internal: ``separation`` calls ``run`` for each policy and must not
    recurse back into the assessment.
    """
    from .ibkr_sync import rewind_positions

    prices = prices.sort_index()
    anchor_ts = prices.index[-1]
    # Step 1: the anchor is real — today's positions with the known fills undone.
    anchor_qty = rewind_positions(portfolio_df, exec_df, multipliers=multipliers)
    anchor_qty = {s: q for s, q in anchor_qty.items() if q != 0 and s in prices.columns}

    # Steps 2-3.
    path = position_path(prices, anchor_qty, anchor_ts, policy=policy, weights=weights,
                         months=months, held_since=held_since)
    # Step 4.
    fills = fills_from_position_path(path, prices)
    book, state = replay(fills, prices.loc[path.index], base_equity=base_equity)
    # Step 5.
    ok, rec = check(book, portfolio_df, avg_rel_tol=avg_rel_tol)

    # Step 5b: could the check have failed at all? A pass on data that cannot distinguish
    # the candidate policies is not evidence, and must not be reported as though it were.
    verdict = "consistent" if ok else "INCONSISTENT"
    sep = None
    if _assess and ok and weights:
        sep = separation(prices, portfolio_df, weights, exec_df=exec_df, months=months,
                         held_since=held_since, base_equity=base_equity,
                         multipliers=multipliers)
        if "separation" in sep.columns:
            worst = sep["separation"].max()
            # If the policies' predicted cost bases sit inside the same tolerance the check
            # uses, no assumption could have been rejected — say so rather than "consistent".
            if not np.isfinite(worst) or worst <= avg_rel_tol:
                verdict = "indeterminate"

    # Everything the backcast produced is modelled; the caller overlays the real-fill window
    # on top. Labelling it here means no downstream consumer has to remember to.
    state["provenance"] = f"backcast ({policy})"

    return {
        "anchor_ts": anchor_ts,
        "anchor_qty": anchor_qty,
        "position_path": path,
        "fills": fills,
        "book": book,
        "state": state,
        "consistent": ok,
        "verdict": verdict,
        "check": rec,
        "separation": sep,
        "policy": policy,
    }
