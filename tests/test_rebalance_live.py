"""
Offline tests for the LIVE rebalancer's decision logic.

No TWS and no connection: these exercise the two functions that decide what happens with
real money — ``resolve_mode`` (dry vs live) and ``build_orders`` (what to trade) — against
stubbed inputs. They are the parts worth being certain about before the script is ever
pointed at an account.

The most important assertion in this file is that ``--dry-run`` beats
``live_trading.enabled: true``. That override is the escape hatch, and an escape hatch that
configuration can silence is not one.
"""

import argparse
import pathlib
import sys

import pandas as pd
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pipelines.rebalance_live import (base_currency_marks, build_orders,       # noqa: E402
                                      live_settings, resolve_mode)
from portutils.portfolio import book_from_portfolio                            # noqa: E402


def args(live=False, dry_run=False):
    return argparse.Namespace(live=live, dry_run=dry_run)


def settings(**over):
    s = {"enabled": False, "account": "DUP102412", "require_paper": True,
         "max_order_value": 50_000, "min_turnover": 0.02, "portfolio": "spy_kmlm",
         "client_id": 151}
    s.update(over)
    return s


def portfolio_frame(rows):
    return pd.DataFrame([{
        "account": "DUP102412", "symbol": s, "conId": i, "secType": "STK",
        "exchange": "SMART", "currency": "USD", "position": q, "marketPrice": mp,
        "marketValue": q * mp, "averageCost": ac, "unrealizedPnL": (mp - ac) * q,
        "realizedPnL": 0.0,
    } for i, (s, q, mp, ac) in enumerate(rows)])


# ---------------------------------------------------------------------------
# MODE RESOLUTION — the decision that moves real money
# ---------------------------------------------------------------------------
def test_default_is_dry_run():
    dry, _ = resolve_mode(args(), settings())
    assert dry is True


def test_live_flag_goes_live():
    dry, why = resolve_mode(args(live=True), settings())
    assert dry is False and "--live" in why


def test_config_enabled_is_the_standing_default():
    # The requested behaviour: set it once, never pass a flag again.
    dry, why = resolve_mode(args(), settings(enabled=True))
    assert dry is False and "config" in why


def test_dry_run_flag_overrides_config_enabled():
    # THE safety-critical case. Configuration must not be able to silence the escape hatch.
    dry, why = resolve_mode(args(dry_run=True), settings(enabled=True))
    assert dry is True
    assert "overrides config" in why


def test_dry_run_beats_live_flag_too():
    # Both flags given: the safe one wins. Never the other way round.
    dry, _ = resolve_mode(args(live=True, dry_run=True), settings(enabled=True))
    assert dry is True


def test_live_settings_defaults_are_the_safe_end():
    # A missing or partial config must yield a dry run against a paper account, never a
    # live one. Defaults are a safety feature here, not a convenience.
    s = live_settings()
    assert s["enabled"] is False
    assert s["require_paper"] is True
    assert str(s["account"]).upper().startswith("DU")


# ---------------------------------------------------------------------------
# ORDER CONSTRUCTION
# ---------------------------------------------------------------------------
def test_orders_move_weights_toward_target():
    # 70/30 against a 60/40 target: sell the overweight leg, buy the underweight one.
    # max_order_value is raised here because this test is about DIRECTION and sizing; the
    # ceiling gets its own test below, and leaving it at the default would silently turn
    # this into a test of the reject path instead.
    net_liq = 1_000_000.0
    df = portfolio_frame([("SPY", 1000.0, 700.0, 600.0), ("KMLM", 10_000.0, 30.0, 25.0)])
    book = book_from_portfolio(df, base_equity=net_liq)
    marks = {"SPY": 700.0, "KMLM": 30.0}

    orders = build_orders(book, marks, {"SPY": 0.6, "KMLM": 0.4}, net_liq,
                          settings(max_order_value=500_000))
    o = orders.set_index("symbol")
    assert o.loc["SPY", "action"] == "SELL"
    assert o.loc["KMLM", "action"] == "BUY"
    # Targets are sized off the account's true equity (1m), NOT 1m + unrealised: 60% of 1m
    # at 700 is ~857 shares, and 40% at 30 is ~13,333.
    assert o.loc["SPY", "current_qty"] + o.loc["SPY", "units"] == pytest.approx(857, abs=1)
    assert o.loc["KMLM", "current_qty"] + o.loc["KMLM", "units"] == pytest.approx(13_333, abs=1)


def test_min_turnover_skips_legs_already_on_target():
    # Already at 60/40 — trading here would pay the spread to correct nothing.
    net_liq = 1_000_000.0
    df = portfolio_frame([("SPY", 857.0, 700.0, 600.0), ("KMLM", 13_333.0, 30.0, 25.0)])
    book = book_from_portfolio(df, base_equity=net_liq)
    orders = build_orders(book, {"SPY": 700.0, "KMLM": 30.0},
                          {"SPY": 0.6, "KMLM": 0.4}, net_liq, settings())
    assert (orders["units"] == 0).all()
    assert orders["status"].str.contains("min_turnover").any()


def test_max_order_value_rejects_rather_than_clips():
    # An order this large means something upstream is wrong. Trading a smaller amount would
    # hide the fault while still acting on it, so the leg is refused outright.
    net_liq = 10_000_000.0
    df = portfolio_frame([("SPY", 0.0, 700.0, 0.0), ("KMLM", 0.0, 30.0, 0.0)])
    book = book_from_portfolio(df, base_equity=net_liq)
    book.position("SPY").restore(0.0, 0.0, 0.0)
    book.position("KMLM").restore(0.0, 0.0, 0.0)

    orders = build_orders(book, {"SPY": 700.0, "KMLM": 30.0},
                          {"SPY": 0.6, "KMLM": 0.4}, net_liq,
                          settings(max_order_value=1_000))
    assert (orders["units"] == 0).all()
    assert orders["status"].str.startswith("REJECT").any()


def test_missing_mark_is_skipped_not_guessed():
    # Filling against an invented price is how a rebalancer turns a data outage into a loss.
    net_liq = 1_000_000.0
    df = portfolio_frame([("SPY", 1000.0, 700.0, 600.0)])
    book = book_from_portfolio(df, base_equity=net_liq)
    orders = build_orders(book, {"SPY": 700.0}, {"SPY": 0.6, "KMLM": 0.4},
                          net_liq, settings()).set_index("symbol")
    assert orders.loc["KMLM", "status"] == "SKIP no mark"


def test_does_not_import_the_cell_script():
    """REGRESSION — the worst bug found in this phase.

    `rebalance_live.py` originally did `from rebalance_port_basic import
    submit_rebalance_orders`. That file is a CELL SCRIPT: its module-level code opens an
    IBKR connection and its bottom cells run a live `dry_run=False` rebalance plus a stray
    market order. Importing it would therefore have TRANSMITTED ORDERS as a side effect of
    the import — before a single guard in this pipeline ran, and even on a dry run.

    The submitter now lives in the library. This test asserts the unsafe import is gone by
    reading the source, because actually importing the cell script to check would trigger
    the very behaviour being guarded against.
    """
    src = (REPO / "src" / "pipelines" / "rebalance_live.py").read_text(encoding="utf-8")
    code_lines = [ln for ln in src.splitlines()
                  if not ln.strip().startswith("#")]
    for line in code_lines:
        assert "import" not in line or "rebalance_port_basic" not in line, \
            f"unsafe import of the cell script reintroduced: {line.strip()}"


# ---------------------------------------------------------------------------
# MIXED CURRENCIES — the London lines
# ---------------------------------------------------------------------------
# Rates as IBKR's $LEDGER:ALL reports them: multiplier INTO the base currency.
BASE_CCY = "GBP"
FX = {"GBP": 1.0, "USD": 0.75, "EUR": 0.86}


def fx_frame():
    """A GBP-base account holding a GBP line, a USD line and a EUR line.

    Modelled on the real DUP102412 rows, where updatePortfolio returns every
    figure in the POSITION'S OWN currency and converts nothing: BARC in pounds
    on LSE, LNG in dollars on NYSE, 5MVL in euros on IBIS2. NetLiquidation is
    the only base-currency number in the whole pull.
    """
    return pd.DataFrame([
        {"account": "DUP102412", "symbol": "BARC", "conId": 908940, "secType": "STK",
         "exchange": "LSE", "currency": "GBP", "position": 200.0, "marketPrice": 5.2495,
         "marketValue": 1_049.90, "averageCost": 4.26, "unrealizedPnL": 197.9,
         "realizedPnL": 0.0},
        {"account": "DUP102412", "symbol": "LNG", "conId": 4728003, "secType": "STK",
         "exchange": "NYSE", "currency": "USD", "position": 100.0, "marketPrice": 270.80,
         "marketValue": 27_080.0, "averageCost": 265.44, "unrealizedPnL": 536.0,
         "realizedPnL": 0.0},
        {"account": "DUP102412", "symbol": "5MVL", "conId": 395578220, "secType": "STK",
         "exchange": "IBIS2", "currency": "EUR", "position": 99.0, "marketPrice": 82.39,
         "marketValue": 8_156.61, "averageCost": 69.6405, "unrealizedPnL": 1_262.2,
         "realizedPnL": 0.0},
    ])


def priced_frame():
    """`fx_frame` with marketPrice AND averageCost converted into base currency.

    This is what main() hands to book_from_portfolio. Not optional: that function
    back-solves the book's base equity from those two columns, so an unconverted
    row makes the book a sum across three currencies — and ConstantMixRule sizes
    every leg off that equity.
    """
    df = fx_frame()
    rate = df["currency"].str.upper().map(FX)
    df["marketPrice"] = df["marketPrice"] * rate
    df["averageCost"] = df["averageCost"] * rate
    return df


def test_marks_are_converted_into_the_base_currency():
    # THE currency test. Each leg's quoted price times ITS OWN rate — never a blanket 1.0,
    # which is what the account really returns and what made the EUR leg read ~23% high.
    marks, detail = base_currency_marks(fx_frame(), fx_rates=FX, base_ccy=BASE_CCY)
    assert marks["BARC"] == pytest.approx(5.2495)             # already base
    assert marks["LNG"] == pytest.approx(270.80 * 0.75)       # 203.10 GBP
    assert marks["5MVL"] == pytest.approx(82.39 * 0.86)       # 70.86 GBP
    # The applied rate is reported so the conversion is visible in the printed table.
    assert detail.loc["5MVL", "fx_rate"] == pytest.approx(0.86)
    # fx_ratio 1.0 is correct ONLY for the base-currency leg.
    assert detail.loc["BARC", "fx_ratio"] == pytest.approx(1.0)
    assert detail.loc["LNG", "fx_ratio"] == pytest.approx(1 / 0.75)


def test_missing_rate_is_not_treated_as_one_to_one():
    """The bug class this function exists to kill.

    A currency with no rate must leave the symbol UNPRICED. Defaulting to 1.0 would
    value 8,156 euros as 8,156 pounds and size an order off it.
    """
    marks, detail = base_currency_marks(fx_frame(), fx_rates={"GBP": 1.0, "USD": 0.75},
                                        base_ccy=BASE_CCY)
    assert "5MVL" not in marks
    assert pd.isna(detail.loc["5MVL", "base_price"])
    assert "BARC" in marks and "LNG" in marks     # the others are unaffected


def test_no_rates_at_all_still_prices_the_base_currency_legs():
    # If $LEDGER comes back empty the base-currency legs are still knowable.
    marks, detail = base_currency_marks(fx_frame(), fx_rates={}, base_ccy=BASE_CCY)
    assert set(marks) == {"BARC"}
    # Contract identity is carried through regardless, so an unpriced leg can still be
    # identified in the table rather than appearing as a bare ticker.
    assert detail.loc["BARC", "conId"] == 908940
    assert detail.loc["BARC", "exchange"] == "LSE"
    assert detail.loc["5MVL", "conId"] == 395578220


def test_book_equity_is_wrong_without_the_currency_conversion():
    """REGRESSION — the subtlest bug in this phase.

    Feeding the RAW frame to book_from_portfolio builds a book whose unrealised is a sum
    across GBP, USD and EUR. ConstantMixRule sizes every leg off book.equity(prices), so
    the two unconverted rows corrupt the targets for the WHOLE book — including BARC,
    which was in the base currency and perfectly fine on its own.
    """
    net_liq = 100_000.0
    marks, _ = base_currency_marks(fx_frame(), fx_rates=FX, base_ccy=BASE_CCY)

    raw_book = book_from_portfolio(fx_frame(), base_equity=net_liq)
    # Evaluated on the correct (base) marks, the raw book does NOT come back to NetLiq.
    assert abs(raw_book.equity(marks) - net_liq) > 1_000

    fixed_book = book_from_portfolio(priced_frame(), base_equity=net_liq)
    assert fixed_book.equity(marks) == pytest.approx(net_liq)


def test_weights_use_converted_prices():
    # Unconverted, the 100 LNG shares look like 27,080 of a 100,000 GBP account (27.1%).
    # Converted at 0.75 they are 20,310 (20.3%) — the difference between selling into a
    # target and buying up to it.
    net_liq = 100_000.0
    book = book_from_portfolio(priced_frame(), base_equity=net_liq)
    marks, detail = base_currency_marks(fx_frame(), fx_rates=FX, base_ccy=BASE_CCY)
    orders = build_orders(book, marks, {"LNG": 0.25, "BARC": 0.10}, net_liq,
                          settings(max_order_value=500_000), detail=detail).set_index("symbol")
    assert orders.loc["LNG", "current_weight"] == pytest.approx(0.20310)
    assert orders.loc["LNG", "action"] == "BUY"       # 20.3% held vs 25% target
    assert orders.loc["BARC", "action"] == "BUY"      # 1.05% held vs 10% target
    # The unconverted quote is still reported, so the reader can see WHY they differ.
    assert orders.loc["LNG", "quote_price"] == pytest.approx(270.80)
    assert orders.loc["LNG", "base_price"] == pytest.approx(203.10)


def test_holding_outside_the_target_is_closed_not_ignored():
    # Iterating only the target weights meant a position we no longer want could never be
    # sold. target_weight 0 is an instruction, not an absence of one.
    net_liq = 100_000.0
    book = book_from_portfolio(priced_frame(), base_equity=net_liq)
    marks, detail = base_currency_marks(fx_frame(), fx_rates=FX, base_ccy=BASE_CCY)
    orders = build_orders(book, marks, {"LNG": 0.25}, net_liq,
                          settings(max_order_value=500_000), detail=detail).set_index("symbol")
    assert "BARC" in orders.index
    assert orders.loc["BARC", "units"] == -200        # the whole position
    assert orders.loc["BARC", "action"] == "SELL"
    assert orders.loc["BARC", "status"] == "CLOSE not in target"


def test_close_still_respects_the_notional_ceiling():
    # A close is exempt from min_turnover but NOT from max_order_value — the ceiling exists
    # to catch a bad tick, and a bad tick can inflate a closing order just as easily.
    net_liq = 100_000.0
    book = book_from_portfolio(priced_frame(), base_equity=net_liq)
    marks, detail = base_currency_marks(fx_frame(), fx_rates=FX, base_ccy=BASE_CCY)
    # 200 * 5.2495 = 1,049.90 of notional, so a 1,000 ceiling must refuse it.
    orders = build_orders(book, marks, {"LNG": 0.25}, net_liq,
                          settings(max_order_value=1_000), detail=detail).set_index("symbol")
    assert orders.loc["BARC", "units"] == 0
    assert orders.loc["BARC", "status"].startswith("REJECT")


# ---------------------------------------------------------------------------
# THE PENDING-ORDER GUARD — do not trade a symbol that is mid-flight
# ---------------------------------------------------------------------------
def working_orders_frame(rows):
    return pd.DataFrame([{"orderId": oid, "symbol": s, "action": a, "totalQuantity": q,
                          "status": "PreSubmitted"} for oid, s, a, q in rows])


def test_pending_symbols_summarises_working_orders():
    from pipelines.rebalance_live import pending_symbols

    p = pending_symbols(working_orders_frame([(10, "BARC", "BUY", 3691),
                                              (12, "LNG", "BUY", 97)]))
    assert set(p) == {"BARC", "LNG"}
    assert "orderId 10" in p["BARC"] and "3691" in p["BARC"]


def test_pending_symbols_keeps_every_order_on_one_symbol():
    # "There is already an order" understates "there are already three".
    from pipelines.rebalance_live import pending_symbols

    p = pending_symbols(working_orders_frame([(1, "BARC", "BUY", 100),
                                              (2, "BARC", "BUY", 200)]))
    assert "orderId 1" in p["BARC"] and "orderId 2" in p["BARC"]


def test_pending_symbols_handles_an_empty_book():
    from pipelines.rebalance_live import pending_symbols
    assert pending_symbols(pd.DataFrame()) == {}
    assert pending_symbols(None) == {}


def test_a_symbol_with_a_working_order_is_not_traded_again():
    """THE double-up guard.

    build_orders sizes from the POSITION, and a working order is not a position yet. A
    second run before the first fills sees the same gap, proposes the same trade, and
    doubles the exposure — silently, because each order is individually correct.
    """
    net_liq = 100_000.0
    book = book_from_portfolio(priced_frame(), base_equity=net_liq)
    marks, detail = base_currency_marks(fx_frame(), fx_rates=FX, base_ccy=BASE_CCY)

    pending = {"BARC": "orderId 10 BUY 3691 [PreSubmitted]"}
    orders = build_orders(book, marks, {"BARC": 0.10, "LNG": 0.25}, net_liq,
                          settings(max_order_value=500_000), detail=detail,
                          pending=pending).set_index("symbol")

    assert orders.loc["BARC", "units"] == 0
    assert "already working" in orders.loc["BARC", "status"]
    assert "orderId 10" in orders.loc["BARC", "status"]
    # Everything else still trades — the guard is per symbol, not a global stop.
    assert orders.loc["LNG", "units"] != 0


def test_the_guard_beats_every_other_sizing_rule():
    # Checked before close/min_turnover/ceiling: a mid-flight position cannot be sized
    # against at all, so no other rule's verdict on it is meaningful.
    net_liq = 100_000.0
    book = book_from_portfolio(priced_frame(), base_equity=net_liq)
    marks, detail = base_currency_marks(fx_frame(), fx_rates=FX, base_ccy=BASE_CCY)

    # BARC is not in the target, so without the guard this would be a CLOSE.
    orders = build_orders(book, marks, {"LNG": 0.25}, net_liq,
                          settings(max_order_value=500_000), detail=detail,
                          pending={"BARC": "orderId 10 BUY 3691 [PreSubmitted]"}
                          ).set_index("symbol")
    assert orders.loc["BARC", "units"] == 0
    assert "already working" in orders.loc["BARC", "status"]


def test_no_pending_means_business_as_usual():
    net_liq = 100_000.0
    book = book_from_portfolio(priced_frame(), base_equity=net_liq)
    marks, detail = base_currency_marks(fx_frame(), fx_rates=FX, base_ccy=BASE_CCY)
    orders = build_orders(book, marks, {"BARC": 0.10}, net_liq,
                          settings(max_order_value=500_000), detail=detail,
                          pending={}).set_index("symbol")
    assert orders.loc["BARC", "units"] != 0


def test_cspx_replaces_spy_in_the_live_book():
    """SPY is refused to this client (PRIIPs/KID); CSPX is the UCITS equivalent."""
    from portutils.utils import config as cfg

    live = cfg.portfolio_weights("live_book")
    assert "CSPX" in live and "SPY" not in live
    assert cfg.asset_role("CSPX") == "beta"


def test_cspx_has_a_contract_override():
    """A name we do not hold has no conId, so the bare ticker would go out as
    STK/SMART/USD — which describes a US listing, not a London one."""
    s = live_settings()
    cspx = (s.get("contract_overrides") or {}).get("CSPX")
    assert cspx, "CSPX needs a contract override; it is not held, so there is no conId"
    assert cspx["currency"] == "USD"
    assert cspx["exchange"] == "SMART", "direct routing triggers the 10311 hold"
    assert cspx["primary_exchange"] == "LSEETF"


def test_min_turnover_must_be_smaller_than_the_live_target_weights():
    """The gate that silently blocks everything if it is wider than a target.

    min_turnover is compared against the WEIGHT GAP, so a 2% gate against live_book's
    1.43% targets skips every leg as 'already close enough' — from flat, with nothing
    held. This pins the config pair, not the code.
    """
    from portutils.utils import config as cfg
    weights = cfg.portfolio_weights("live_book")
    gate = live_settings()["min_turnover"]
    assert gate < min(weights.values()), (
        f"min_turnover {gate} >= smallest target weight {min(weights.values())}: "
        f"every leg would skip and the rebalancer could never trade")


def test_live_book_orders_fit_under_the_notional_ceiling():
    """The sizing that made the first real dry run reject all seven legs.

    On the ~1.02M GBP account, 60% over seven names was ~87.5k per order against a 50k
    ceiling. This asserts the configured weights and ceiling are mutually consistent at
    that account size, so the pair cannot silently drift back into all-reject.
    """
    from portutils.utils import config as cfg
    weights = cfg.portfolio_weights("live_book")
    s = live_settings()
    per_leg = 1_021_135.0 * max(weights.values())
    assert per_leg < s["max_order_value"], (
        f"a full leg is {per_leg:,.0f} against max_order_value {s['max_order_value']:,.0f}")


def test_contract_specs_reach_the_submitter():
    """The order must name the conId, not re-resolve the bare ticker.

    Uses a stub OrderApp so nothing connects: the assertion is purely about what
    submit_rebalance_orders passes down.
    """
    from portutils.ingestion.ibkr_requests import (contract_specs_from_portfolio,
                                                   submit_rebalance_orders)

    calls = []

    class StubOrderApp:
        def submit_market_order(self, symbol, action, quantity, **kw):
            calls.append({"symbol": symbol, "action": action, "quantity": quantity, **kw})
            return len(calls)

    specs = contract_specs_from_portfolio(fx_frame())
    table = pd.DataFrame([{"symbol": "BARC", "units": -200},
                          {"symbol": "5MVL", "units": 10}])
    out = submit_rebalance_orders(StubOrderApp(), table, units_col="units",
                                  dry_run=False, contract_specs=specs).set_index("symbol")

    assert len(calls) == 2
    barc = next(c for c in calls if c["symbol"] == "BARC")
    assert barc["con_id"] == 908940
    assert barc["currency"] == "GBP"          # NOT the old hardcoded USD
    # SMART routing with the venue in primary_exchange — direct routing to "LSE" is what
    # triggered warning 10311 and left the order held for manual Transmit.
    assert barc["exchange"] == "SMART"
    assert barc["primary_exchange"] == "LSE"
    assert barc["action"] == "SELL" and barc["quantity"] == 200
    # The EUR line on Xetra is the case the hardcoded USD would have got most wrong.
    eur = next(c for c in calls if c["symbol"] == "5MVL")
    assert eur["currency"] == "EUR" and eur["primary_exchange"] == "IBIS2"
    assert out.loc["BARC", "currency"] == "GBP" and out.loc["BARC", "con_id"] == 908940


def test_submitter_without_specs_keeps_old_behaviour():
    # Existing callers pass no contract_specs; they must be unaffected.
    from portutils.ingestion.ibkr_requests import submit_rebalance_orders

    calls = []

    class StubOrderApp:
        def submit_market_order(self, symbol, action, quantity, **kw):
            calls.append({"symbol": symbol, **kw})
            return 1

    table = pd.DataFrame([{"symbol": "SPY", "currency": "USD", "units": 10}])
    submit_rebalance_orders(StubOrderApp(), table, units_col="units", dry_run=False)
    assert calls[0]["currency"] == "USD"
    assert "con_id" not in calls[0]     # nothing invented


# ---------------------------------------------------------------------------
# ORDER ACKNOWLEDGEMENT — the bug that made seven orders vanish
# ---------------------------------------------------------------------------
class StubApp:
    """Minimal stand-in for IBApp's post-submit state. No socket, no threads."""

    def __init__(self, order_status=None, open_orders=None, req_errors=None,
                 req_notices=None):
        import threading
        self.lock = threading.RLock()
        self.order_status = order_status or {}
        self.open_orders = open_orders or {}
        self.req_errors = req_errors or {}
        self.req_notices = req_notices or {}


def test_unacknowledged_orders_are_reported_not_assumed_successful():
    """THE regression. placeOrder returning is not acceptance.

    An order TWS never mentioned must come back acknowledged=False, so the caller can
    say "this probably did not arrive" instead of printing Submitted and hanging up.
    """
    from portutils.ingestion.ibkr_requests import wait_for_order_ack

    app = StubApp(order_status={1: {"orderId": 1, "status": "PreSubmitted", "filled": 0}},
                  open_orders={1: {"symbol": "BARC", "action": "BUY", "totalQuantity": 200}})
    ack = wait_for_order_ack(app, [1, 2, 3], timeout=0.2, settle=0.0,
                             poll=0.05).set_index("orderId")

    assert ack.loc[1, "acknowledged"] is True or bool(ack.loc[1, "acknowledged"])
    assert not ack.loc[2, "acknowledged"]
    assert not ack.loc[3, "acknowledged"]
    assert ack.loc[1, "status"] == "PreSubmitted"
    assert ack.loc[1, "symbol"] == "BARC"


def test_all_acknowledged_when_tws_answered():
    from portutils.ingestion.ibkr_requests import wait_for_order_ack

    app = StubApp(order_status={i: {"orderId": i, "status": "Filled", "filled": 10}
                                for i in (1, 2)})
    ack = wait_for_order_ack(app, [1, 2], timeout=0.2, settle=0.0, poll=0.05)
    assert ack["acknowledged"].all()
    assert set(ack["status"]) == {"Filled"}


def test_rejection_surfaces_through_req_errors():
    """IB reports an order REJECTION via error(), with the order id in the reqId slot.

    Without reading req_errors a rejected order looks identical to an unacknowledged
    one, and those call for opposite responses: one arrived and was refused, the other
    never arrived at all.
    """
    from portutils.ingestion.ibkr_requests import wait_for_order_ack

    app = StubApp(req_errors={5: (201, "Order rejected - reason: insufficient margin")})
    ack = wait_for_order_ack(app, [5], timeout=0.2, settle=0.0, poll=0.05).set_index("orderId")
    assert bool(ack.loc[5, "acknowledged"])          # TWS DID answer — with a refusal
    assert "201" in ack.loc[5, "error"]


def test_wait_for_order_ack_handles_no_orders():
    from portutils.ingestion.ibkr_requests import wait_for_order_ack
    assert wait_for_order_ack(StubApp(), [], timeout=0.1, settle=0.0).empty


# ---------------------------------------------------------------------------
# ORDER FIELDS TWS REQUIRES — the empty-TIF rejection
# ---------------------------------------------------------------------------
def test_market_order_sets_time_in_force():
    """ibapi leaves Order().tif empty and TWS refuses it: 'Invalid time in force:Empty'.

    Every order in the batch was rejected for this, with nothing reaching the Orders
    panel. DAY is correct for a rebalance — an unfilled remainder should expire with
    the marks that sized it, not fill against tomorrow's prices.
    """
    from ibapi.order import Order
    from portutils.ingestion.ibkr_requests import market_order

    assert Order().tif == "", "if ibapi starts defaulting tif, revisit this comment"
    o = market_order("BUY", 100)
    assert o.tif == "DAY"
    assert o.orderType == "MKT" and o.action == "BUY" and o.totalQuantity == 100


def test_limit_order_inherits_the_time_in_force():
    from portutils.ingestion.ibkr_requests import limit_order
    o = limit_order("SELL", 50, 12.34)
    assert o.tif == "DAY"
    assert o.orderType == "LMT" and o.lmtPrice == 12.34


def test_crypto_order_keeps_its_own_ioc():
    # The one path that always worked, because it set tif explicitly. Must not be
    # clobbered by the DAY default.
    from portutils.ingestion.ibkr_requests import crypto_marketable_limit_order
    o = crypto_marketable_limit_order("BUY", 0.01, 60_000.0)
    assert o.tif == "IOC" and o.outsideRth is True


# ---------------------------------------------------------------------------
# THE error() CALLBACK — errorTime was being read as the error code
# ---------------------------------------------------------------------------
def make_app():
    """A bare IBApp with no connection — only its callback state is exercised."""
    from portutils.ingestion.ibkr_requests import IBApp
    return IBApp()


def test_error_reads_the_code_not_the_timestamp():
    """ibapi 10.47: error(reqId, errorTime, errorCode, errorString).

    Taking 'the first int' read the epoch-ms timestamp as the code, so every
    code-based decision silently stopped working.
    """
    app = make_app()
    # The real shape of the rejection seen in the live run.
    app.error(3, 1785091519186, 201, "Order rejected - reason: test")
    assert app.req_errors[3][0] == 201
    assert "Order rejected" in app.req_errors[3][1]


def test_error_still_suppresses_farm_status_pings():
    """2104/2106/2158/2176 are routine. They were being PRINTED as errors because the
    code never matched — visible as `IB Error -1: 1785082784783 - ... is OK`."""
    app = make_app()
    app.error(-1, 1785082784783, 2104, "Market data farm connection is OK:usfarm")
    assert -1 not in app.req_errors          # swallowed, not recorded


def test_error_does_not_treat_advisory_codes_as_terminal():
    import threading
    app = make_app()
    ev = threading.Event()
    app._hist_events[9] = ev
    # 2108 is advisory — the request is still alive, so the waiter must NOT be released.
    app.error(9, 1785082784783, 2108, "Market data farm connection is inactive")
    assert not ev.is_set()


def test_error_releases_the_waiter_on_a_terminal_code():
    """The fail-fast that never fired against real TWS: 'No security definition' (200)
    left get_equity_data blocked for the full 30s timeout instead of skipping."""
    import threading
    app = make_app()
    ev = threading.Event()
    app._hist_events[4] = ev
    app.error(4, 1785082784783, 200, "No security definition has been found")
    assert ev.is_set()
    assert app.req_errors[4][0] == 200


def test_advisory_codes_are_recorded_as_notices_not_discarded():
    """Warning 10311 means TWS accepted the order and is HOLDING it for manual Transmit.

    It is not terminal, so it must not release a waiter — but discarding it made five
    orders sitting visibly in the TWS Pending panel report as 'never received'.
    """
    app = make_app()
    app.error(1, 1785091519186, 10311,
              "This order will be directly routed to LSE. Direct routed orders may "
              "result in higher trade fees.")
    assert 1 not in app.req_errors           # not terminal
    assert app.req_notices[1][0] == 10311    # but kept


def test_order_message_399_is_a_warning_not_a_rejection():
    """399 is IB's 'Order Message' — attached to an ACCEPTED order.

    Five PreSubmitted orders queued for Monday's open were printed as REJECTED because
    399 is below the 2100 advisory band.
    """
    app = make_app()
    app.error(8, 1785091519186, 399,
              "Order Message:\nBUY 191 5MVL IBIS2\nWarning: Your order will not be "
              "placed at the exchange until 2026-07-27 09:00:00 MET.")
    assert 8 not in app.req_errors
    assert app.req_notices[8][0] == 399


def test_a_live_status_overrides_a_recorded_error():
    """The backstop for codes no constant knows about yet.

    IB adds warnings faster than anyone updates a code list, but the order's own status
    is authoritative: PreSubmitted means alive, so the message is a notice, not a refusal.
    """
    from portutils.ingestion.ibkr_requests import wait_for_order_ack

    app = StubApp(order_status={8: {"orderId": 8, "status": "PreSubmitted", "filled": 0}},
                  req_errors={8: (12345, "some future warning nobody has listed")})
    ack = wait_for_order_ack(app, [8], timeout=0.3, settle=0.0, poll=0.05).set_index("orderId")
    assert ack.loc[8, "error"] is None, "a PreSubmitted order was not rejected"
    assert "12345" in ack.loc[8, "notice"]


def test_a_genuine_rejection_still_reads_as_an_error():
    # The demotion must not swallow a real refusal: Inactive is not a live state.
    from portutils.ingestion.ibkr_requests import wait_for_order_ack

    app = StubApp(order_status={5: {"orderId": 5, "status": "Inactive"}},
                  req_errors={5: (201, "Order rejected - reason:No Trading Permission")})
    ack = wait_for_order_ack(app, [5], timeout=0.3, settle=0.0, poll=0.05).set_index("orderId")
    assert "201" in ack.loc[5, "error"]
    assert ack.loc[5, "notice"] is None


def test_a_notice_counts_as_acknowledgement():
    from portutils.ingestion.ibkr_requests import wait_for_order_ack

    app = make_app()
    app.error(3, 1785091519186, 10311, "This order will be directly routed to LSE.")
    ack = wait_for_order_ack(app, [3], timeout=0.3, settle=0.0, poll=0.05).set_index("orderId")
    assert bool(ack.loc[3, "acknowledged"]), "TWS spoke about this order; that is not silence"
    assert "10311" in ack.loc[3, "notice"]
    assert ack.loc[3, "error"] is None       # held, not rejected — different outcomes


# ---------------------------------------------------------------------------
# ROUTING — the direct-route hold
# ---------------------------------------------------------------------------
def test_specs_route_smart_and_put_the_venue_in_primary_exchange():
    """Copying the position's `exchange` into Contract.exchange is DIRECT ROUTING.

    That trips warning 10311 and makes TWS hold the order for manual confirmation.
    The listing venue belongs in primaryExchange, which disambiguates without routing.
    """
    from portutils.ingestion.ibkr_requests import contract, contract_specs_from_portfolio

    specs = contract_specs_from_portfolio(fx_frame())
    for sym in ("BARC", "LNG", "5MVL"):
        assert specs[sym]["exchange"] == "SMART", f"{sym} would be direct-routed"
    assert specs["BARC"]["primary_exchange"] == "LSE"
    assert specs["LNG"]["primary_exchange"] == "NYSE"
    assert specs["5MVL"]["primary_exchange"] == "IBIS2"

    # And the pair composes into a contract that is unambiguous without direct routing.
    c = contract("BARC", **specs["BARC"])
    assert c.exchange == "SMART" and c.primaryExchange == "LSE"
    assert c.conId == 908940 and c.currency == "GBP"


def test_a_smart_listed_position_gets_no_redundant_primary_exchange():
    from portutils.ingestion.ibkr_requests import contract_specs_from_portfolio

    df = fx_frame()
    df.loc[df["symbol"] == "LNG", "exchange"] = "SMART"
    specs = contract_specs_from_portfolio(df)
    assert specs["LNG"]["exchange"] == "SMART"
    assert "primary_exchange" not in specs["LNG"]


def test_live_book_excludes_the_kid_ineligible_us_etfs():
    """SPY and KMLM are rejected by IB for this client (PRIIPs/KID), permanently.

    They must not sit in the live vector generating a guaranteed rejection every run —
    while staying in the catalogue, since the research vectors still use them.
    """
    from portutils.utils import config as cfg

    live = cfg.portfolio_weights("live_book")
    assert "SPY" not in live and "KMLM" not in live
    # Equal weight across the tradeable names, and a total that leaves most of the
    # account in cash. The exact total is a policy dial (0.10 with five names, 0.12 once
    # CSPX joined) — what must not drift is the cash-heavy shape.
    assert len(set(live.values())) == 1, "equal weight across the tradeable names"
    assert 0.05 <= sum(live.values()) <= 0.20, "live book should stay mostly cash"
    # Still available to the studies.
    assert set(cfg.portfolio_weights("spy_kmlm")) == {"SPY", "KMLM"}


def test_error_tolerates_the_legacy_signature():
    # Older builds: error(reqId, errorCode, errorString) with no timestamp.
    app = make_app()
    app.error(2, 200, "No security definition has been found")
    assert app.req_errors[2][0] == 200


# ---------------------------------------------------------------------------
# THE CANCEL PATH — absent from this repo until now
# ---------------------------------------------------------------------------
def test_cancel_order_builds_the_shape_this_ibapi_wants():
    """ibapi 10.47 needs cancelOrder(orderId, OrderCancel()); older builds do not.

    The shim picks by inspecting the real signature, so this asserts against whatever
    ibapi is actually installed rather than against a version string.
    """
    import inspect
    from ibapi.client import EClient
    from portutils.ingestion.ibkr_requests import cancel_order

    calls = []

    class StubCancelApp:
        def cancelOrder(self, orderId, *args):
            calls.append((orderId, args))

    # Match the installed EClient's signature so the shim is exercised for real.
    StubCancelApp.cancelOrder.__signature__ = inspect.signature(EClient.cancelOrder)

    cancel_order(StubCancelApp(), 7)
    assert calls[0][0] == 7
    params = list(inspect.signature(EClient.cancelOrder).parameters)
    if "orderCancel" in params:
        from ibapi.order_cancel import OrderCancel
        assert len(calls[0][1]) == 1 and isinstance(calls[0][1][0], OrderCancel)


def test_cancel_all_orders_calls_global_cancel():
    import inspect
    from ibapi.client import EClient
    from portutils.ingestion.ibkr_requests import cancel_all_orders

    called = []

    class StubCancelApp:
        def reqGlobalCancel(self, *args):
            called.append(args)

    StubCancelApp.reqGlobalCancel.__signature__ = inspect.signature(EClient.reqGlobalCancel)
    cancel_all_orders(StubCancelApp())
    assert len(called) == 1


# ---------------------------------------------------------------------------
# MARKET HOURS — the Sunday that ate the first live run
# ---------------------------------------------------------------------------
def test_weekend_is_flagged_as_closed():
    import datetime as dt
    from pipelines.rebalance_live import market_hours_note

    # 2026-07-26 is the Sunday the first armed run actually went out on.
    looks_open, msg = market_hours_note(dt.datetime(2026, 7, 26, 19, 25))
    assert looks_open is False
    assert "WEEKEND" in msg


def test_midweek_afternoon_is_open():
    import datetime as dt
    from pipelines.rebalance_live import market_hours_note

    looks_open, _ = market_hours_note(dt.datetime(2026, 7, 27, 15, 0))
    assert looks_open is True


def test_overnight_is_flagged_as_closed():
    import datetime as dt
    from pipelines.rebalance_live import market_hours_note

    looks_open, msg = market_hours_note(dt.datetime(2026, 7, 27, 6, 0))
    assert looks_open is False
    assert "OUTSIDE" in msg


# ---------------------------------------------------------------------------
# THE DEBUG CELL SCRIPT — must be as un-importable as rebalance_port_basic.py
# ---------------------------------------------------------------------------
def test_debug_cell_script_is_never_imported_by_src():
    """orders/rebalance_live_debug.py connects to TWS at module level.

    Same hazard as rebalance_port_basic.py: importing it would run its cells. Checked by
    reading the source of everything under src/, never by importing anything.
    """
    for path in (REPO / "src").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for line in src.splitlines():
            if line.strip().startswith("#"):
                continue
            assert not ("import" in line and "rebalance_live_debug" in line), \
                f"{path.name} imports the debug cell script: {line.strip()}"


def test_debug_cell_script_is_disarmed_and_gated():
    """The live cell must refuse to run unless deliberately armed.

    Pins two things: the committed state is disarmed, and the live submit is guarded by
    an assert so 'Run All' stops rather than trading. This is exactly what
    rebalance_port_basic.py lacks — its module level ends with a live dry_run=False call.
    """
    path = REPO / "orders" / "rebalance_live_debug.py"
    src = path.read_text(encoding="utf-8")

    assert "ARM_LIVE = False" in src, "the committed file must be disarmed"
    assert "assert ARM_LIVE" in src, "the live cell must be gated by an assert"

    # Every live submit must come after the gate. Comments are skipped — the file's
    # header explains the hazard in prose and mentions `dry_run=False` while doing so,
    # which is documentation, not a call.
    lines = src.splitlines()
    gate_line = next(i for i, ln in enumerate(lines) if ln.strip().startswith("assert ARM_LIVE"))
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        assert "dry_run=False" not in line or i > gate_line, \
            f"a dry_run=False submit appears before the ARM_LIVE gate (line {i + 1})"


def test_units_are_whole_shares_and_truncated():
    # Truncation rather than rounding, so a rebalance never overshoots its target.
    net_liq = 1_000_000.0
    df = portfolio_frame([("SPY", 100.0, 640.37, 600.0), ("KMLM", 100.0, 27.13, 25.0)])
    book = book_from_portfolio(df, base_equity=net_liq)
    orders = build_orders(book, {"SPY": 640.37, "KMLM": 27.13},
                          {"SPY": 0.6, "KMLM": 0.4}, net_liq, settings())
    for _, row in orders.iterrows():
        assert float(row["units"]).is_integer()
        assert abs(row["units"]) <= abs(row["raw_delta_units"])
