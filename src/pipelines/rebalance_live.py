"""
LIVE CONSTANT-MIX REBALANCER — the studied policy, pointed at a real IBKR account.

This is the live counterpart of the `mix` book in src/pipelines/rebalance_study.py. It does
not reimplement the policy: it imports and calls the SAME ``ConstantMixRule`` that produced
the backtested result, so the code deciding what to trade with real money is literally the
code that was measured. A live rebalancer that reimplements its own arithmetic is a live
rebalancer that will eventually disagree with its own research.

Flow:
    connect -> get_account_updates -> book_from_portfolio -> BASE-CURRENCY marks
            -> ConstantMixRule.propose() -> signed unit deltas -> round to whole shares
            -> min-turnover gate -> max-order-value guard -> print table -> (maybe) submit

═══════════════════════════════════════════════════════════════════════════════
MIXED CURRENCIES AND MIXED VENUES — why this is not a two-ticker script
═══════════════════════════════════════════════════════════════════════════════
The account holds London lines beside US ones. Two things follow, and both are handled
by taking the broker's numbers rather than computing our own:

1. EVERY POSITION IS CONVERTED INTO THE BASE CURRENCY BEFORE IT IS WEIGHED.
   updatePortfolio DOES NOT CONVERT. `marketPrice`, `marketValue`, `averageCost` and
   `unrealizedPnL` all arrive in the POSITION'S OWN currency — this account holds a Xetra
   line in EUR, two LSE lines in GBP and a NYSE line in USD, in one frame. Only
   NetLiquidation is in the base currency. So `qty * marketPrice / net_liq` adds euros to
   pounds and calls the result a weight: measured on the real account, a EUR holding showing
   0.80% is really ~0.70%, and `max_order_value` ends up comparing three currencies against
   one ceiling.

   The rate comes from IBKR's own account ledger (`get_exchange_rates`, the `$LEDGER:ALL`
   summary), so converted values agree with the NetLiquidation they are divided by. A
   currency with no rate is NOT assumed to be 1:1 — the leg is left unpriced and skipped.
   The applied rate and the resulting quote/base ratio are printed per leg, so the
   conversion is visible rather than assumed; a ratio of ~100 would additionally reveal a
   venue quoting in pence, which the historical bars do even though the portfolio rows
   do not.

2. ORDERS NAME A CONTRACT, NOT A TICKER.
   `submit_market_order` defaults to STK/SMART/USD. For a London line that is a different
   instrument — or a rejection. The conId of every held position is in the account snapshot,
   so `contract_specs_from_portfolio` feeds it straight back into the order.

A symbol in the target that is NOT held has neither a marketValue nor a conId. Its mark is
fetched from recent history, and if that fails it is SKIPPED — never priced by assumption.

═══════════════════════════════════════════════════════════════════════════════
DRY RUN vs LIVE — how the decision is made
═══════════════════════════════════════════════════════════════════════════════
Resolution order, most specific first:

    1. --dry-run           ALWAYS dry, whatever the config says. The escape hatch; it is
                           deliberately impossible to disable this from configuration.
    2. --live              transmit orders.
    3. live_trading.enabled in config/settings.yaml   the standing default.

Setting `enabled: true` in config means every subsequent run trades for real with no flag
and no prompt. That is intentional standing authorization — which is exactly why the guards
below stay in place rather than also being made optional:

    * the full order table is printed BEFORE anything is transmitted, on every run;
    * `require_paper` refuses account ids that do not start with "DU" (a real account needs
      --i-know-this-is-real on top of --live);
    * `max_order_value` rejects any single order above the ceiling, so one bad price tick
      cannot produce an enormous order;
    * the resolved weights must match config/asset_universe.yaml, so the live policy cannot
      silently drift from the studied one;
    * every submitted order is written to outputs/live/orders_<timestamp>.csv, an audit
      trail that does not depend on TWS.

Usage:
    python src/pipelines/rebalance_live.py                 # dry run (unless config says otherwise)
    python src/pipelines/rebalance_live.py --live          # transmit
    python src/pipelines/rebalance_live.py --dry-run       # force dry, overrides config
"""

import argparse
import datetime as dt
import pathlib
import sys
import time  # the post-submit drain before disconnecting — see the finally block

import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from portutils.portfolio import ConstantMixRule, book_from_portfolio   # noqa: E402
from portutils.utils import config as cfg                              # noqa: E402

OUT_DIR = REPO / "outputs" / "live"


def live_settings():
    """The `live_trading` block, with defaults for anything absent.

    Defaults are the SAFE end of every switch: if the config is missing or partial, the
    result is a dry run against the paper account, not a live one.
    """
    s = dict(cfg.SETTINGS.get("live_trading") or {})
    s.setdefault("enabled", False)
    s.setdefault("account", "DUP102412")
    s.setdefault("require_paper", True)
    s.setdefault("max_order_value", 50_000)
    s.setdefault("min_turnover", 0.0001)
    s.setdefault("portfolio", "spy_kmlm")
    s.setdefault("client_id", 151)
    # Contract details for target symbols we do NOT hold. A held position carries its own
    # conId in the account snapshot; a name being opened from flat has nothing, so the
    # bare ticker goes to TWS on the STK/SMART/USD default — correct for a US listing and
    # wrong for anything else (CSPX is a London line, so `CSPX` alone would either fail to
    # resolve or resolve to something we did not mean).
    s.setdefault("contract_overrides", {})
    return s


def market_hours_note(now=None):
    """Is anything in this basket likely to be open right now?

    # ========================================================================
    # A WARNING, NEVER A BLOCK. Queueing orders for the next open is a perfectly
    # legitimate thing to want; doing it WITHOUT REALISING is not — the first
    # armed run of this script went out on a Sunday, when LSE, IBIS2 and NYSE
    # were all shut, and nothing in the output said so.
    #
    # Deliberately crude: local weekday and clock hour, not a venue calendar.
    # Holidays and half-days are not modelled, so a quiet "looks open" is not a
    # promise. It exists to catch the blatant case, and it says which case it is
    # rather than implying more precision than it has.
    # ========================================================================

    Returns (looks_open, message).
    """
    now = now or dt.datetime.now()
    # Monday=0 ... Saturday=5, Sunday=6.
    if now.weekday() >= 5:
        return False, (f"{now:%A %d %b} is a WEEKEND — every venue in this book "
                       f"(LSE, IBIS2, NYSE, SMART) is closed. Market orders cannot "
                       f"execute; at best they queue for the next open.")
    # Rough envelope covering this basket in UK local time: LSE/Xetra from 08:00,
    # US cash from 14:30, US close 21:00.
    if now.hour < 8 or now.hour >= 21:
        return False, (f"{now:%H:%M} local is OUTSIDE the trading day for this book "
                       f"(roughly 08:00-21:00 UK: LSE/Xetra from 08:00, US from 14:30). "
                       f"Market orders cannot execute now.")
    if now.hour >= 17:
        return True, (f"{now:%H:%M} local — LSE and Xetra have closed (~16:30/17:30); "
                      f"only the US legs can trade.")
    if now.hour < 14 or (now.hour == 14 and now.minute < 30):
        return True, (f"{now:%H:%M} local — European venues are open, US cash does not "
                      f"open until 14:30; the US legs will queue until then.")
    return True, f"{now:%A %H:%M} local — European and US venues both open."


def resolve_mode(args, settings):
    """Return (dry_run, why) — the single place the live/dry decision is made.

    Kept as one small function precisely because this is the decision that moves real money:
    it should be readable in one glance and testable without a broker.
    """
    if args.dry_run:
        return True, "--dry-run given (overrides config)"
    if args.live:
        return False, "--live given"
    if settings["enabled"]:
        return False, "live_trading.enabled: true in config/settings.yaml"
    return True, "default (no --live, config disabled)"


def base_currency_marks(portfolio_df, fx_rates=None, base_ccy=None):
    """Per-share prices converted into the ACCOUNT'S BASE CURRENCY.

    # ========================================================================
    # THE ONE CALCULATION THAT MAKES A MIXED-CURRENCY BOOK SAFE TO REBALANCE.
    #
    # updatePortfolio DOES NOT CONVERT ANYTHING. `marketPrice`, `marketValue`,
    # `averageCost` and `unrealizedPnL` all arrive in the POSITION'S OWN currency
    # — a Xetra line in EUR, an LSE line in GBP, a NYSE line in USD, side by
    # side in one frame. Only NetLiquidation is in the base currency. So
    # `qty * marketPrice / net_liq` adds euros to pounds and calls the result a
    # weight. (Measured on the real account: a EUR holding showing 0.80% is
    # really ~0.70% once converted — every non-base leg is overstated by exactly
    # its FX rate, and `max_order_value` compares three currencies against one
    # ceiling.)
    #
    # The rate is IBKR'S OWN, read off the account ledger by
    # get_exchange_rates(), so converted values agree with the NetLiquidation
    # they are divided by. A rate pulled from market data would be a second
    # opinion on a number the broker has already decided.
    #
    # A currency with NO rate is NOT assumed to be 1:1 — that is the whole class
    # of bug this function exists to kill. The symbol gets no mark, and
    # build_orders reports it as unpriced rather than trading it at face value.
    # ========================================================================

    Parameters
    ----------
    portfolio_df : pd.DataFrame
        Output of ``ibkr_requests.get_account_updates``.
    fx_rates : dict[str, float] | None
        Currency -> multiplier into base, from ``get_exchange_rates``. None or
        empty means "no conversion available": base-currency legs still price
        normally, everything else is skipped.
    base_ccy : str | None
        The account's base currency, so a leg already denominated in it needs no
        rate. Taken from the NetLiquidation row's currency.

    Returns
    -------
    (marks, detail) : (dict[str, float], pd.DataFrame)
        `marks` maps symbol -> base-currency price per share.
        `detail` carries the quote price, the rate applied, the base price and the
        implied ratio between them, for the pre-trade table. The ratio is what
        makes a conversion visible instead of silent: ~1 for a base-currency leg,
        the FX rate for a foreign one, and ~100 if a venue ever quotes in pence.
    """
    fx_rates = {k.upper(): v for k, v in (fx_rates or {}).items()}
    base_ccy = str(base_ccy).upper() if base_ccy else None

    marks, rows = {}, []
    if portfolio_df is None or len(portfolio_df) == 0:
        return marks, pd.DataFrame()

    for _, row in portfolio_df.iterrows():
        symbol = row.get("symbol")
        if symbol is None:
            continue
        qty = float(row.get("position", 0.0) or 0.0)
        quote_px = float(row.get("marketPrice", 0.0) or 0.0)
        mkt_val = float(row.get("marketValue", 0.0) or 0.0)
        ccy = str(row.get("currency") or "").upper()

        # Prefer marketValue / qty over marketPrice: the two agree for an ordinary
        # equity, but the ratio between them catches a venue quoting in a sub-unit
        # (pence) without needing to know in advance which venues do that.
        quoted_px = (mkt_val / qty) if qty else quote_px

        # Resolve the rate into base. A leg already in the base currency is 1 by
        # definition; anything else needs a real rate or it is not priced at all.
        if base_ccy and ccy == base_ccy:
            rate = 1.0
        else:
            rate = fx_rates.get(ccy)
        base_px = quoted_px * rate if (rate is not None) else np.nan

        if np.isfinite(base_px) and base_px > 0:
            marks[symbol] = base_px

        rows.append({
            "symbol": symbol,
            "currency": ccy,
            "quote_price": quote_px,
            "fx_rate": rate if rate is not None else np.nan,
            "base_price": base_px,
            # quote / base: 1.0 means no conversion happened, which is only correct
            # for a base-currency leg. Anything else on a base-currency line, or a
            # 1.0 on a foreign one, means the conversion did not do its job.
            "fx_ratio": (quote_px / base_px) if (np.isfinite(base_px) and base_px) else np.nan,
            "exchange": row.get("exchange"),
            "conId": row.get("conId"),
        })
    return marks, pd.DataFrame(rows).set_index("symbol")


def pending_symbols(open_orders_df):
    """Symbols that already have a working order at IB.

    # ========================================================================
    # WHY A REBALANCER MUST LOOK BEFORE IT TRADES.
    # build_orders sizes from the POSITION, and a working order is not a position
    # yet. So a second run before the first one fills sees the same gap, proposes
    # the same trade again, and doubles the intended exposure — silently, because
    # every individual order looks correct. With market orders queued for an open,
    # that window is hours long.
    #
    # The guard is per SYMBOL rather than per order: any live order on a name means
    # our picture of that name is mid-flight and cannot be sized against.
    # ========================================================================

    Returns dict[str, str] — symbol -> short description of what is already working.
    """
    if open_orders_df is None or len(open_orders_df) == 0:
        return {}
    if "symbol" not in open_orders_df.columns:
        return {}

    out = {}
    for _, row in open_orders_df.iterrows():
        sym = row.get("symbol")
        if not sym:
            continue
        desc = (f"orderId {row.get('orderId')} {row.get('action', '?')} "
                f"{row.get('totalQuantity', '?')} [{row.get('status', '?')}]")
        # Several working orders on one symbol: keep them all in the message, because
        # "there is already an order" understates "there are already three".
        out[sym] = f"{out[sym]}; {desc}" if sym in out else desc
    return out


def build_orders(book, marks, weights, net_liq, settings, detail=None, pending=None):
    """Ask the studied policy what to trade, then apply the live-only guards.

    The deltas come from ``ConstantMixRule`` unchanged. What this function adds is the set of
    adjustments that only matter when the order is real: whole-share rounding, the
    min-turnover gate, and the per-order notional ceiling.

    `marks` MUST be base-currency prices (see ``base_currency_marks``) — every weight and
    every notional below is computed against ``net_liq``, which is in the base currency.
    `detail` is the companion frame from that function; it only decorates the output table.
    `pending` maps symbol -> description of an already-working order; those symbols are
    skipped (see ``pending_symbols``). Pass {} to disable the guard.
    """
    pending = pending or {}
    # every=1 so a single call proposes the full rebalance for "now" — the rule's bar cadence
    # is irrelevant when it is invoked once per run rather than marched over a series.
    rule = ConstantMixRule(weights, every=1, min_trade_frac=0.0)
    deltas = rule.propose(pd.Timestamp.now(), marks, book)

    detail = detail if detail is not None else pd.DataFrame()

    # THE UNIVERSE IS THE UNION, NOT THE TARGET.
    # Iterating `weights` alone means a position we hold but no longer want can never be
    # sold — it is simply not considered, so it sits there forever while the rest of the
    # book is rebalanced around it. A holding outside the target has target_w = 0, which is
    # a real instruction ("close it"), not an absence of one.
    universe = sorted(set(weights) | set(book.symbols))

    rows = []
    for symbol in universe:
        px = marks.get(symbol)
        target_w = float(weights.get(symbol, 0.0))
        d = detail.loc[symbol] if symbol in detail.index else None

        if px is None or not np.isfinite(px) or px <= 0:
            # No mark means no order. Filling against an invented price is how a rebalancer
            # turns a data outage into a real loss.
            # target_value is still stated: "we wanted 40,000 of this and could not price
            # it" is a more useful line than a row of blanks. current_value is None
            # rather than 0.0 — we do not know what the position is worth, and writing
            # zero would understate the book by exactly the amount we cannot see.
            rows.append({"symbol": symbol, "target_weight": target_w,
                         "current_qty": book.position(symbol).qty,
                         "current_value": None, "target_value": target_w * net_liq,
                         "units": 0, "status": "SKIP no mark"})
            continue

        current_qty = book.position(symbol).qty
        # Both terms are base currency: px via marketValue/qty, net_liq via NetLiquidation.
        current_w = (current_qty * px) / net_liq if net_liq else 0.0
        raw_delta = float(deltas.get(symbol, 0.0))
        # A holding outside the target gets no delta from the rule (it does not know the
        # symbol), so the instruction to close it has to be stated here: sell the lot.
        if symbol not in weights and current_qty != 0:
            raw_delta = -current_qty
        # Whole shares: IB will reject or silently alter a fractional equity order.
        units = int(np.trunc(raw_delta))
        notional = abs(units * px)

        status = "OK"
        # Checked FIRST, ahead of every sizing rule: if an order is already working on
        # this name, the position is mid-flight and no target computed from it is
        # trustworthy. Refusing outright is the only safe reading — trading a "corrected"
        # smaller amount would still double up once the first order fills.
        if symbol in pending:
            status = f"SKIP already working: {pending[symbol]}"
            units = 0
        elif symbol not in weights and current_qty != 0:
            # Deliberately NOT gated by min_turnover: "this is not in the book any more" is
            # a decision about the position's existence, not a drift correction.
            status = "CLOSE not in target"
            if units == 0:
                status = "SKIP rounds to zero shares"
        elif abs(target_w - current_w) < settings["min_turnover"]:
            # Already close enough. Trading here pays the spread to correct noise.
            status = f"SKIP below min_turnover ({settings['min_turnover']:.2%})"
            units = 0
        elif units == 0:
            status = "SKIP rounds to zero shares"
        if units != 0 and notional > settings["max_order_value"]:
            # Refuse rather than clip: an order this size means something upstream is wrong
            # (a bad tick, a stale NetLiq), and silently trading a smaller amount would hide
            # the fault while still acting on it. Checked after the branches above so a
            # CLOSE cannot slip past the ceiling.
            status = f"REJECT notional {notional:,.0f} > max_order_value {settings['max_order_value']:,.0f}"
            units = 0

        rows.append({
            "symbol": symbol,
            # Contract identity, carried into the table so the printed row and the
            # transmitted order are demonstrably the same instrument.
            "conId": (d["conId"] if d is not None else None),
            "exchange": (d["exchange"] if d is not None else "SMART"),
            "currency": (d["currency"] if d is not None else "USD"),
            "quote_price": (d["quote_price"] if d is not None else px),
            "fx_ratio": (d["fx_ratio"] if d is not None else 1.0),
            # MONEY FIRST, THEN WEIGHTS. A weight tells you the shape of the book; only
            # the value tells you whether a 0.4pp gap is worth four hundred or forty
            # thousand, which is the number that decides whether the trade is worth the
            # spread. Both are base currency, on the same basis as current_weight.
            "current_qty": current_qty,
            # base_price = quote_price * fx_ratio, so it belongs to the conversion block
            # above by derivation — but it is read as "the price this row is valued at",
            # so it sits with the quantity and value it multiplies out to.
            "base_price": px,
            "current_value": current_qty * px,
            # What the target weight is WORTH — so target_value - current_value is the
            # trade in money, directly comparable with notional_base below.
            "target_value": target_w * net_liq,
            "current_weight": current_w, "target_weight": target_w,
            # Kept immediately after the pair it is the difference of.
            "weight_diff": target_w - current_w,
            "raw_delta_units": raw_delta, "units": units,
            "action": "BUY" if units > 0 else ("SELL" if units < 0 else ""),
            "notional_base": units * px, "status": status,
        })
    return pd.DataFrame(rows)


def print_order_table(orders, net_liq, base_ccy="", weights=None, title="intended orders"):
    """Print the portfolio totals, then the order table, then the weights sanity line.

    # ========================================================================
    # ONE PRINTER, TWO CALLERS. main() below and cell 8 of
    # orders/rebalance_live_debug.py show the same frame, and until now each
    # formatted it its own way. A harness that presents the pipeline's numbers
    # differently from the pipeline teaches the wrong thing about the pipeline,
    # and the two had already drifted.
    #
    # THE TOTALS GO ABOVE THE TABLE, not below it: they are what every weight in
    # the table is measured against, so reading the table without them first is
    # reading percentages of an unknown number.
    #
    # Side-effecting by design (it prints) — which is why it lives in pipelines/
    # next to build_orders rather than in portutils/.
    # ========================================================================
    """
    # Sum of every row we could price. A symbol with no mark contributes nothing,
    # so the count of those is printed too — a total that is quietly short by one
    # position is worse than one that says how much it is missing.
    # to_numeric first: a table in which EVERY row is unpriced gives an all-None column of
    # object dtype, and .fillna(0) on that is deprecated (and would sum strings if a status
    # ever leaked in). errors="coerce" turns anything unparseable into NaN, which is the
    # honest reading of "we could not value this".
    values = (pd.to_numeric(orders["current_value"], errors="coerce")
              if "current_value" in orders else pd.Series(dtype=float))
    non_cash = float(values.sum())      # Series.sum() skips NaN by default
    unpriced = int(values.isna().sum())
    # Residual, NOT a balance IB reported — hence "implied". It is also the fastest
    # tell for a currency bug: an unconverted leg drives this negative or absurd.
    cash = net_liq - non_cash

    pct = (lambda v: f"{v / net_liq:6.1%}" if net_liq else "     -")
    print(f"\ntotal portfolio value  {net_liq:>16,.2f} {base_ccy}   (NetLiquidation)")
    print(f"total non-cash value   {non_cash:>16,.2f} {base_ccy}   ({pct(non_cash)} invested)")
    print(f"implied cash           {cash:>16,.2f} {base_ccy}   ({pct(cash)})")
    if unpriced:
        print(f"*** {unpriced} position(s) had no base price and are EXCLUDED from the "
              f"non-cash total — it is understated by however much they are worth. ***")

    print(f"\n{title}:")
    print(orders.round(4).to_string(index=False))

    # Sanity line the reader can check against the config in one glance.
    if weights:
        invested = orders["current_weight"].fillna(0).sum() if "current_weight" in orders else 0.0
        print(f"\ntarget weights sum to {sum(weights.values()):.4f} "
              f"(the remainder is cash by design); current invested weight "
              f"{invested:.4f}")


def print_order_outcomes(ack):
    """Say what happened to each order and what to DO about it.

    # ========================================================================
    # DRIVEN BY `verdict`, NOT RE-DERIVED HERE. This used to test status strings
    # ("PreSubmitted"/"PendingSubmit") and search notice text for "10311", which
    # is the same classification the library already performs — kept in two
    # places, drifting apart, and wrong in the pipeline first because that is the
    # path nobody watches run. check_orders computes the verdict once, from
    # status AND messages; this function only chooses which paragraph to print.
    #
    # Extracted from main() so it can be tested without a TWS connection: these
    # paragraphs are the operator's instructions after a live submit, and an
    # untested instruction is a guess.
    # ========================================================================
    """
    if not len(ack):
        return
    verdicts = ack["verdict"] if "verdict" in ack else pd.Series(dtype=object)
    missing = ack.loc[verdicts == "NO_ANSWER", "orderId"].tolist()
    if missing:
        print(f"\n*** {len(missing)} of {len(ack)} ORDERS WERE NEVER "
              f"ACKNOWLEDGED BY TWS (orderIds {missing}). They were most "
              f"likely NOT received. Check the TWS Orders panel before "
              f"re-running — do not assume they are working. ***")
    else:
        print(f"\nall {len(ack)} orders acknowledged by TWS:")
    print(ack.to_string(index=False))
    # A rejection arrives through the error callback, not orderStatus, so it
    # would otherwise be invisible in the status column alone. The symbol is
    # printed alongside because "orderId 18 was refused" is not actionable
    # until you know 18 was the SPY leg.
    for _, r in ack.iterrows():
        if str(r.get("verdict")) == "REJECTED":
            print(f"  orderId {r['orderId']} {r.get('symbol') or ''} "
                  f"REJECTED: {r.get('reason')}")
        elif r.get("notice"):
            print(f"  orderId {r['orderId']} {r.get('symbol') or ''} "
                  f"note: {r['notice']}")
    # Warning 10311 means TWS ACCEPTED the order but is holding it for manual
    # confirmation — it shows in the Pending panel with a Transmit button and
    # will never fill on its own. Neither working nor lost, and the only
    # outcome that needs a human to finish it.
    # Orders accepted and waiting for the venue to open. Not a problem — but
    # the reader should know nothing will fill until then, rather than
    # discovering it tomorrow.
    queued = [int(r["orderId"]) for _, r in ack.iterrows()
              if str(r.get("verdict")) == "PENDING_OPEN"]
    if queued:
        print(f"\norderIds {queued} are ACCEPTED and QUEUED — they sit at IB "
              f"until their venue opens, then go to the exchange. Nothing "
              f"fills before that. Cancel with: "
              f"python src/pipelines/cancel_orders.py --cancel")

    held = [r["orderId"] for _, r in ack.iterrows()
            if str(r.get("verdict")) == "HELD"]
    if held:
        print(f"\n*** orderIds {held} are HELD BY TWS pending manual Transmit "
              f"(precautionary setting for directly-routed orders). They are "
              f"NOT working. Either click Transmit in TWS, cancel them, or "
              f"enable 'Bypass Order Precautions for API Orders' in "
              f"Global Configuration > API > Precautions. ***")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="transmit orders")
    ap.add_argument("--dry-run", action="store_true",
                    help="force dry run; overrides live_trading.enabled in config")
    ap.add_argument("--i-know-this-is-real", action="store_true",
                    help="required to trade a non-paper account")
    ap.add_argument("--account", default=None)
    ap.add_argument("--portfolio", default=None)
    ap.add_argument("--max-order-value", type=float, default=None)
    ap.add_argument("--client-id", type=int, default=None)
    ap.add_argument("--override-pending", action="store_true",
                    help="trade a symbol even if it already has a working order at IB. "
                         "OFF by default: a working order is not a position yet, so a "
                         "second run would size against a stale picture and double up.")
    args = ap.parse_args()

    settings = live_settings()
    for key, val in (("account", args.account), ("portfolio", args.portfolio),
                     ("max_order_value", args.max_order_value), ("client_id", args.client_id)):
        if val is not None:
            settings[key] = val

    dry_run, why = resolve_mode(args, settings)
    print(f"mode: {'DRY RUN' if dry_run else '*** LIVE ***'}  ({why})")

    # Say whether the market is even open, before anything else. Printed on dry runs too:
    # a dry run's whole job is to show what the live run would do, and "it would sit in a
    # queue until Monday" is part of that.
    looks_open, hours_msg = market_hours_note()
    print(f"market hours: {'' if looks_open else '*** '}{hours_msg}{'' if looks_open else ' ***'}")

    # Guard: the live policy must be the studied policy. If the weights in the catalogue have
    # changed since the research was run, that is a decision to make deliberately, not to
    # discover after the orders have gone.
    weights = cfg.portfolio_weights(settings["portfolio"])
    print(f"portfolio '{settings['portfolio']}': {weights}")

    # Paper-account assertion, checked before connecting rather than after.
    account = settings["account"]
    if settings["require_paper"] and not str(account).upper().startswith("DU"):
        if not args.i_know_this_is_real:
            raise SystemExit(
                f"account {account} is not a paper account (no 'DU' prefix) and "
                f"require_paper is on. Pass --i-know-this-is-real to override.")
        print(f"!!! {account} is a REAL account and --i-know-this-is-real was given")

    # --- connect and read the account ---------------------------------------
    from portutils.ingestion import ibkr_requests as ib
    app = ib.IBApp()
    app.start(client_id=settings["client_id"])
    try:
        portfolio_df = ib.get_account_updates(app=app, account=account)
        acct = ib.get_account_data(app=app)
        net_liq_row = acct.loc[acct["tag"] == "NetLiquidation"].iloc[0]
        net_liq = float(net_liq_row["value"])
        # The base currency is not decoration: every weight, every notional and
        # max_order_value are denominated in it, so it belongs in the printout.
        base_ccy = str(net_liq_row.get("currency", "?"))

        # FX into the base currency, from IBKR's own ledger. Fetched BEFORE anything is
        # valued, because without it a multi-currency book cannot be weighted at all.
        fx_rates = ib.get_exchange_rates(app)
        if not fx_rates:
            print("WARNING: no exchange rates returned ($LEDGER:ALL) — only "
                  f"{base_ccy} positions can be priced; everything else will be skipped.")
        else:
            print(f"\nFX into {base_ccy}: "
                  + ", ".join(f"{c}={r:.4f}" for c, r in sorted(fx_rates.items())))

        # Base-currency marks, NOT raw marketPrice — see the header. `detail` carries the
        # quote/rate/base breakdown for the table.
        marks, detail = base_currency_marks(portfolio_df, fx_rates=fx_rates, base_ccy=base_ccy)

        # ══════════════════════════════════════════════════════════════════════════════
        # THE BOOK MUST BE BUILT ON BASE-CURRENCY PRICES TOO, NOT JUST THE MARKS.
        # book_from_portfolio back-solves its internal base equity so that
        # equity(marks) == NetLiquidation, and it does that from the frame's own
        # `marketPrice` and `averageCost`. Hand it the raw frame and the book holds
        # euros, pounds and dollars in one total: unrealised is a sum across currencies
        # and the back-solved base equity absorbs the error. ConstantMixRule then sizes
        # every target off `book.equity(prices)`, so one unconverted row produces wrong
        # targets for EVERY leg, not just its own.
        #
        # BOTH columns are converted, not just the price. avg_entry comes from
        # `averageCost`, so converting the mark alone would compute unrealised as
        # (base price - foreign cost) — a subtraction across two currencies, which is
        # worse than leaving both wrong together.
        # ══════════════════════════════════════════════════════════════════════════════
        priced_df = portfolio_df.copy()
        # Per-row rate, derived from the marks we just built: base_price / quoted_price.
        # Reusing that ratio rather than re-looking-up the rate guarantees the book and the
        # marks agree by construction, even for a leg whose rate was missing (NaN, which
        # leaves the row untouched and the symbol unpriced downstream).
        row_rate = []
        for _, r in priced_df.iterrows():
            sym, ccy = r.get("symbol"), str(r.get("currency") or "").upper()
            row_rate.append(1.0 if (base_ccy and ccy == base_ccy)
                            else fx_rates.get(ccy, np.nan))
        priced_df["_fx"] = row_rate
        priced_df["marketPrice"] = priced_df["marketPrice"] * priced_df["_fx"]
        priced_df["averageCost"] = priced_df["averageCost"] * priced_df["_fx"]
        # A row with no rate would carry NaN into the book and poison equity(). Drop it:
        # the position is reported as unpriced by build_orders, which is the honest state.
        dropped = priced_df.loc[priced_df["_fx"].isna(), "symbol"].tolist()
        if dropped:
            print(f"WARNING: no FX rate for {', '.join(dropped)} — excluded from the book "
                  f"and from every weight below.")
            priced_df = priced_df.loc[priced_df["_fx"].notna()]
        book = book_from_portfolio(priced_df.drop(columns=["_fx"]), base_equity=net_liq)

        # A target symbol not currently held has no portfolio row at all: no marketValue,
        # so no base price. Fall back to its last close. This only works for symbols whose
        # bare ticker resolves on SMART/USD (the US listings); a non-US name we do not yet
        # hold has neither a conId nor a resolvable symbol, and build_orders will report it
        # as SKIP no mark rather than invent a price.
        # Contract details for names we do not hold, from config. Held positions never
        # need these — they carry a conId — so the overrides are only consulted here and
        # for the order specs below.
        overrides = {k: dict(v) for k, v in (settings.get("contract_overrides") or {}).items()}

        unpriced_targets = [s for s in weights if s not in marks]
        if unpriced_targets:
            print(f"\nno position (so no marketValue) for {', '.join(unpriced_targets)} — "
                  f"pulling last close to price them")
            hist = ib.get_equity_data(symbols=unpriced_targets, duration="5 D",
                                      bar_size="1 day", output_format="dict", app=app,
                                      contract_specs=overrides)
            for sym, df in hist.items():
                if df is None or df.empty:
                    continue
                last = float(df["close"].iloc[-1])
                if not (np.isfinite(last) and last > 0):
                    continue
                # This close is in the symbol's QUOTE currency — USD by default, since
                # get_equity_data resolves an unknown ticker on STK/SMART/USD, but an
                # override can say otherwise (CSPX is a London line quoted in USD; a GBP
                # line would need the GBP rate, not the USD one). Read the currency from
                # the same override that resolved the contract, so the price and the rate
                # can never disagree about what currency this is.
                sym_ccy = str(overrides.get(sym, {}).get("currency", "USD")).upper()
                rate = 1.0 if base_ccy.upper() == sym_ccy else fx_rates.get(sym_ccy)
                if rate is None:
                    print(f"  {sym}: last close {last:,.4f} {sym_ccy} — NO {sym_ccy} RATE, "
                          f"skipping")
                    continue
                marks[sym] = last * rate
                print(f"  {sym}: last close {last:,.4f} {sym_ccy} -> "
                      f"{marks[sym]:,.4f} {base_ccy}")

        print(f"\naccount {account}: NetLiquidation {net_liq:,.2f} {base_ccy}, "
              f"{len(portfolio_df)} holdings")
        print(f"guards: max_order_value {settings['max_order_value']:,.0f} {base_ccy} "
              f"per order | min_turnover {settings['min_turnover']:.2%} | "
              f"require_paper {settings['require_paper']}")
        if len(detail):
            print(f"\ncontract / currency resolution — base_price = quoted * fx_rate, in "
                  f"{base_ccy}. fx_ratio 1.0 is correct ONLY for a {base_ccy} line; a 1.0 on "
                  f"a foreign leg means the conversion did not happen:")
            print(detail.round(4).to_string())

        # ══════════════════════════════════════════════════════════════════════════════
        # WHAT IS ALREADY WORKING AT IB — read BEFORE deciding anything.
        # build_orders sizes from the POSITION, and a working order is not a position
        # yet. Re-running before the first order fills would see the same gap, propose
        # the same trade, and double the exposure — invisibly, since each order looks
        # right on its own. With market orders queued for an open, that window is hours.
        # all_clients=True because an order placed by another client id is just as real.
        # ══════════════════════════════════════════════════════════════════════════════
        working = ib.get_open_orders_data(app, all_clients=True)
        pending = pending_symbols(working)
        if pending and args.override_pending:
            print(f"\n--override-pending given: IGNORING {len(pending)} working order(s) "
                  f"on {', '.join(sorted(pending))} and sizing as if they did not exist.")
            pending = {}
        elif pending:
            print(f"\nalready working at IB (these symbols will be SKIPPED):")
            for sym, desc in sorted(pending.items()):
                print(f"  {sym}: {desc}")

        orders = build_orders(book, marks, weights, net_liq, settings, detail=detail,
                              pending=pending)
        print_order_table(orders, net_liq, base_ccy=base_ccy, weights=weights)

        tradeable = orders[orders["units"].fillna(0) != 0] if "units" in orders else orders.iloc[0:0]
        if len(tradeable) == 0:
            print("\nnothing to trade.")
            return

        # --- submit -----------------------------------------------------------
        # The batch submitter comes from the LIBRARY, not from orders/rebalance_port_basic.py.
        # That file is a cell script whose module-level code opens its own IBKR connection
        # and runs a live rebalance on import — importing the function from there would have
        # transmitted orders before any check in this file ran. See the note on
        # submit_rebalance_orders in ibkr_requests.py.
        order_app = ib.OrderApp(app)
        # Contract specs come from the account snapshot, so each order names the exact
        # listing IBKR already told us we hold — conId, exchange and currency. The old
        # `.assign(currency="USD")` stamped dollars on every leg, which for a London line
        # is either a rejection or, worse, a different instrument that happens to share a
        # ticker. A target symbol we do not yet hold has no spec and falls back to the
        # STK/SMART/USD default, which is correct for the US listings and only those.
        # Config overrides first, the account snapshot second — the snapshot WINS, because
        # a conId straight from the broker beats anything hand-written in a config file.
        specs = {**overrides, **ib.contract_specs_from_portfolio(portfolio_df)}
        submitted = ib.submit_rebalance_orders(
            order_app, tradeable,
            symbol_col="symbol", currency_col="currency", units_col="units",
            dry_run=dry_run, contract_specs=specs,
        )
        print("\nsubmitted:")
        print(submitted.to_string(index=False))

        # ══════════════════════════════════════════════════════════════════════════════
        # WAIT FOR TWS TO ACTUALLY SAY SOMETHING — DO NOT REPORT UNOBSERVED SUCCESS.
        # placeOrder writes to the socket and returns; acceptance arrives later on the
        # reader thread. This function used to disconnect in the very next `finally`,
        # which closed the socket and stopped the reader before any callback could be
        # read — seven orders were reported "Submitted" into an empty TWS Orders panel.
        # Nothing below assumes an order landed; it reports what TWS answered, and says
        # so plainly when the answer was silence.
        # ══════════════════════════════════════════════════════════════════════════════
        ack = pd.DataFrame()
        if not dry_run:
            # ONE call, same infrastructure the debug harness uses: acknowledgement with
            # verdicts, then the broker's own view, then the two joined. show=False
            # because this file does its own printing below — which says what to DO
            # about each outcome, where the harness narration says what is running.
            #
            # sent=submitted makes OUR record the spine, so a REJECTED leg still has a
            # symbol. TWS sends no openOrder for an order it refuses, so without this the
            # rejected rows come back nameless — exactly the leg you most need to identify.
            ack = ib.check_orders(app, submitted["orderId"].tolist(),
                                  sent=submitted, show=False)
            print_order_outcomes(ack)

        # Audit trail, written for dry runs too so the intent is on record either way.
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = OUT_DIR / f"orders_{stamp}{'' if not dry_run else '_dryrun'}.csv"
        out = tradeable.copy()
        out["dry_run"] = dry_run
        out["mode_reason"] = why
        # Record what TWS SAID, not what was hoped. An audit trail claiming seven orders
        # were placed when TWS never saw them is worse than no audit trail.
        if len(ack):
            # JOIN ON orderId, NOT ON POSITION. This used to copy columns across by row
            # index, guarded on the two frames being the same length — which meant that
            # the moment a leg was rejected and the frames diverged, the guard fired and
            # the audit trail silently lost every status column. That is precisely the
            # run whose record matters most. A key join cannot mis-attach a status to the
            # wrong symbol, so the guard is no longer needed.
            keys = ["orderId", "verdict", "reason", "status", "acknowledged",
                    "n_errors", "n_notices", "error_1", "notice_1"]
            # submitted carries symbol -> orderId; tradeable (out) is keyed by symbol.
            id_map = submitted[["symbol", "orderId"]] if "orderId" in submitted else None
            if id_map is not None:
                out = out.merge(id_map, on="symbol", how="left")
                out = out.merge(ack[[c for c in keys if c in ack.columns]],
                                on="orderId", how="left", suffixes=("", "_ack"))
        out.to_csv(path, index=False)
        print(f"\nwrote {path}")
    finally:
        # Drain before hanging up. disconnect() stops the reader thread outright, so a
        # callback still in flight is lost — the exact mechanism that made the first live
        # run's orders disappear. A short pause costs nothing and keeps the socket honest.
        time.sleep(1.0)
        ib.disconnect_ib(app)


if __name__ == "__main__":
    main()
