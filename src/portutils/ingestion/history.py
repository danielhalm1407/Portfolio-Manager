"""
portutils.ingestion.history — long-history price (and volatility) fetching with
per-row provenance.

Library code: pure and import-safe, no side effects on import (src/CLAUDE.md). The
paginated IBKR fetch and the yfinance fallback both live here; the side-effecting half
(writing parquets to data/processed/) belongs in src/pipelines/cache_prices.py, which
is the only caller.

SOURCE SPLIT — decided 2026-09-20, Phase 11 Task 1 checkpoint (STATE.md Decisions):
IBKR is PRIMARY. A live-TWS probe (port 7497) found NO per-request duration ceiling
for daily bars: SPY and QQQ TRADES each returned their FULL listing history
(1993-02-01 and 1999-03-11 respectively) in a single request, up to 60 years of
requested duration, with zero pacing errors across five rapid sequential requests.
yfinance matched IBKR's depth to within a day. yfinance is kept here as the
documented FALLBACK for AC-2 (a span IBKR does not reach) — for the two ETF legs
this milestone actually needs it is never invoked, but the mechanism must exist for
symbols the probe did not test.

The same probe found IBKR's OPTION_IMPLIED_VOLATILITY whatToShow gives a genuine
(non-synthetic) implied-vol history for SPY/QQQ back to 2006-01-09, and
HISTORICAL_VOLATILITY (realised) back to 2005-02-16 — see AC-5 / the 11-01 SUMMARY
for the full evidence. Both are fetched through the same paginated path below by
passing what_to_show explicitly.
"""
from __future__ import annotations

import pandas as pd

# IBKR's daily-bar ceiling, if one exists at all, was NOT found by the Task 1 probe up
# to 60 years of requested duration — SPY and QQQ each returned their full listing
# history in ONE request. This window exists for pagination SAFETY: a symbol the probe
# never tested (a different bar size, a thinner name) might still hit a real ceiling,
# and the fetcher below pages backwards from whatever it received rather than trusting
# a single request to be complete.
PAGE_WINDOW = "25 Y"

# Two adjacent pages' overlapping bars must agree to within this tolerance or the stitch
# is refused outright (AC-1) — a silent disagreement here would corrupt every downstream
# result and be nearly invisible once concatenated.
STITCH_TOLERANCE = 1e-6


def fetch_ibkr_long_history(symbol, app, bar_size="1 day", what_to_show="TRADES",
                             end_date="", max_pages=4):
    """Fetch as much history for `symbol` as IBKR will serve, paginating backwards in
    PAGE_WINDOW-sized requests when a single request comes back exactly window-sized
    (a signal there may be more before it) and stitching on the overlap.

    Returns a DataFrame indexed by date ('ts'), with `symbol` (close price) and
    `f'{symbol}_source'` (== 'ibkr' on every row) columns. Empty DataFrame with the
    same columns if IBKR returned nothing at all for this symbol.

    Parameters
    ----------
    app : an already-connected IBApp (ingestion.ibkr_requests.connect_ib already run).
    max_pages : safety cap on backward pagination requests. The Task 1 probe found
        daily bars need exactly ONE page for SPY/QQQ (full history in one request), so
        this guards against a symbol that behaves differently, not the expected path.
    """
    from portutils.ingestion.ibkr_requests import get_equity_data

    empty = pd.DataFrame(columns=[symbol, f"{symbol}_source"])
    empty.index = pd.DatetimeIndex([], name="ts")

    frames = []  # raw per-page DataFrames, newest-requested first
    earliest_seen = end_date  # '' means "now" to reqHistoricalData
    for _ in range(max_pages):
        res = get_equity_data(
            symbols=symbol, app=app, duration=PAGE_WINDOW, bar_size=bar_size,
            end_date=earliest_seen, what_to_show=what_to_show, output_format="dict",
        )
        df = res.get(symbol)
        if df is None or df.empty:
            break
        df = df.copy()
        # get_equity_data's raw column is 'datetime' as YYYYMMDD int/str for daily bars.
        df["datetime"] = pd.to_datetime(df["datetime"].astype(str), format="%Y%m%d")
        df = df.sort_values("datetime")
        frames.append(df)

        # --- Stitch-overlap assertion (AC-1) -----------------------------------
        # Compare THIS page against the PREVIOUS one on any dates both cover. A silent
        # concatenation over a disagreement would corrupt every downstream result and
        # be nearly invisible once the pages are merged — refuse instead of guessing.
        if len(frames) >= 2:
            prior = frames[-2]
            overlap_dates = sorted(set(df["datetime"]) & set(prior["datetime"]))
            if overlap_dates:
                new_close = df.set_index("datetime")["close"].loc[overlap_dates]
                prior_close = prior.set_index("datetime")["close"].loc[overlap_dates]
                mismatch = (new_close - prior_close).abs() > STITCH_TOLERANCE
                if mismatch.any():
                    bad_dates = new_close.index[mismatch].tolist()
                    raise ValueError(
                        f"{symbol}: stitch overlap disagrees on {mismatch.sum()} bar(s) "
                        f"({bad_dates[:3]}...) between adjacent IBKR requests — refusing "
                        f"to silently concatenate"
                    )

        # A page shorter than the requested window means IBKR had no more history
        # before its own start — nothing left to page backwards for.
        if len(df) < 2:
            break

        page_start = df["datetime"].iloc[0]
        next_end = page_start.strftime("%Y%m%d-%H:%M:%S")  # UTC dash form, no suffix
        # No forward progress (IBKR handed back the same starting bar again) — stop
        # rather than spin for max_pages iterations for nothing.
        if next_end == earliest_seen:
            break
        earliest_seen = next_end

    if not frames:
        return empty

    combined = pd.concat(frames, ignore_index=True)
    # Duplicates arise ONLY at verified-matching stitch seams (checked above), so
    # dropping them here is safe — the alternative, keeping both, would double-count
    # a bar in any downstream return calculation.
    combined = combined.drop_duplicates(subset="datetime").sort_values("datetime")
    out = combined.set_index("datetime")[["close"]].rename(columns={"close": symbol})
    out.index.name = "ts"
    out[f"{symbol}_source"] = "ibkr"
    return out


def fetch_yfinance_fallback(symbol, start=None, end=None):
    """The free-source fallback for AC-2: yfinance, already a declared but previously
    unused dependency. Used only for the span IBKR's primary path did not reach —
    every row is tagged 'yfinance' so a merged series never mixes provenance silently.
    """
    import yfinance as yf

    tk = yf.Ticker(symbol)
    if start is None and end is None:
        hist = tk.history(period="max")
    else:
        hist = tk.history(start=start, end=end)

    empty = pd.DataFrame(columns=[symbol, f"{symbol}_source"])
    empty.index = pd.DatetimeIndex([], name="ts")
    if hist.empty:
        return empty

    out = hist[["Close"]].rename(columns={"Close": symbol})
    # yfinance's index carries an exchange-local timezone; IBKR's daily bars are
    # timezone-naive dates. Normalise to a naive date index so a later join lines up
    # rather than silently failing to match on tz-aware vs tz-naive timestamps.
    out.index = pd.DatetimeIndex(out.index.date, name="ts")
    out[f"{symbol}_source"] = "yfinance"
    return out


def find_coverage_gaps(series, max_gap_days=5):
    """Internal gaps in a date-indexed series longer than a normal market closure.

    A long weekend plus a single holiday is at most a handful of calendar days;
    anything longer is a genuine hole in the data, not routine non-trading time.
    Used by the coverage report (AC-3) — "every internal gap" must be NAMED, not
    just implied by a lower row count.

    Returns a list of (gap_start, gap_end, calendar_days) tuples, one per gap found
    between consecutive VALID (non-NaN) observations. Leading/trailing NaN (a series
    that simply has not started yet, or has ended) is coverage, not a gap, and is
    reported separately via first_valid_index / last_valid_index.
    """
    valid_index = series.dropna().index
    if len(valid_index) < 2:
        return []
    deltas = valid_index.to_series().diff().dt.days
    gaps = []
    for i in range(1, len(valid_index)):
        days = deltas.iloc[i]
        if days is not None and days > max_gap_days:
            gaps.append((valid_index[i - 1], valid_index[i], int(days)))
    return gaps


def fetch_long_history(symbol, app=None, host="127.0.0.1", port=7497, client_id=501,
                        bar_size="1 day", what_to_show="TRADES"):
    """Long-history fetch for one symbol: IBKR primary, yfinance fills whatever span
    IBKR's own history does not reach (the 2026-09-20 checkpoint decision). Returns a
    DataFrame indexed by date with `symbol` and `f'{symbol}_source'` columns, so every
    row's provenance is explicit regardless of which path produced it (AC-2).

    Parameters
    ----------
    app : an already-connected IBApp. If omitted, a temporary connection is opened and
        closed here (mirrors get_equity_data's own owns_app convention).
    """
    from portutils.ingestion.ibkr_requests import IBApp, connect_ib

    owns_app = app is None
    if owns_app:
        app = IBApp()
        connect_ib(app, host=host, port=port, client_id=client_id)

    try:
        ibkr_df = fetch_ibkr_long_history(symbol, app, bar_size=bar_size, what_to_show=what_to_show)
    finally:
        if owns_app:
            app.disconnect()

    if ibkr_df.empty:
        # IBKR reached nothing at all for this symbol/what_to_show combination —
        # fall back to the free source for the WHOLE span rather than returning empty.
        return fetch_yfinance_fallback(symbol)

    ibkr_start = ibkr_df.index.min()
    fallback = fetch_yfinance_fallback(symbol, end=ibkr_start - pd.Timedelta(days=1))
    # Belt-and-braces: even if the fallback's own `end` bound leaked a boundary row,
    # never let a fallback row sit on or after where the primary source's own data
    # begins — IBKR's own bar always wins on any date it actually covers.
    fallback = fallback[fallback.index < ibkr_start]
    if fallback.empty:
        return ibkr_df
    return pd.concat([fallback, ibkr_df]).sort_index()
