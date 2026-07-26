# %% 1. Import libraries
# 1. Import libraries
#
# CHECK EXISTING PORTFOLIO — break down the realised/unrealised P&L of every holding in the
# IBKR paper account, using data pulled from the broker rather than simulated fills.
#
# Companion to research/rebalance_realisation.py: same cell-script idiom, same PanelBuilder
# -> make_level_figure plotting, same explicit colour/label maps. The difference is the
# source of truth — that file simulates a policy over cached prices, this one reads the
# account that actually exists.
#
# ══════════════════════════════════════════════════════════════════════════════
# HOW FAR BACK THIS CAN HONESTLY SEE — read before believing any number below
# ══════════════════════════════════════════════════════════════════════════════
#   TODAY            positions, average cost, IB's own P&L      -> exact, from the broker
#   LAST ~7 DAYS     real executions via reqExecutions          -> exact, real fills
#   BEFORE THAT      nothing. TWS will not serve it.            -> reconstructed, see below
#
# For the period before the executions window this script runs a POLICY-ANCHORED BACKCAST
# (portutils.portfolio.backcast): anchored on the real position at the window start, walked
# backwards under a NAMED policy you choose, then replayed forward through the same
# accounting engine and CHECKED against the account IBKR actually reports today.
#
# That check has three possible verdicts and they are not interchangeable:
#   consistent     the assumption fits, and the data could have rejected it
#   INCONSISTENT   the assumption does not fit — do not believe the reconstruction
#   indeterminate  it fits, but the candidate policies are indistinguishable on this data,
#                  so the fit is not evidence of anything
#
# THE VERDICT GOVERNS LABELLING, NOT VISIBILITY.
# backcast.run() reduces the whole run to ONE verdict string — it is INCONSISTENT if any
# single symbol fails. Cells 9-11 used to hide themselves on that string, so one unpriced
# holding blanked every chart in the file and took three perfectly good reconstructions down
# with it. A blank cell teaches nothing. Instead the per-symbol verdict (which the check
# frame has always carried) is stamped onto every table and every legend entry: an
# unverified symbol is drawn, and drawn marked `(!) UNVERIFIED`. Set SHOW_SUSPECT_BACKCAST
# False in cell 2 to restore the old hide-on-failure behaviour.
#
# Everything reconstructed is labelled `backcast (...)`; everything measured is labelled
# `real`. They are never blended into one unmarked line.

import importlib
import pathlib
import sys

import numpy as np
import pandas as pd
from IPython.display import display

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import portutils
from portutils.viz import panel, dash_timeseries_app
from portutils.viz.panel import PanelBuilder
from portutils.utils import config as cfg

# %% Reload custom packages
# Reload custom packages

importlib.reload(portutils.viz.dash_timeseries_app)
from portutils.viz.dash_timeseries_app import make_level_figure

from portutils.ingestion import ibkr_requests as ib
importlib.reload(ib)

from portutils.portfolio import backcast, book_from_portfolio, fills_from_executions, reconcile, Book
import pipelines.rebalance_study as study
importlib.reload(study)

# %% 2. Settings
# 2. Settings
#
# ACCOUNT is the paper account. CLIENT_ID must differ from every other script that might be
# connected at the same time — TWS misbehaves silently when two connections share one.
ACCOUNT = "DUP102412"
CLIENT_ID = 141
EXEC_DAYS_BACK = 7          # the ceiling TWS will actually serve

# Backcast controls (see the header). `none` disables the reconstruction entirely and leaves
# the analysis to the exact-data window only.
BACKCAST_POLICY = backcast.BUY_AND_HOLD    # or backcast.CONSTANT_MIX, or None
BACKCAST_MONTHS = 3
# "I know I have held this since roughly then" — overrides the default horizon per symbol.
HELD_SINCE = {}             # e.g. {"SPY": "2025-08-01"}

# Render cells 9-11 even when the run's verdict is INCONSISTENT. Default True because the
# verdict is a whole-run summary: one symbol with no market-data subscription forces it to
# INCONSISTENT while every other holding reconstructs cleanly, and hiding the lot discards
# more information than it protects. The failures are marked on each chart instead (cell 8c).
# Set False to go back to "show nothing unless everything passes".
SHOW_SUSPECT_BACKCAST = True

# %% 3. Connect and pull the account
# 3. Connect and pull the account
#
# Three pulls: holdings (positions + average cost + IB's P&L), account totals (NetLiq, cash),
# and the recent executions. Nothing here computes anything — it is purely acquisition.

ib_app = ib.IBApp()
ib_app.start(client_id=CLIENT_ID)
ib.connection_status(ib_app)

portfolio_df = ib.get_account_updates(app=ib_app, account=ACCOUNT)
acct = ib.get_account_data(app=ib_app)
net_liq = float(acct.loc[acct["tag"] == "NetLiquidation", "value"].iloc[0])

print(f"account {ACCOUNT}: {len(portfolio_df)} holdings, NetLiquidation {net_liq:,.2f}")
display(portfolio_df)

# %% 4. Mirror the account in our accounting engine
# 4. Mirror the account in our accounting engine
#
# book_from_portfolio seeds a Book straight from IBKR's position + averageCost. Note the
# realised figure is deliberately NOT seeded from IB: theirs is session-scoped, ours is
# cumulative since the position opened, and silently equating the two would misreport a
# day's trading as a lifetime result.

book = book_from_portfolio(portfolio_df, base_equity=net_liq)
marks = {r["symbol"]: float(r["marketPrice"]) for _, r in portfolio_df.iterrows()}

# reconcile is the "does our book agree with the broker" check. If `match` is not all True,
# every P&L number further down is suspect and the right response is to stop and find out
# why — not to carry on and hope.
rec = reconcile(book, portfolio_df)
print("reconciliation vs IBKR:", "ALL MATCH" if rec["match"].all() else "*** DRIFT ***")
display(rec)

# %% 5. Roles and current weights
# 5. Roles and current weights
#
# Roles come from config/asset_universe.yaml so hedge-vs-beta reads at a glance. A holding
# the catalogue does not know shows as `unclassified` rather than breaking the run.

roles = {s: cfg.asset_role(s) for s in book.symbols}
weights_now = {s: (book.position(s).qty * marks.get(s, 0.0)) / net_liq for s in book.symbols}

summary_now = pd.DataFrame({
    "role": pd.Series(roles),
    "position": pd.Series({s: book.position(s).qty for s in book.symbols}),
    "avg_entry": pd.Series({s: book.position(s).avg_entry for s in book.symbols}),
    "mark": pd.Series(marks),
    "weight": pd.Series(weights_now),
    "unrealised": pd.Series({s: book.position(s).unrealised(marks.get(s, 0.0))
                             for s in book.symbols}),
})
display(summary_now.round(4))
print("\nby role:")
display(summary_now.groupby("role")[["weight", "unrealised"]].sum().round(4))

# %% 6. The real executions window (exact data)
# 6. The real executions window (exact data)
#
# This is genuine trade history — every fill, its price, and what it cost in commission.
# The window is printed explicitly because an EMPTY result here means "no fills in the few
# days TWS will serve", never "no trades ever".

exec_df = ib.get_executions_data(ib_app, days_back=EXEC_DAYS_BACK)
print(f"executions window: last {EXEC_DAYS_BACK} days (TWS ceiling) -> {len(exec_df)} fills")
if len(exec_df):
    display(exec_df[["ts", "symbol", "side", "shares", "price", "commission"]])

    # Replay those fills through a fresh Book to see what the week actually crystallised.
    # Same apply_fill as every simulated study — the fills just came from a broker.
    week = Book(base_equity=net_liq)
    for f in fills_from_executions(exec_df):
        week.apply_fill(f)
    week_rows = pd.DataFrame({
        s: week.position(s).snapshot(marks.get(s, 0.0)) for s in week.symbols
    }).T
    print("\nwhat the last week crystallised (real fills only):")
    display(week_rows[["position", "avg_entry_price", "realised_pnl", "unrealised_pnl"]].round(2))
    print(f"total commission paid in window: {exec_df['commission'].sum():,.2f}")
else:
    print("no fills in the window. This does NOT mean no trades were ever made.")

# %% 7. Price history for the held symbols
# 7. Price history for the held symbols
#
# Pulled once and cached to parquet so the marking below is reproducible and the next run
# does not re-hit TWS. Same pattern as src/pipelines/cache_prices.py.
#
# Two things here are not optional:
#   * app=ib_app — without it get_equity_data opens its OWN second TWS connection on its
#     default client id, which collides with whatever else is connected (see CLIENT_ID above).
#   * contract_specs — the holdings are not all US-SMART lines. Asking TWS to resolve a bare
#     ticker returns error 200 "No security definition has been found" for anything listed
#     abroad. The account snapshot already carries each holding's conId/exchange/currency, so
#     we hand those straight back rather than guessing.
#
# A holding TWS will not serve is DROPPED, not fatal: it is named in the printout below and
# excluded from the backcast, so every verdict from cell 8 onwards is about the priced
# subset only.

# The universe is whatever we actually hold — taken from the Book (which was seeded from the
# broker's own position list), never from a hand-maintained ticker list that could drift out
# of step with the account. sorted() only so the printout and the parquet column order are
# stable between runs.
symbols = sorted(book.symbols)
# Destination for the cached panel. data/processed/ is the "derived, safe to regenerate" half
# of the data dir (data/raw/ is append-only), so overwriting this file on each run is fine.
prices_path = REPO / "data" / "processed" / "prices_holdings.parquet"

# One request covering every held symbol. NOTE: this ALWAYS hits TWS — the parquet below is
# written for the *next* consumer (and for offline re-runs of cells 8-11), not consulted here.
# output_format="combined" is what makes the result a wide panel rather than a dict of OHLCV
# frames: get_equity_data keeps only each symbol's close and outer-joins them on the bar
# timestamp, so we get one row per date and one column per ticker.
raw = ib.get_equity_data(symbols=symbols, duration="1 Y", bar_size="1 day",
                         output_format="combined", app=ib_app,
                         contract_specs=ib.contract_specs_from_portfolio(portfolio_df))

# Copy before mutating: `raw` stays as returned, so a failed cleanup below can be inspected
# against the untouched original instead of being lost to an in-place edit.
prices = raw.copy()
# get_equity_data's combined path renames its 'datetime' column to 'Date'. The fallback to
# column 0 covers a panel that arrived from some other producer with a differently named
# timestamp column — positionally it is always the first column.
date_col = "Date" if "Date" in prices.columns else prices.columns[0]
# IBKR hands dates back as STRINGS ('20260702'). Parse to real datetimes, otherwise the sort
# below is lexicographic and every downstream .loc[timestamp] lookup misses.
prices[date_col] = pd.to_datetime(prices[date_col])
# One chained cleanup, and each link matters:
#   sort_values  — the outer join above does not preserve order, and every cumulative P&L
#                  calculation downstream assumes rows run forwards in time.
#   set_index    — dates become the index so the frame can be aligned by timestamp against
#                  the backcast's position path (cell 11 does exactly that).
#   astype(float)— close prices can come back as objects after the join; arithmetic on an
#                  object column silently produces objects, not numbers.
#   ffill        — carry the last close over gaps. Symbols on different exchanges keep
#                  different holiday calendars, so a London line has NaN on a US-only session
#                  and vice versa; without this the next step would delete those whole days.
#   dropna(any)  — drop rows where a symbol still has no price, i.e. the leading stretch
#                  before its listing/history begins (ffill cannot fill backwards). This is
#                  the one lossy step: the panel is TRUNCATED to the shortest history in the
#                  basket, so adding a recently-listed holding shortens the window for all.
prices = prices.sort_values(date_col).set_index(date_col).astype(float).ffill().dropna(how="any")
# Name the index 'ts' — the convention the rest of portutils (Book, backcast, PanelBuilder)
# expects for a time index.
prices.index.name = "ts"
# data/processed/ may not exist on a fresh clone; create it rather than failing on the write.
prices_path.parent.mkdir(parents=True, exist_ok=True)
# Parquet, not CSV: it preserves the datetime index and float dtypes exactly, so a reload
# needs no re-parsing and cannot re-introduce the string-date problem handled above.
prices.to_parquet(prices_path)
print(f"cached {len(prices)} bars x {len(prices.columns)} symbols -> {prices_path.name}")

# ─────────────────────────────────────────────────────────────────────────────────────────
# WHAT THIS CELL LEAVES BEHIND
#   prices       DataFrame — index `ts` (daily datetimes, ascending, common to every column),
#                one float column per held symbol, named by ticker, holding CLOSE prices only.
#                No NaNs anywhere by construction. Window = min(1 Y, shortest history held).
#   prices_path  the same frame on disk at data/processed/prices_holdings.parquet.
#   unpriced     tickers held but not priced (below) — excluded from everything downstream.
# This frame is the price input to the backcast in cell 8 and to the weight path in cell 11;
# `marks` from cell 4 remains the source for TODAY's valuation.
# ─────────────────────────────────────────────────────────────────────────────────────────

# Name the gap explicitly. backcast.run() filters its universe to prices.columns, so an
# unpriced holding disappears from the reconstruction WITHOUT raising — which is exactly the
# kind of silent omission that makes a P&L number wrong and confident at the same time.
unpriced = [s for s in symbols if s not in prices.columns]
if unpriced:
    print(f"NO PRICE HISTORY (excluded from the backcast): {', '.join(unpriced)}")

# %% 7b. Unit check — are the bars in the same currency unit as the cost basis?
# 7b. Unit check — are the bars in the same currency unit as the cost basis?
#
# ══════════════════════════════════════════════════════════════════════════════
# THE PENCE TRAP. LSE lines (BARC, HSBA, ...) are QUOTED IN PENCE (GBX) while the
# contract currency is GBP. IBKR's historical bars come back in the quoted unit —
# pence — but `averageCost` and `marketPrice` on the portfolio row come back in
# GBP. The two differ by exactly 100 and nothing in either API says so.
#
# Left alone this corrupts far more than the verdict:
#   * the backcast's entry price is 100x IB's cost basis -> INCONSISTENT
#   * cell 11's weights are qty * price, so a pence-quoted holding looks ~100x
#     bigger than it is and swamps every other line in the chart
#
# The test is unit-free and needs no ticker list: `marks` (IBKR's marketPrice)
# and the last bar of `prices` are THE SAME PRICE ON THE SAME DAY, so their ratio
# can only be a units artifact. ~1 means agreement; ~100 means pence-vs-pounds.
# ══════════════════════════════════════════════════════════════════════════════

# Ratio of our panel's latest close to the broker's own mark, per symbol.
unit_check = pd.DataFrame({
    "last_bar": prices.iloc[-1],
    "ib_market_price": pd.Series({s: marks.get(s, np.nan) for s in prices.columns}),
})
unit_check["ratio"] = unit_check["last_bar"] / unit_check["ib_market_price"]
# Snap to the nearest sane scale factor rather than dividing by the raw ratio: the two
# prices are from the same day but not necessarily the same instant, so the raw ratio
# is ~99.5, not exactly 100. Rounding to a power of 10 keeps the correction exact and
# refuses to "fix" a 3% discrepancy that is a real disagreement, not a unit mismatch.
unit_check["scale"] = 10.0 ** np.round(np.log10(unit_check["ratio"].abs()))
display(unit_check.round(4))

# Apply only where the scale is genuinely not 1 — and say which columns were touched.
rescale = {s: f for s, f in unit_check["scale"].items() if np.isfinite(f) and f != 1.0}
if rescale:
    print(f"RESCALING to the broker's unit (÷ scale): {rescale}")
    for s, f in rescale.items():
        prices[s] = prices[s] / f
    # Re-cache so the parquet on disk is in the same unit as the frame in memory —
    # otherwise the next offline run silently reintroduces the mismatch.
    prices.to_parquet(prices_path)
else:
    print("units agree with the broker for every priced holding.")

# %% 8. Policy-anchored backcast — THE FALSIFICATION CHECK COMES FIRST
# 8. Policy-anchored backcast — THE FALSIFICATION CHECK COMES FIRST
#
# The verdict is printed before any of the numbers it produced. A reconstruction whose
# assumption does not fit the account is worse than no reconstruction, so it must not be
# possible to read the pretty chart without first seeing whether it should be believed.

if BACKCAST_POLICY is None:
    print("backcast disabled — analysis limited to the exact-data window above.")
    bc = None
else:
    bc = backcast.run(
        prices, portfolio_df, exec_df=exec_df,
        policy=BACKCAST_POLICY,
        weights=cfg.portfolio_weights("spy_kmlm"),
        months=BACKCAST_MONTHS,
        held_since=HELD_SINCE,
        base_equity=net_liq,
    )

    print("=" * 70)
    print(f"BACKCAST VERDICT: {bc['verdict']}   (policy assumed: {bc['policy']})")
    print("=" * 70)
    if bc["verdict"] == "INCONSISTENT":
        print("The assumed policy does NOT reproduce the account IBKR reports today.\n"
              "The reconstructed path below is therefore WRONG. Try the other policy, or\n"
              "narrow the horizon with HELD_SINCE, or disable the backcast entirely.")
    elif bc["verdict"] == "indeterminate":
        print("The assumption fits — but on this data the candidate policies imply cost\n"
              "bases too close to tell apart, so the fit is not evidence for it. Treat the\n"
              "path as illustrative only.")
    else:
        print("The assumption fits, and the data was capable of rejecting it.")
    display(bc["check"][["symbol", "our_position", "ib_position", "our_avg_entry",
                         "ib_avg_entry", "avg_entry_rel_diff", "verdict"]].round(4))
    if bc["separation"] is not None:
        print("\npolicy separation (how distinguishable the candidates are on this data):")
        display(bc["separation"].round(4))

# %% 8b. TRACING AN INCONSISTENT VERDICT — decompose it symbol by symbol
# 8b. TRACING AN INCONSISTENT VERDICT — decompose it symbol by symbol
#
# ══════════════════════════════════════════════════════════════════════════════
# "INCONSISTENT" is a verdict on the WHOLE run — one bad symbol condemns the lot.
# It has exactly three causes and they need completely different responses, so the
# first job is always to say WHICH one applies to WHICH symbol:
#
#   A. NO DATA        our_position 0 vs ib_position N. The symbol was dropped in
#                     cell 7 (unpriced), so backcast.run() filtered it out of
#                     anchor_qty — but reconcile() still unions it in from the
#                     account. This is a data gap wearing a falsification's
#                     clothes. Fix the subscription/contract, or accept it is out.
#   B. WRONG UNITS    avg_entry off by a clean power of ten (see cell 7b). Not a
#                     statement about the policy at all.
#   C. WRONG HORIZON  avg_entry off by a few percent. THIS is the only case where
#                     the check is doing its actual job: the assumed start date
#                     (BACKCAST_MONTHS ago) is not when the position was really
#                     opened, so the entry price is the wrong bar's price.
#
# WHAT backcast.run() HANDS BACK, and what each part is good for:
#   anchor_qty     dict  — real position at the start of the executions window
#                          (step 1, measured, not assumed)
#   position_path  frame — units per symbol per bar, the policy's actual claim
#   fills          list  — the trades that path implies; for buy_and_hold there is
#                          exactly ONE per symbol, so its price IS our avg_entry
#   book           Book  — end state of the forward replay
#   state          frame — per bar, per symbol: {sym}_realised_pnl,
#                          {sym}_unrealised_pnl, {sym}_position, ... plus equity
#   check          frame — the comparison table printed above
#   separation     frame — could the check have failed at all
# ══════════════════════════════════════════════════════════════════════════════

if bc is not None:
    chk = bc["check"].set_index("symbol")

    # One row per symbol: what the policy assumed, what it produced, and — the useful
    # part — the DATES on which the market actually traded at IB's cost basis. Those
    # dates are the candidate values for HELD_SINCE, read straight off the data
    # instead of guessed at.
    trace_rows = {}
    for s in chk.index:
        row = chk.loc[s]
        ib_avg = float(row["ib_avg_entry"])

        # Cause A: held at the broker, absent from our reconstruction.
        if s not in prices.columns:
            trace_rows[s] = {"cause": "A: no price data", "assumed_start": None,
                             "entry_used": np.nan, "ib_avg_entry": ib_avg,
                             "rel_diff": np.nan, "cost_basis_seen": None}
            continue

        # The bar the policy actually bought at: the first bar where the path is non-zero.
        path_s = bc["position_path"][s] if s in bc["position_path"].columns else None
        start = path_s[path_s != 0].index.min() if path_s is not None else None
        entry_used = float(prices.loc[start, s]) if start is not None else np.nan

        # Cause B vs C: a clean power of ten is a unit bug, anything else is the horizon.
        rel = float(row["avg_entry_rel_diff"]) if np.isfinite(row["avg_entry_rel_diff"]) else np.nan
        ratio = entry_used / ib_avg if ib_avg else np.nan
        if np.isfinite(ratio) and abs(np.log10(abs(ratio))) > 0.5:
            cause = "B: unit mismatch"
        elif np.isfinite(rel) and rel > 0.02:
            cause = "C: wrong start date"
        else:
            cause = "consistent"

        # Every bar whose close sits within the check's 2% band of IB's average cost —
        # i.e. every date on which this position COULD have been opened at that price.
        band = prices.index[(prices[s] - ib_avg).abs() <= 0.02 * abs(ib_avg)] if ib_avg else []
        seen = (f"{band[0].date()} .. {band[-1].date()} ({len(band)} bars)"
                if len(band) else "never in window")

        trace_rows[s] = {"cause": cause,
                         "assumed_start": start.date() if start is not None else None,
                         "entry_used": entry_used, "ib_avg_entry": ib_avg,
                         "rel_diff": rel, "cost_basis_seen": seen}

    trace = pd.DataFrame(trace_rows).T
    print("WHY each symbol landed where it did:")
    display(trace)

    # The actionable output: a HELD_SINCE dict to paste into cell 2. Only the symbols
    # whose start date is the problem — a unit bug or a missing subscription is not
    # something a different start date can repair.
    suggest = {}
    for s, r in trace_rows.items():
        if r["cause"] != "C: wrong start date" or s not in prices.columns:
            continue
        ib_avg = r["ib_avg_entry"]
        # Nearest bar to IB's cost basis: the single most likely opening date under a
        # one-trade assumption. A judgement call, not a measurement — labelled as such.
        nearest = (prices[s] - ib_avg).abs().idxmin()
        suggest[s] = str(nearest.date())
    if suggest:
        print("\nCandidate HELD_SINCE (nearest bar to IB's cost basis — a HYPOTHESIS to test,\n"
              "not a measurement; re-run cell 8 with it and see if the verdict changes):")
        print(f"HELD_SINCE = {suggest}")

    # The fills are the finest grain available: for buy_and_hold there is one per symbol
    # and its price is, by construction, exactly our avg_entry. If a number above looks
    # impossible, this is where to look first.
    print(f"\nimplied fills ({len(bc['fills'])} total) — the trades the policy claims happened:")
    display(pd.DataFrame([{"ts": f.ts, "symbol": f.symbol, "side": f.side,
                           "qty": f.qty, "price": f.price} for f in bc["fills"]]).round(4))

# %% 8c. Per-symbol trust — computed once, worn by every chart below
# 8c. Per-symbol trust — computed once, worn by every chart below
#
# backcast.run() collapses the whole run into ONE verdict string, but check() built it from a
# per-symbol table and kept that table. This cell reads the per-symbol column back out and
# turns it into the three things cells 9-11 need: who to trust, how to label them, and one
# banner sentence to print above each figure.
#
# Deriving them HERE, once, is deliberate: three cells drawing their own conclusions about
# which symbols are sound is three chances for the legends to disagree with each other.

if bc is not None:
    chk_idx = bc["check"].set_index("symbol")

    # Three states, and the third is NOT a kind of failure. A symbol with no price history
    # never entered anchor_qty (backcast.run filters on prices.columns), so the backcast made
    # no claim about it at all — reconcile only lists it because it unions in the account's
    # holdings. Calling that INCONSISTENT would report a missing subscription as a refuted
    # assumption, which is the mistake that made the original output so misleading.
    sym_verdict = {}
    for s in chk_idx.index:
        if s not in prices.columns:
            sym_verdict[s] = "no price data"
        else:
            sym_verdict[s] = str(chk_idx.loc[s, "verdict"])

    trusted = [s for s, v in sym_verdict.items() if v == "consistent"]
    suspect = [s for s, v in sym_verdict.items() if v != "consistent"]

    def label_for(s):
        """Legend entry for one symbol, carrying its verdict.

        # ====================================================================
        # Used by EVERY label_map below so the marking cannot drift between
        # charts. A reader looking at a stacked area must be able to tell, from
        # the legend alone, which bands are reconstructions that survived the
        # falsification check and which are not — the alternative is a chart
        # that looks equally authoritative everywhere.
        # ====================================================================
        """
        base = f"{s} ({cfg.asset_role(s)})"
        v = sym_verdict.get(s, "consistent")
        if v == "consistent":
            return base
        # Name the reason, not just the fact: "no data" and "does not fit" call for
        # completely different responses from the reader.
        tag = "NO DATA" if v == "no price data" else "UNVERIFIED"
        # ASCII marker, not a warning glyph: this string is print()ed as well as plotted, and
        # a Windows console on cp1252 raises UnicodeEncodeError on anything outside latin-1.
        return f"{base} (!) {tag}"

    # One sentence, printed above each figure so the caveat travels with the picture even
    # when a single cell is re-run in isolation.
    if suspect:
        banner = (f"(!) {len(suspect)} of {len(sym_verdict)} symbols UNVERIFIED "
                  f"({', '.join(f'{s}: {sym_verdict[s]}' for s in suspect)}). "
                  f"They are still drawn — marked in the legend. See cell 8b for why.")
    else:
        banner = f"all {len(sym_verdict)} symbols consistent with the assumed policy."

    # Suffix for figure titles: the run-level verdict plus how much of it is in doubt.
    title_tag = (f"{bc['verdict']} — {len(suspect)}/{len(sym_verdict)} unverified"
                 if suspect else bc["verdict"])

    # One colour per ticker, assigned ONCE and reused by every figure below. Built from the
    # slot palette in portutils.viz.theme rather than TICKER_COLOURS.get(s, "#6baed6"): that
    # dict only pins the rebalance study's four names, so every holding in this account fell
    # through to the SAME fallback blue and the stacked charts read as a single series.
    # `symbols` is already sorted, so the same basket always gets the same colours.
    ticker_colours = study.ticker_colour_map(symbols)

    print(banner)
    display(pd.Series(sym_verdict, name="verdict").to_frame())

# %% 9. Reconstructed P&L path
# 9. Reconstructed P&L path
#
# Provenance is stamped on every row so the modelled segment can never be mistaken for
# measured history. Renders whatever the verdict is (see SHOW_SUSPECT_BACKCAST in cell 2);
# what the verdict changes is how the numbers are LABELLED, not whether they appear.

if bc is not None and (SHOW_SUSPECT_BACKCAST or bc["verdict"] != "INCONSISTENT"):
    print(banner)
    state = bc["state"]
    display(state[[c for c in state.columns if c.startswith("TOTAL_")]].tail().round(2))

    # HOW MUCH OF THE HEADLINE RESTS ON UNVERIFIED RECONSTRUCTION.
    # The TOTAL_ columns plotted below sum every symbol, sound or not, so a large P&L number
    # can be driven entirely by the one holding whose cost basis did not check out. Recompute
    # the same totals over the trusted subset only: the gap between the two rows IS the
    # exposure to the failed assumption, and it is a number rather than a caveat.
    if suspect:
        def _sum_over(syms, field):
            cols = [f"{s}_{field}" for s in syms if f"{s}_{field}" in state.columns]
            return float(state[cols].iloc[-1].astype(float).sum()) if cols else 0.0

        exposure = pd.DataFrame({
            "realised": {"all symbols": float(state["TOTAL_realised_pnl"].iloc[-1]),
                         "trusted only": _sum_over(trusted, "realised_pnl")},
            "unrealised": {"all symbols": float(state["TOTAL_unrealised_pnl"].iloc[-1]),
                           "trusted only": _sum_over(trusted, "unrealised_pnl")},
        })
        exposure.loc["difference (unverified)"] = (exposure.loc["all symbols"]
                                                   - exposure.loc["trusted only"])
        print("\nhow much of the total comes from symbols that FAILED the check:")
        display(exposure.round(2))

    split = pd.DataFrame({
        "realised": state["TOTAL_realised_pnl"].astype(float),
        "unrealised": state["TOTAL_unrealised_pnl"].astype(float),
        "total": state["TOTAL_total_pnl"].astype(float),
    })
    split["time"] = split.index
    fig = make_level_figure(
        split.reset_index(drop=True),
        cols_of_interest=["realised", "unrealised"],
        reindex=False, stack_mode="stack_split_sign",
        show_overall_line=True, overall_col="total",
        overall_label="total P&L", overall_colour="white",
        colour_map=study.SPLIT_COLOURS,
        label_map={"realised": "realised (banked)", "unrealised": "unrealised (at risk)"},
        auto_colour_map=False, auto_label_map=False,
        figure_title=f"Reconstructed P&L split - backcast ({bc['policy']}) [{title_tag}]",
        fig_height=600, font_color="#e0e0e0",
        x_tick_label_mode="year_month", close_hour=99,
        show_plotly_stack_mode_buttons=False,
    )
    fig.show()

# %% 10. Realised AND unrealised P&L by ticker and by role
# 10. Realised AND unrealised P&L by ticker and by role
#
# Who did the crystallising — the same question rebalance_realisation.py asks of a simulated
# book, asked here of the real one. Two charts: what has been banked per name, and what is
# still open per name.

if bc is not None and (SHOW_SUSPECT_BACKCAST or bc["verdict"] != "INCONSISTENT"):
    print(banner)
    state = bc["state"]
    # NOTE the missing .dropna(): a holding with no price history used to be silently deleted
    # from this table, so the account appeared to consist only of the symbols we happened to
    # be able to price. It now survives as a NaN row carrying its `no price data` verdict —
    # a visible hole is honest, a vanished row is not.
    # IBKR's own per-position figures, from the reconcile table cell 4 already built off the
    # account snapshot. Putting them beside the reconstruction is the whole point of this
    # script: a modelled number with the measured one next to it can be judged, a modelled
    # number alone cannot.
    ib_side = rec.set_index("symbol")

    per_ticker = pd.DataFrame({
        "role": pd.Series({s: cfg.asset_role(s) for s in symbols}),
        "verdict": pd.Series({s: sym_verdict.get(s, "not in backcast") for s in symbols}),
        # `bc_` prefix on ours: these are RECONSTRUCTED from the policy walk, and the columns
        # sit inches from IBKR's measured ones. An unprefixed "realised" next to "ib_realised"
        # invites exactly the conflation the file's header forbids.
        "bc_realised": pd.Series({s: float(state[f"{s}_realised_pnl"].iloc[-1])
                                  for s in symbols if f"{s}_realised_pnl" in state.columns}),
        "bc_unrealised": pd.Series({s: float(state[f"{s}_unrealised_pnl"].iloc[-1])
                                    for s in symbols if f"{s}_unrealised_pnl" in state.columns}),
        # ══════════════════════════════════════════════════════════════════════════════════
        # ONLY ONE OF THESE TWO IS COMPARABLE TO OURS.
        #   ib_unrealised  IS comparable. Both sides mark the SAME position at today's price,
        #                  so a gap here means our cost basis differs from IB's — which is
        #                  precisely what the backcast is being tested on.
        #   ib_realised    IS NOT. updatePortfolio's realizedPnL is typically SESSION-scoped
        #                  (see the header of portutils/portfolio/ibkr_sync.py), while the
        #                  backcast's is cumulative over the whole reconstructed window.
        #                  Subtracting them would compare a day against a year. It is shown
        #                  because it is real data worth seeing, and named to prevent the
        #                  comparison, not to invite it.
        # ══════════════════════════════════════════════════════════════════════════════════
        "ib_unrealised": pd.Series({s: float(ib_side.loc[s, "ib_unrealised"])
                                    for s in symbols if s in ib_side.index}),
        "ib_realised_session": pd.Series({s: float(ib_side.loc[s, "ib_realised"])
                                          for s in symbols if s in ib_side.index}),
    })
    # The one difference worth computing. Large here == the reconstruction's entry price is
    # wrong for that name, which is the same defect cell 8b diagnoses from the cost basis;
    # this expresses it in money rather than in percent.
    per_ticker["unrealised_diff"] = per_ticker["bc_unrealised"] - per_ticker["ib_unrealised"]
    display(per_ticker.round(2))

    print("\nby role (backcast vs IBKR, unrealised only — realised is not comparable):")
    # sum() skips NaN, so the unpriced rows drop out of the role totals automatically —
    # which is right, but it means these totals cover only the priced part of each role.
    # ib_realised_session is deliberately absent from this aggregate: summing a session
    # figure into a role total produces a number that means nothing at all.
    display(per_ticker.groupby("role")[["bc_realised", "bc_unrealised", "ib_unrealised",
                                        "unrealised_diff"]].sum().round(2))

    # ─────────────────────────────────────────────────────────────────────────────────────
    # TWO CHARTS, ONE CODE PATH — realised then unrealised, per ticker.
    # Cell 9 shows the realised/unrealised split at the BOOK level; this pair answers the
    # next question, which is WHICH NAME each half came from. They are drawn separately
    # rather than stacked together because they are not the same kind of number:
    #   realised    banked, monotone-ish, a record of decisions already taken. Only moves
    #               on a fill, so a flat line means "no trading", not "no movement".
    #   unrealised  mark-to-market, moves every bar, and is fully reversible. A big band
    #               here is exposure, not profit.
    # Adding them into one stack would let a reader mistake an open gain for a booked one.
    # The loop keeps the two figures identical in every respect except the field, so any
    # visual difference between them is a difference in the DATA, not in the plotting.
    # ─────────────────────────────────────────────────────────────────────────────────────
    for field, overall_label, chart_title in (
        ("realised_pnl", "total realised", "Cumulative realised P&L by ticker"),
        ("unrealised_pnl", "total unrealised", "Unrealised P&L by ticker (mark-to-market)"),
    ):
        # Only symbols the backcast actually reconstructed have per-symbol columns; an
        # unpriced holding has none and is simply absent from the stack (it is named in the
        # table above and in the banner, so its absence is already accounted for).
        cols = [s for s in symbols if f"{s}_{field}" in state.columns]
        if not cols:
            print(f"no {field} columns in the state frame — nothing to plot.")
            continue

        cum = pd.DataFrame({s: state[f"{s}_{field}"].astype(float) for s in cols})
        # The TOTAL_ line is the book-level figure straight from Book.snapshot, NOT the sum
        # of the bands below it. Plotting it as the overall line means a discrepancy between
        # the two would be visible rather than hidden by construction.
        cum["overall"] = state[f"TOTAL_{field}"].astype(float)
        cum["time"] = cum.index
        make_level_figure(
            cum.reset_index(drop=True),
            cols_of_interest=[c for c in cum.columns if c not in ("overall", "time")],
            # stack_split_sign keeps winners above the axis and losers below instead of
            # netting them off — with P&L the cancellation is exactly what you want to see.
            reindex=False, stack_mode="stack_split_sign",
            show_overall_line=True, overall_col="overall",
            overall_label=overall_label, overall_colour="white",
            colour_map=ticker_colours,
            label_map={s: label_for(s) for s in symbols},
            auto_colour_map=False, auto_label_map=False,
            figure_title=f"{chart_title} [{title_tag} backcast]",
            fig_height=600, font_color="#e0e0e0",
            x_tick_label_mode="year_month", close_hour=99,
            show_plotly_stack_mode_buttons=False,
        ).show()

# %% 11. Weight path vs target
# 11. Weight path vs target

if bc is not None and (SHOW_SUSPECT_BACKCAST or bc["verdict"] != "INCONSISTENT"):
    print(banner)
    path = bc["position_path"]
    value = path * prices.loc[path.index, path.columns]
    # The denominator is the value of the RECONSTRUCTED symbols only, so these weights sum
    # to 1 across a universe that may be smaller than the account. An unpriced holding is not
    # in `path` at all, and its absence inflates every weight that is plotted. Quantify it
    # from the broker's own marks — a chart claiming 40% in something that is really 32% of
    # the account is worse than no chart.
    excluded = [s for s in symbols if s not in path.columns]
    if excluded:
        excl_value = sum(book.position(s).qty * marks.get(s, 0.0) for s in excluded)
        print(f"NOT IN THIS CHART: {', '.join(excluded)} — {excl_value:,.0f} of "
              f"{net_liq:,.0f} NetLiq ({excl_value / net_liq:.1%}). Every weight below is "
              f"normalised over the rest and is therefore overstated by roughly that much.")
    w = value.div(value.sum(axis=1), axis=0)
    w["time"] = w.index
    make_level_figure(
        w.reset_index(drop=True),
        cols_of_interest=[c for c in w.columns if c != "time"],
        reindex=False,
        # Same map as cells 9-10 — a ticker must not change colour between the P&L chart and
        # the weight chart, or the two cannot be read against each other.
        colour_map=ticker_colours,
        label_map={s: label_for(s) for s in path.columns},
        auto_colour_map=False, auto_label_map=False,
        figure_title=f"Reconstructed weights [{title_tag} backcast: {bc['policy']}]",
        fig_height=600, font_color="#e0e0e0",
        x_tick_label_mode="year_month", close_hour=99,
        show_plotly_stack_mode_buttons=False,
    ).show()

# %% 12. Disconnect
# 12. Disconnect

ib.disconnect_ib(ib_app)
print("disconnected")

# %%
