"""IBKR data request helpers built around a single reusable IBApp instance.

This module owns the WHOLE IBKR stack: the connection lifecycle at the top,
then the single ``IBApp`` class, then the concrete request workflows.

Why one combined object (EClient + EWrapper)
--------------------------------------------
The IB Python API (ibapi) uses a dual-class design:

  • EClient  — the *outgoing* side: methods you call to send requests
               (reqHistoricalData, reqAccountSummary, etc.).
  • EWrapper — the *incoming* side: callbacks IBKR fires when data arrives
               (historicalData, accountSummary, error, etc.).

You must combine both into a single object so that (a) you can *send* requests
and (b) the same object *receives* the asynchronous responses.  ``IBApp``
inherits from both and wires them together in ``__init__`` via
``EClient.__init__(self, self)`` — passing ``self`` as both the client and the
wrapper.  ``connect_ib`` / ``disconnect_ib`` (module-level helpers below) drive
the connection lifecycle so the request workflows stay decoupled from it.

Design intent
-------------
There are two separate concerns in this module:

1. ``IBApp`` is the one long-lived object that connects to TWS / Gateway,
   runs the background event loop, and stores the asynchronous callback state.
2. Separate functions / helper classes then issue concrete requests against an
   *instance* of ``IBApp`` by calling ``app.reqHistoricalData()``,
   ``app.reqAccountSummary()``, ``app.placeOrder()``, etc.

This separation keeps the connection plumbing independent from the actual
request workflows.  That was the original intention behind keeping
``get_equity_data()`` separate, and the structure below preserves that.
"""

from __future__ import annotations

import itertools
import math
import pathlib
import threading
import time
from datetime import datetime, timedelta  # timedelta builds the executions lookback window
from threading import Thread  # connect_ib spins app.run() on a daemon reader thread

import pandas as pd
from ibapi.client import EClient    # outgoing request side of the IB API
from ibapi.wrapper import EWrapper  # incoming callback side of the IB API
from ibapi.contract import Contract
from ibapi.execution import ExecutionFilter  # narrows reqExecutions by time / symbol / side
from ibapi.order import Order


# ═══════════════════════════════════════════════════════════════════════════
# Connection lifecycle (host / port / handshake)
# ═══════════════════════════════════════════════════════════════════════════

def connect_ib(app, host='127.0.0.1', port=7497, client_id=123):
    """Open the IBKR socket on a daemon thread and block until the handshake
    completes.

    Why a background thread?
    ------------------------
    app.run() enters an infinite read-loop that processes incoming IBKR
    messages.  If we ran it on the main thread it would block forever, so we
    spin it off on a daemon thread.  T

    A deamon thread is just a thread that automatically dies when the main process
    exits, so we don't have to worry about cleaning it up manually.

    he main thread is then free to call
    request methods (reqHistoricalData, etc.) while the daemon thread routes
    the asynchronous responses to our EWrapper callbacks.

    Raises ``ConnectionError`` if TWS/Gateway doesn't respond within 10 s.
    """
    def _run():
        try:
            app.connect(host, port, client_id)  # open TCP socket
            app.run()  # blocks forever, dispatching incoming messages
        except Exception as e:
            print(f"Connection error: {e}")

    # daemon=True so the thread dies automatically when the main process exits
    Thread(target=_run, daemon=True).start()

    # Poll up to 10 s (100 × 0.1 s) for nextValidId() + valid server version.
    # We check serverVersion() as an extra guard that the handshake finished.
    for _ in range(100):
        if app.connected:
            try:
                if app.serverVersion() is not None and app.serverVersion() > 0:
                    break
            except Exception:
                pass
        time.sleep(0.1)

    if not app.connected:
        raise ConnectionError(
            f"Failed to connect to IBKR at {host}:{port} — is TWS/Gateway running?"
        )

    print(f"Connected to IBKR (Server Version: {app.serverVersion()})")


def disconnect_ib(app):
    """Close the TCP socket and stop the background event loop.

    Once disconnect() is called the daemon thread's app.run() returns,
    and because it's a daemon thread it is cleaned up automatically.
    """
    try:
        app.disconnect()
        print("Disconnected from IBKR")
    except Exception as e:
        print(f"Disconnect error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════

def contract(
    symbol,
    sec_type='STK',  # stock (vs. OPT, FUT, etc.)
    exchange='SMART',  # IBKR's smart-routing engine
    currency='USD',
    primary_exchange=None,
    last_trade_date_or_contract_month=None,
    con_id=None,
):
    """Build a Contract object for the requested instrument."""
    c = Contract()
    c.symbol = symbol
    c.secType = sec_type
    c.exchange = exchange
    c.currency = currency

    # conId is IBKR's own primary key for a specific listing — it pins the exact
    # venue and currency line, which symbol+SMART+USD cannot.  A London-listed
    # ETF and a US ETF can share a ticker; asking TWS to re-resolve the bare
    # symbol gets "No security definition has been found" (error 200), whereas
    # the conId the broker already handed us in the position/portfolio snapshot
    # resolves first time.  Symbol is still set so logs stay readable.
    if con_id:
        c.conId = int(con_id)
    if primary_exchange:
        c.primaryExchange = primary_exchange
    if last_trade_date_or_contract_month:
        c.lastTradeDateOrContractMonth = last_trade_date_or_contract_month

    return c


def contract_specs_from_portfolio(portfolio_df, symbol_col='symbol'):
    """Build per-symbol contract kwargs from an account snapshot.

    # ========================================================================
    # WHY THIS EXISTS — the broker already knows the contract.
    # get_account_updates() / get_positions_data() return conId, secType,
    # exchange and currency for every holding, because updatePortfolio() and
    # position() copy them off the Contract object IBKR sends.  Historical-data
    # helpers then threw all of that away and rebuilt a bare STK/SMART/USD
    # contract from the ticker alone, which fails for every non-US listing in
    # the account.  This maps the snapshot back into kwargs for contract(), so
    # a pull covers exactly the lines that are actually held.
    #
    # Pure: no connection, no I/O.  Feed it a DataFrame, get a dict.
    # ========================================================================

    Parameters
    ----------
    portfolio_df : pd.DataFrame
        Output of get_account_updates() (or get_positions_data()) — needs a
        symbol column plus any of conId / secType / exchange / currency.
    symbol_col : str
        Name of the ticker column.  Default 'symbol'.

    Returns
    -------
    dict[str, dict]
        Mapping symbol -> kwargs suitable for ``contract(sym, **kwargs)``.
        Empty dict when the frame is empty or has no symbol column.
    """
    if portfolio_df is None or len(portfolio_df) == 0 or symbol_col not in portfolio_df.columns:
        return {}

    specs = {}
    for _, row in portfolio_df.iterrows():
        sym = row.get(symbol_col)
        # Guard against blank/NaN tickers — a spec keyed on NaN can never match
        # a requested symbol and would silently do nothing.
        if not sym or (isinstance(sym, float) and pd.isna(sym)):
            continue

        spec = {}
        con_id = row.get('conId')
        if con_id and not pd.isna(con_id):
            spec['con_id'] = int(con_id)
        sec_type = row.get('secType')
        if sec_type and not pd.isna(sec_type):
            spec['sec_type'] = str(sec_type)
        currency = row.get('currency')
        if currency and not pd.isna(currency):
            spec['currency'] = str(currency)
        # ══════════════════════════════════════════════════════════════════════
        # THE ROW'S `exchange` IS THE LISTING VENUE, NOT A ROUTING INSTRUCTION.
        # Copying it into Contract.exchange (IBIS2 / LSEETF / LSE / NYSE) means
        # DIRECT ROUTING, which trips IB's precautionary warning 10311 —
        #   "This order will be directly routed to LSE. Direct routed orders may
        #    result in higher trade fees."
        # — and TWS then HOLDS the order for manual confirmation. Every foreign
        # leg of a live rebalance arrived in the Pending panel with a Transmit
        # button instead of working, which looks identical to a lost order.
        #
        # The listing venue belongs in primaryExchange, whose whole job is to
        # disambiguate a ticker WITHOUT dictating a route. With conId +
        # primaryExchange the contract is already unambiguous, so exchange is free
        # to be SMART and let IB pick the venue — no warning, no hold.
        # ══════════════════════════════════════════════════════════════════════
        spec['exchange'] = 'SMART'
        exchange = row.get('exchange')
        if exchange and not pd.isna(exchange) and str(exchange).upper() != 'SMART':
            spec['primary_exchange'] = str(exchange)

        specs[str(sym)] = spec

    return specs


def market_order(action, quantity):
    """
    Builds a basic market order, in the standard IBKR Order
    object format
    """
    order = Order()
    order.action = action.upper()
    order.orderType = 'MKT'
    order.totalQuantity = quantity
    # TIME IN FORCE IS NOT OPTIONAL. ibapi's Order() leaves `tif` as the empty string,
    # and TWS refuses the order outright with "Invalid time in force:Empty" — every
    # order in a batch, with no partial success and nothing appearing in the Orders
    # panel. DAY is the right default for a rebalance: the decision was made from
    # today's weights and today's marks, so an unfilled remainder should expire with
    # them rather than sit on the book overnight and fill against tomorrow's prices.
    # (crypto_marketable_limit_order sets "IOC" for its own reasons — that path always
    # worked, which is exactly why this gap went unnoticed.)
    order.tif = 'DAY'
    # eTradeOnly / firmQuoteOnly were REMOVED from the Order class in ibapi
    # >=10.19. On older builds they default to True and block routing for retail;
    # on newer builds setting them either raises AttributeError or makes TWS
    # reject with "Error validating request" (321). Guard with hasattr so the
    # same builder runs on both old and new ibapi.
    if hasattr(order, "eTradeOnly"):
        order.eTradeOnly = False
    if hasattr(order, "firmQuoteOnly"):
        order.firmQuoteOnly = False
    return order


def limit_order(action, quantity, limit_price):
    """
    Build a basic limit order:
    This starts with the market_order function defined above,
    because it already initialises the standard Order() object that IBKR
    expects, and already assigns some of the initial parameters, like 
    the action and quantity. We then modify the orderType to be a limit order,
    and set the lmtPrice to the limit_price provided as an input argument
    """
    order = market_order(action, quantity)
    order.orderType = 'LMT'
    order.lmtPrice = float(limit_price)
    return order


def crypto_marketable_limit_order(action, quantity, ref_price, tick=0.50, buf_bps=10):
    """Build a marketable IOC limit order for crypto (PAXOS).

    PAXOS (IB's crypto venue) DOES NOT accept MKT orders. It only supports LMT
    with tif="IOC" (immediate-or-cancel marketable limit). Submitting MKT is the
    most common cause of silent crypto-order rejection (error 201 /
    "Order rejected - reason:").

    Strategy: build a "marketable" limit by crossing the spread by a small buffer
    so the IOC fills against the resting opposite side. If the order doesn't fill
    within the IOC window, TWS cancels it rather than resting it on the book.

    Parameters
    ----------
    action : str       'BUY' or 'SELL'.
    quantity           Order size (Decimal preferred for fractional crypto so it
                       doesn't serialise as e.g. "0.0010000000000001").
    ref_price : float  Reference price to cross from — the caller passes the ask
                       for a BUY and the bid for a SELL (or last price as a
                       fallback). The None-check stays in the caller, which has
                       the UI context to surface an error; this builder assumes a
                       valid ref_price.
    tick : float       Min price increment for the symbol. PAXOS BTC/USD uses a
                       coarse minTick (currently $0.50, sometimes higher at very
                       high BTC prices). $0.50 is a conservative default that works
                       for all current PAXOS crypto symbols; for a more robust impl
                       query reqContractDetails and use details.minTick.
    buf_bps : float    Spread-crossing buffer in basis points. 10 bps (0.1%) is
                       tight enough to avoid bad fills, wide enough to clear normal
                       PAXOS spread (~5-20 bps on BTC/USD).
    """
    order = Order()
    order.action = action.upper()
    order.orderType = 'LMT'
    order.totalQuantity = quantity
    # Cross the spread by buf_bps: BUY slightly above ref, SELL slightly below, so
    # the marketable limit actually crosses and the IOC can fill immediately.
    buf = (1.0 + buf_bps / 10000.0) if order.action == "BUY" else (1.0 - buf_bps / 10000.0)
    raw_px = ref_price * buf
    # Submitting a price off the tick grid triggers Error 110 "price does not
    # conform to the minimum price variation". Snap to the next valid grid point
    # in the SAFE direction: round UP for BUY (so the marketable limit still
    # crosses), round DOWN for SELL.
    if order.action == "BUY":
        snapped = math.ceil(raw_px / tick) * tick
    else:
        snapped = math.floor(raw_px / tick) * tick
    order.lmtPrice = round(snapped, 2)
    order.tif = "IOC"
    # PAXOS quotes 24/7; without this flag, off-hours orders (which is most of the
    # time for crypto) get rejected as "outside RTH".
    order.outsideRth = True
    # Same eTradeOnly / firmQuoteOnly hasattr guard as market_order — see there.
    if hasattr(order, "eTradeOnly"):
        order.eTradeOnly = False
    if hasattr(order, "firmQuoteOnly"):
        order.firmQuoteOnly = False
    return order


def _wait_for(event, timeout, label):
    """Shared polling helper for async IBKR callbacks."""
    # IBKR's API is asynchronous, so we often need to wait for a 
    # callback to arrive before we can proceed.  This helper
    #  function takes a threading.Event object that the relevant 
    # callback will set when it arrives, and then waits for that
    #  event to be set, with a timeout to avoid waiting indefinitely.
    if not event.wait(timeout):
        raise TimeoutError(f'Timed out waiting for IBKR {label}.')


def _coerce_account_values(df):
    """Convert account-summary value strings to numerics where possible."""
    if df.empty or 'value' not in df.columns:
        return df
    df = df.copy()
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(df['value'])
    return df


def connection_status(app):
    """Print and return the connection state of an IBApp.

    Checks both the socket layer (EClient.isConnected) and our handshake flag
    (app.connected) so stale True values from a dropped TWS session are visible.
    Returns True only when both layers agree the connection is live.
    """
    socket_ok = app.isConnected()
    handshake_ok = app.connected
    accounts = ', '.join(app.managed_accounts) if app.managed_accounts else 'unknown'
    print(f"Socket   : {'open' if socket_ok else 'closed'}")
    print(f"Handshake: {'complete' if handshake_ok else 'not complete'}")
    print(f"Account(s): {accounts}")
    return socket_ok and handshake_ok


def reconnect(app, host='127.0.0.1', port=7497, client_id=123):
    """Close an existing IBApp connection and open a fresh one.

    Use this when TWS was restarted and error 326 (client ID already in use)
    appears.  Pass a different client_id if 123 is still held by TWS.
    Returns the same app instance, reconnected.
    """
    try:
        app.close()
    except Exception:
        pass
    app.connected = False
    app.next_order_id = None
    app.start(host=host, port=port, client_id=client_id)
    return app


def _ensure_connected_app(app=None, host='127.0.0.1', port=7497, client_id=123):
    """Return a connected IBApp plus a flag telling us whether we own it."""
    if app is not None:
        return app, False

    app = IBApp()
    app.start(host=host, port=port, client_id=client_id)
    return app, True


DEFAULT_SYMBOLS = ['STX', 'LNG', 'JPM', 'FRES', 'RTX']


# The tags parameter for reqAccountSummary() must be a single comma-separated
# STRING — not a list.  This is explicitly typed as `tags: str` in the ibapi
# source (ibapi/client.py) and documented as:
#   "tags:str - A comma-separated list of account tags."
# Passing a Python list would cause a serialisation error.  We pre-join here
# so the default is ready to use directly.
DEFAULT_TAGS = ','.join([
    'NetLiquidation',        # total account value if liquidated
    'TotalCashValue',        # cash including futures P&L
    'GrossPositionValue',    # sum of abs(position values)
    'BuyingPower',           # max marginable US stock purchase
    'AvailableFunds',        # equity with loan value - initial margin
    'ExcessLiquidity',       # equity with loan value - maintenance margin
    'FullMaintMarginReq',    # maintenance margin across all positions
    'FullInitMarginReq',     # initial margin across all positions
    'RealizedPnL',
    'UnrealizedPnL',
])

_TERMINAL_ORDER_STATES = {
    'Filled',
    'Cancelled',
    'ApiCancelled',
    'Inactive',
}

# Order states meaning "this order is alive at IB". An order in one of these was NOT
# rejected, whatever advisory message arrived alongside it — see wait_for_order_ack,
# where a live status demotes a recorded error to a notice.
_LIVE_ORDER_STATES = {
    'PendingSubmit',
    'PreSubmitted',
    'Submitted',
    'ApiPending',
    'PartiallyFilled',
    'Filled',
}

# Sub-2100 codes that are WARNINGS about an accepted order rather than failures.
# 399 is IB's "Order Message", e.g. "Warning: Your order will not be placed at the
# exchange until 2026-07-27 09:00" for a market order sent while the venue is shut.
# The 2100+ band is advisory by convention; these are the exceptions below it.
_ADVISORY_ORDER_CODES = {399}


# ═══════════════════════════════════════════════════════════════════════════
# Shared IB app class
# ═══════════════════════════════════════════════════════════════════════════

class IBApp(EWrapper, EClient):
    """Single TWS/Gateway app that stores callback state for many request types.

    This class is intentionally *not* the place where we define the higher-level
    data-fetching workflows.  Its job is narrower:

    - connect to TWS / Gateway
    - keep the background event loop alive on a separate thread
    - receive EWrapper callbacks
    - store the latest state so separate functions / helpers can use it
    """

    def __init__(self, on_tick=None):
        # IBApp is BOTH the EClient (outgoing: it SENDS requests) and the
        # EWrapper (incoming: it RECEIVES the asynchronous callbacks).  The IB
        # API requires the client to be constructed with a reference to the
        # wrapper that will receive its callbacks.  Because this one object plays
        # both roles, we pass `self` as that wrapper — hence
        # EClient.__init__(self, self): the first `self` is the client being
        # initialised, the second `self` is the wrapper it will route incoming
        # messages to.
        #
        # How this could instead have been done with super():
        #   Previously IBApp inherited from an intermediate base (IBKRApp) and
        #   called super().__init__() here.  That base's __init__ in turn ran
        #   EClient.__init__(self, self) for us, so a single super() call set up
        #   all the shared client/wrapper plumbing before we added our own state
        #   containers below.  The appeal of super() is that it follows the MRO
        #   (Method Resolution Order — the fixed order Python searches base
        #   classes for a method/attribute), so if another base were inserted
        #   later, super() would initialise it without us naming the class.
        # Why we DON'T use super() here:
        #   We have collapsed that intermediate base away — IBApp now inherits
        #   EWrapper and EClient directly — so there is a single, fixed base to
        #   initialise.  Naming EClient.__init__ explicitly is clearer about what
        #   is being set up, and avoids any ambiguity over which base super()
        #   would resolve to first across the two-parent MRO.
        EClient.__init__(self, self)

        # Flipped to True by nextValidId() — the first callback IBKR fires after a
        # successful TCP + handshake connection — and back to False by
        # connectionClosed().  connect_ib() polls this before letting callers send
        # requests, and reserve_order_id() also gates on it for a quick local check.
        self.connected = False

        self.on_tick = on_tick

        # For the below, it is key to understand threads and locks

        # == What a thread is and why we use it ==
        # A thread is a separate flow of execution 
        # Within the IB API, the socket connection to TWS/Gateway runs on a background thread 
        # that listens for incoming messages and dispatches them to the appropriate EWrapper callbacks.
        # On the other hand, the main thread is where we issue requests and run our application logic.
        # The fact that we have at least 2 threads (main thread + IB API thread) is why we need to be
        #  careful about shared state and use locks

        # === What a lock is and why we use it ===
        #  note that all a lock is is a synchronization primitive that can be used
        #  to protect access to shared resources in a multi-threaded environment.

        # Operationally, it is used a 'with self.lock' guard around any code 
        # that reads or writes shared state, to make sure that only one thread 
        # can execute that code at a time, which prevents race conditions and
        #  keeps the internal state consistent even when multiple callbacks arrive concurrently.

        # In object terms, what this is specifically is an instance of a threading.RLock()
        #  object,  which is a type of lock that can be acquired multiple times by the
        #  same thread without causing a deadlock.
        self.lock = threading.RLock()

        # These counters hand out unique request IDs and order IDs.  Keeping
        # them on the app ensures every helper function uses the same shared
        # connection-safe sequence.
        # set to be an itertools.count() object that generates an infinite 
        # sequence of integers starting from 1.
        self._req_id_source = itertools.count(1)
        # Next valid order id, populated by the nextValidId() EWrapper callback
        # right after connect. Required by placeOrder; we increment per order
        # (locally, via reserve_order_id) rather than re-querying IBKR each time.
        self.next_order_id = None

        # Store transient events per request so caller-side helper functions can
        # block until the matching callback stream is complete.
        self._hist_events = {}
        self._account_events = {}
        self._pnl_events = {}

        # Terminal request errors, keyed by reqId -> (errorCode, errorString),
        # written by error().  TWS answers a REJECTED request with an error and
        # then nothing else — no historicalDataEnd, no accountSummaryEnd — so the
        # error callback is the ONLY place a waiter can learn the request died.
        # Callers read this after their event unblocks to distinguish "the data
        # arrived" from "the request was refused" (see get_equity_data).
        self.req_errors = {}

        # NON-terminal messages, keyed the same way. TWS says plenty that does not
        # end a request but does explain its behaviour — warning 10311 ("directly
        # routed", which makes TWS hold the order for manual confirmation) being the
        # case that matters most here. Recording it separately keeps the terminal
        # test honest while preserving the only evidence that an order was seen at
        # all: without this, five orders sitting visibly in the TWS Pending panel
        # were reported as "never received".
        self.req_notices = {}

        # Market-data style state.
        self.managed_accounts = []
        # Latest trade price, written by tickPrice() when tickType is LAST (4/68).
        # note that tickPrice is the built-in EWrapper callback that IBKR fires 
        # when a new price tick arrives, and it passes the price and tickType as arguments.
        self.last_price = None
        # Latest top-of-book quote, written by tickPrice() for BID (1/66) /
        # ASK (2/67). Used to synthesize a mid when LAST is stale (FX/CRYPTO).
        self.bid = None
        self.ask = None
        # Active secType (set by the GUI right before reqMktData). Gates
        # _maybe_emit_mid: FX/CRYPTO ("CASH"/"CRYPTO") keep emitting mids even
        # after a LAST has been seen because their LAST stream is sparse/stale;
        # equities (STK) suppress mids in favour of genuine LAST prints.
        self.sectype = "STK"
        # Dedup guard so identical consecutive synthesized mids don't spam on_tick.
        self._last_emitted_mid = None
        # Provenance tag set right before each on_tick call so the consumer/log can
        # see where the price came from (a real LAST tick vs a synthesized mid).
        self._last_tick_source = "?"
        # Net liquidation value, written by accountSummary() on the NetLiquidation
        # tag. A convenience for UIs that want a single account-value number
        # without re-deriving it from account_summary_by_tag.
        self.account_value = None

        # Historical bars keyed by reqId so multiple symbols can be in flight
        # without colliding in the same list.
        self.historical_data = {}

        # Account summary rows are naturally flat records; we also keep a tag
        # lookup keyed by (account, tag) for quick inspection if needed.
        self.account_summary_rows = []
        self.account_summary_by_tag = {}

        # Positions / portfolio / order state are keyed by account + contract
        # identity (or orderId) so updates overwrite cleanly over time.
        self.positions = {}
        self.portfolio = {}
        self.open_orders = {}
        self.order_status = {}
        self.executions = []
        # Commissions arrive on their OWN callback (commissionReport), separately from the
        # fill itself and possibly after it, keyed by execId. Kept in a dict so
        # get_executions_data can join them back onto the executions once both have landed —
        # without this every fill looks free and any cost analysis is silently wrong.
        self.commissions = {}
        self.account_pnl = {}
        self.contract_pnl = {}

        # Shared completion events for request types that IBKR finishes with an
        # "...End" callback.
        self.positions_event = threading.Event()
        self.open_orders_event = threading.Event()
        self.account_updates_event = threading.Event()
        self.executions_event = threading.Event()

    # --- Connection lifecycle ---------------------------------------------

    def start(self, host='127.0.0.1', port=7497, client_id=123):
        """Connect this app instance to TWS / Gateway.

        If a client ID is already in use (error 326), retries automatically
        with client_id+1 and client_id+2 before giving up.
        """
        tried = []
        for attempt_id in (client_id, client_id + 1, client_id + 2):
            try:
                self.close()
            except Exception:
                pass
            self.connected = False
            self.next_order_id = None
            tried.append(attempt_id)
            try:
                # try a slightly different id
                connect_ib(self, host=host, port=port, client_id=attempt_id)
                return self
            except ConnectionError:
                continue
        raise ConnectionError(
            f'Failed to connect with client IDs {tried}. '
            'Check that TWS/Gateway is running and the API port is enabled.'
        )

    def close(self):
        """Disconnect this app instance from TWS / Gateway."""
        disconnect_ib(self)

    # --- Small app-level utilities ----------------------------------------

    def next_req_id(self):
        """Return the next unique request ID for a helper function."""
        with self.lock:
            # the next() call advances the counter, so the next caller will 
            # get a different ID
            # the counter being referred to is the _req_id_source, which is 
            # as per initialisation of this IBApp class, set to be an 
            # itertools.count() object that generates an infinite sequence of
            #  integers starting from 1. Each time next() is called on it,
            #  it returns the next integer in the sequence. 
            # By using a lock around this, we ensure that even if multiple
            #  threads call next_req_id() at the same time, they won't get
            #  the same ID because the lock will serialize access to the counter.
            return next(self._req_id_source)

    def reserve_order_id(self, timeout=10):
        """Wait for TWS to provide the next valid order ID, then reserve it.

                IBKR sends the next valid order ID via the `nextValidId` callback.
                Once we have an initial value, we can safely increment locally per
                order (IBKR recommends this pattern).

                Why this exists (and why we increment locally)
                ---------------------------------------------
                - IBKR's guidance says the `nextValidId` value *may* be queried on each
                    request.
                - However, it is often recommended to request it once at the beginning
                    of the session (via the `nextValidId` callback), and then locally
                    increment the value for each subsequent order.
                - This method implements that approach.

        Notes
        -----
        - If `timeout` is 0, this method does not wait; it will raise if
          `nextValidId` has not arrived yet.
        - This method increments `self.next_order_id` under a lock so multiple
          callers cannot reserve the same ID.
        """
        # first, check that we're connected at all before trying to reserve an
        #  order ID, because if we're not connected, we won't receive the 
        # nextValidId callback and the logic below will just wait until it times out.
        if not self.connected:
            raise ConnectionError('IBApp is not connected to TWS/Gateway.')

        # `next_order_id` is populated by the `nextValidId` callback. Because
        # the socket/event loop is running on a background thread, the main
        # thread may reach this method before that callback fires.
        #
        # If a timeout is provided, we poll briefly to give TWS/Gateway time
        # to deliver the callback. If timeout==0, we skip waiting and fail fast.
        # we only wait for a callback because upon connection, we should receive a
        # self.next_order_id from the nextValidId callback, automatically


        if self.next_order_id is None and timeout:
            # Compute an absolute deadline so our polling loop cannot run
            # indefinitely if IBKR never responds.
            deadline = time.time() + timeout
            while self.next_order_id is None and time.time() < deadline:
                # Small sleep keeps CPU usage low while we wait for the
                # callback thread to update `self.next_order_id`.
                time.sleep(0.1)

        # if after the above loop, we still don't have a next_order_id, 
        # it means we timed out waiting for the callback
        if self.next_order_id is None:
            raise RuntimeError('No nextValidId received yet.')

        # Reserve the current ID and increment it so the next caller gets a
        # fresh one. This is protected by `self.lock` to prevent two callers
        # from reserving the same order ID if they place orders concurrently.
        with self.lock:
            order_id = self.next_order_id
            # this time, as per recommendations from IBKR, we don't wait for the
            # connection's own callback to update self.next_order_id, but we just
            #  increment it locally for each new order we place,
            self.next_order_id += 1

        return order_id

    # --- EWrapper callbacks: connection / handshake -----------------------

    def nextValidId(self, orderId):
        # IBKR fires this as its FIRST message after a successful handshake, so it
        # doubles as our "connection ready" signal (connect_ib() polls connected).
        # Set next_order_id BEFORE flipping connected so the connect_ib() poll loop
        # cannot observe connected=True while next_order_id is still None (race
        # window between the two lines).  We set connected here directly now that
        # IBApp has no IBKRApp parent to delegate to.
        self.next_order_id = orderId
        self.connected = True

    def connectionClosed(self):
        # Called by IBKR when the socket is closed (TWS restart, network drop).
        # Reset connected so callers and reserve_order_id() see an accurate flag
        # rather than a stale True left over from the last successful handshake.
        self.connected = False

    def error(self, reqId, *args):
        # ibapi 10.30+ widened this callback: legacy signature was
        #   (reqId, errorCode, errorString, advancedOrderRejectJson="")
        # newer builds (10.47+) pass an additional `errorTime` (epoch ms) and may
        # reorder/extend further. Accept *args and locate fields by type to stay
        # forward-compatible.
        #
        # ══════════════════════════════════════════════════════════════════════
        # THE errorTime TRAP. On ibapi 10.47 the real signature is
        #     error(reqId, errorTime, errorCode, errorString, advancedOrderRejectJson)
        # so errorTime comes FIRST and is also an int. Taking "the first int" as the
        # error code therefore reads an epoch-millisecond timestamp as the code, and
        # everything downstream that switches on the code silently stops working:
        # the routine 2104/2106/2158/2176 farm-status pings were printed as errors
        # (`IB Error -1: 1785082784783 - Market data farm connection is OK`), and the
        # terminal-error handling below never fired because an epoch value is always
        # >= 2100 — so a rejected order was recorded as "no answer from TWS" when TWS
        # had in fact answered plainly.
        #
        # An epoch-ms timestamp is ~1.7e12; no IB error code is above five digits, so
        # magnitude separates them unambiguously and keeps working if the argument
        # order changes again.
        # ══════════════════════════════════════════════════════════════════════
        errorTime = None
        errorCode = None
        errorString = ""
        for a in args:
            if isinstance(a, int) and not isinstance(a, bool):
                if a > 10 ** 11:          # epoch milliseconds, not an error code
                    errorTime = a
                elif errorCode is None:
                    errorCode = a
            elif isinstance(a, str) and not errorString:
                errorString = a
        # Codes 2104 ("Market data farm connection is OK"), 2106 ("HMDS data farm
        # connection is OK"), 2158 ("Sec-def data farm connection is OK") and 2176
        # are routine connection-status pings, not errors — swallow them so they
        # don't clutter the console.
        if errorCode in (2104, 2106, 2158, 2176):
            return
        print(f"IB Error {reqId}: {errorCode} - {errorString}")

        # --- Unblock whoever is waiting on this request ----------------------
        # TWS sends NO terminating callback for a request it refused: a rejected
        # reqHistoricalData never produces historicalDataEnd, so the Event that
        # _wait_for() is blocked on would stay unset until the timeout expires
        # and then raise.  That turns a 1-second "no such contract" into a 30s
        # stall that aborts an entire multi-symbol pull.  Record the failure and
        # set the event here so the waiter returns immediately and can report
        # WHY rather than guessing "timed out".
        #
        # Which codes are terminal: reqId < 0 means the message is connection-level
        # (data-farm status, TWS notices) and belongs to no request at all.  Codes
        # >= 2100 are the advisory band — delayed-data substitutions, farm
        # connect/disconnect churn — which arrive alongside a request that is still
        # very much alive.  Everything below 2100 with a real reqId (200 "no
        # security definition", 162 "historical data request failed", 321 "error
        # validating request", ...) genuinely ends that request.
        #
        # Note the 10000+ band (10311 "directly routed", and its neighbours) is also
        # advisory: the order lives on, though TWS may hold it for confirmation. So it
        # is recorded as a NOTICE rather than dropped — "not terminal" and "not worth
        # keeping" are different claims, and treating them as one made held orders
        # indistinguishable from lost ones.
        if reqId is None or reqId < 0 or errorCode is None:
            return
        # Not every sub-2100 code is a failure. 399 is IB's "Order Message" — a warning
        # attached to an order that was ACCEPTED, e.g.
        #   "Warning: Your order will not be placed at the exchange until 2026-07-27 09:00"
        # for a market order submitted while the venue is shut. Classifying that as
        # terminal made five PreSubmitted orders print as "REJECTED" while they sat
        # perfectly healthy in the book, queued for the open.
        if errorCode >= 2100 or errorCode in _ADVISORY_ORDER_CODES:
            with self.lock:
                self.req_notices[reqId] = (errorCode, errorString)
            return
        with self.lock:
            self.req_errors[reqId] = (errorCode, errorString)
        # The reqId space is shared across request types, so at most one of these
        # dicts holds the id; setting all three that match is safe and saves the
        # caller from having to tell error() which kind of request it issued.
        for pending in (self._hist_events, self._account_events, self._pnl_events):
            event = pending.get(reqId)
            if event:
                event.set()

    def managedAccounts(self, accountsList):
        self.managed_accounts = [acct for acct in accountsList.split(',') if acct]

    # --- EWrapper callbacks: market data ----------------------------------

    def tickPrice(self, reqId, tickType, price, attrib):
        # IB delivers different tickType IDs depending on the data tier:
        #   LIVE  (entitlement / paid sub):  1=BID, 2=ASK, 4=LAST
        #   DELAYED (free, ~15min lag):     66=BID, 67=ASK, 68=LAST
        #   FROZEN/DELAYED-FROZEN: same IDs as their non-frozen counterparts.
        # The tier is selected by reqMarketDataType(...) before reqMktData. If we
        # only listened on 4 we would miss every delayed feed entirely (e.g. crypto
        # on a Sunday arrives as type 68 — ignored, so on_tick never fires and any
        # downstream model sits frozen).
        if price <= 0:
            return
        if tickType in (4, 68):          # LAST (live) or LAST (delayed)
            self.last_price = price
            # Guard: on_tick may be None when IBApp is used without a consumer
            # (historical-only pulls, REPL, account/position scripts). Calling
            # None(...) would raise TypeError on the reader thread and can
            # destabilise the EReader loop, so we skip rather than blow up.
            if self.on_tick:
                self._last_tick_source = f"LAST({tickType})"
                # Pass a timestamp so consumers can bar-aggregate. ts is optional
                # in the signature for callers that don't need it.
                self.on_tick(price, datetime.now())
        elif tickType in (1, 66):        # BID (live) or BID (delayed)
            self.bid = price
            # New bid — try to synthesize a mid if we also have an ask but LAST is
            # stale/missing (common for FX/crypto).
            self._maybe_emit_mid()
        elif tickType in (2, 67):        # ASK (live) or ASK (delayed)
            self.ask = price
            self._maybe_emit_mid()

    def _maybe_emit_mid(self):
        # FX (and frequently crypto on PAXOS) never sends LAST ticks — only
        # BID/ASK. Without this, on_tick would never fire and any downstream model
        # would freeze even though quotes are streaming. Synthesize a "last" from
        # the mid of the most recent bid/ask pair and route it through on_tick.
        # Equities (STK) prefer real LAST prints; if one has arrived, suppress mid
        # synth so on_tick is driven by genuine trades. FX/CRYPTO have no
        # meaningful LAST stream — bid/ask is the continuous signal there, so we
        # keep emitting mids regardless of any stale LAST cached for a price label.
        if self.last_price is not None and self.sectype not in ("CASH", "CRYPTO"):
            return
        if self.bid is None or self.ask is None or self.ask <= self.bid:
            return
        mid = 0.5 * (self.bid + self.ask)
        # Skip duplicate emits when neither side moved.
        if self._last_emitted_mid == mid:
            return
        self._last_emitted_mid = mid
        # Same on_tick None-guard as tickPrice: IBApp may have no consumer wired in.
        if self.on_tick:
            self._last_tick_source = "MID"
            self.on_tick(mid, datetime.now())

    # --- EWrapper callbacks: historical data ------------------------------

    def historicalData(self, reqId, bar):
        """Collect OHLCV bars from reqHistoricalData(), keyed by reqId.

        We use a dict keyed by reqId because one app instance may have several
        historical requests in flight at once, and we need to keep each
        symbol's bars separate until the outer helper function assembles the
        final DataFrames.
        """
        # first, use a self.lock guard to ensure that only one thread can 
        # execute this block at a time, which prevents race conditions 
        # when multiple historicalData callbacks arrive concurrently 
        # and try to update the same app.historical_data dict.
        with self.lock:
            self.historical_data.setdefault(reqId, []).append({
                'datetime': bar.date,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            })

    def historicalDataEnd(self, reqId, start, end):
        """Signal that all historical bars for this reqId have arrived."""
        event = self._hist_events.get(reqId)
        if event:
            event.set()

    # --- EWrapper callbacks: account summary ------------------------------

    def accountSummary(self, reqId, account, tag, value, currency):
        """Collect tag/value rows from reqAccountSummary()."""

        # as per the API docus, this could include things like:
        # NetLiquidation, TotalCashValue, AvailableFunds
        # as we have right now by default, but can also include things like:
        # EquityWithLoanValue

        # It is returned to us through the EWrapper class in a callback that 
        # takes the form of a dict with keys:
        # 'reqId', 'account', 'tag', 'value', and 'currency'.


        # ----i.e., from the API docs: 
        # def accountSummary(self, reqId: int, account: str, tag: str, value: str,currency: str):
        # print("AccountSummary. ReqId:", reqId, "Account:", account,"Tag: ", tag, "Value:", value, "Currency:", currency)

        row = {
            'reqId': reqId,
            'account': account,
            'tag': tag,
            'value': value,
            'currency': currency,
        }
        print("AccountSummary. ReqId:", reqId,
               "Account:", account,
               "Tag: ", tag,
               "Value:", value,
               "Currency:", currency)
        # self.lock is a reentrant lock that ensures thread-safe access 
        # to shared state like account_summary_rows and account_summary
        # so if we only execute the following logic after a with self.lock,
        # it means that even if multiple callbacks are trying to update 
        # the account summary at the same time, they won't step on each other's
        # updates and the internal state will remain consistent.
        with self.lock:
            self.account_summary_rows.append(row)
            self.account_summary_by_tag[(account, tag)] = row
            # Convenience: surface the single most-asked-for number (net
            # liquidation) as a flat attribute so UIs can read app.account_value
            # without re-scanning account_summary_by_tag. Best-effort float coerce.
            if tag == "NetLiquidation":
                try:
                    self.account_value = float(value)
                except (TypeError, ValueError):
                    pass

    def accountSummaryEnd(self, reqId):
        """Signal that all requested account-summary rows have arrived."""
        event = self._account_events.get(reqId)
        if event:
            print(f"AccountSummaryEnd. ReqId: {reqId}")
            event.set()

    # --- EWrapper callbacks: positions / portfolio ------------------------

    def position(self, account, contract_obj, position, avgCost):
        """Collect current positions from reqPositions()."""
        key = (account, contract_obj.conId or contract_obj.symbol)

        with self.lock:
            self.positions[key] = {
                'account': account,
                'symbol': contract_obj.symbol,
                'conId': contract_obj.conId,
                'secType': contract_obj.secType,
                'exchange': contract_obj.exchange,
                'currency': contract_obj.currency,
                'position': position,
                'avgCost': avgCost,
            }

    def positionEnd(self):
        """Signal that the current positions snapshot is complete."""
        self.positions_event.set()

    def updatePortfolio(
        self,
        contract_obj,
        position,
        marketPrice,
        marketValue,
        averageCost,
        unrealizedPNL,
        realizedPNL,
        accountName,
    ):
        """Collect per-contract portfolio values from reqAccountUpdates()."""
        key = (accountName, contract_obj.conId or contract_obj.symbol)

        with self.lock:
            self.portfolio[key] = {
                'account': accountName,
                'symbol': contract_obj.symbol,
                'conId': contract_obj.conId,
                'secType': contract_obj.secType,
                'exchange': contract_obj.exchange,
                'currency': contract_obj.currency,
                'position': position,
                'marketPrice': marketPrice,
                'marketValue': marketValue,
                'averageCost': averageCost,
                'unrealizedPnL': unrealizedPNL,
                'realizedPnL': realizedPNL,
            }

    def accountDownloadEnd(self, accountName):
        """Signal that reqAccountUpdates() has finished its initial snapshot."""
        self.account_updates_event.set()

    # --- EWrapper callbacks: orders / executions --------------------------

    def openOrder(self, orderId, contract_obj, order, orderState):
        """Collect open-order details from reqOpenOrders()."""
        with self.lock:
            self.open_orders[orderId] = {
                'orderId': orderId,
                'symbol': contract_obj.symbol,
                'conId': contract_obj.conId,
                'secType': contract_obj.secType,
                'exchange': contract_obj.exchange,
                'currency': contract_obj.currency,
                'action': order.action,
                'orderType': order.orderType,
                'totalQuantity': order.totalQuantity,
                'lmtPrice': getattr(order, 'lmtPrice', None),
                'auxPrice': getattr(order, 'auxPrice', None),
                'tif': order.tif,
                'status': getattr(orderState, 'status', None),
            }

    def openOrderEnd(self):
        """Signal that the open-order snapshot is complete."""
        self.open_orders_event.set()

    def orderStatus(
        self,
        orderId,
        status,
        filled,
        remaining,
        avgFillPrice,
        permId,
        parentId,
        lastFillPrice,
        clientId,
        whyHeld,
        mktCapPrice,
    ):
        """Keep the latest order status keyed by order ID."""
        with self.lock:
            status_row = self.order_status.setdefault(orderId, {})
            status_row.update({
                'orderId': orderId,
                'status': status,
                'filled': filled,
                'remaining': remaining,
                'avgFillPrice': avgFillPrice,
                'permId': permId,
                'parentId': parentId,
                'lastFillPrice': lastFillPrice,
                'clientId': clientId,
                'whyHeld': whyHeld,
                'mktCapPrice': mktCapPrice,
            })

            if orderId in self.open_orders:
                self.open_orders[orderId].update(status_row)
                if status in _TERMINAL_ORDER_STATES:
                    self.open_orders[orderId]['isTerminal'] = True

    def execDetails(self, reqId, contract_obj, execution):
        """Collect execution fills as they arrive."""
        with self.lock:
            self.executions.append({
                'reqId': reqId,
                # execId is the join key for commissionReport, which arrives separately.
                # Without it a fill can never be matched to what it cost.
                'execId': execution.execId,
                'symbol': contract_obj.symbol,
                'conId': contract_obj.conId,
                # secType/currency are needed to rebuild the Contract for any follow-up
                # request, and to know whether a multiplier applies to the price.
                'secType': contract_obj.secType,
                'currency': contract_obj.currency,
                'side': execution.side,
                'shares': execution.shares,
                'price': execution.price,
                # cumQty/avgPrice describe the PARENT order's progress at this fill, which
                # is how a partially-filled order is distinguished from a complete one.
                'cumQty': execution.cumQty,
                'avgPrice': execution.avgPrice,
                'time': execution.time,
                'orderId': execution.orderId,
                'permId': execution.permId,
            })

    def execDetailsEnd(self, reqId):
        """Signal that reqExecutions() has delivered every matching fill.

        Without this the caller has no completion signal and would have to guess at a
        sleep duration — the same pattern positionEnd / accountDownloadEnd already use.
        """
        self.executions_event.set()

    def commissionReport(self, commissionReport):
        """Collect per-execution commission/realised figures.

        IB sends this on a SEPARATE callback from execDetails and does not guarantee
        ordering between the two, so it is stored by execId and joined later rather than
        being attached to a fill in flight.

        Note `realizedPNL` here is IB's OWN average-cost calculation for the closing
        portion of the fill. We keep it for comparison but never treat it as authoritative:
        our realised P&L comes from Position.apply_fill, so the two can be reconciled
        against each other instead of one silently overwriting the other.
        """
        with self.lock:
            self.commissions[commissionReport.execId] = {
                'execId': commissionReport.execId,
                'commission': commissionReport.commission,
                'currency': commissionReport.currency,
                'realizedPNL': commissionReport.realizedPNL,
            }

    # --- EWrapper callbacks: PnL ------------------------------------------

    def pnl(self, reqId, dailyPnL, unrealizedPnL, realizedPnL):
        """Store account-level PnL snapshots from reqPnL()."""
        with self.lock:
            self.account_pnl[reqId] = {
                'reqId': reqId,
                'dailyPnL': dailyPnL,
                'unrealizedPnL': unrealizedPnL,
                'realizedPnL': realizedPnL,
            }

        event = self._pnl_events.get(reqId)
        if event:
            event.set()

    def pnlSingle(self, reqId, pos, dailyPnL, unrealizedPnL, realizedPnL, value):
        """Store contract-level PnL snapshots from reqPnLSingle()."""
        with self.lock:
            self.contract_pnl[reqId] = {
                'reqId': reqId,
                'position': pos,
                'dailyPnL': dailyPnL,
                'unrealizedPnL': unrealizedPnL,
                'realizedPnL': realizedPnL,
                'value': value,
            }

        event = self._pnl_events.get(reqId)
        if event:
            event.set()


# ═══════════════════════════════════════════════════════════════════════════
# Historical equity data
# ═══════════════════════════════════════════════════════════════════════════

def get_equity_data(
    symbols=None,
    host='127.0.0.1',
    port=7497,
    duration='1 Y',
    bar_size='1 day',
    end_date='',
    output_dir=None,
    skip_existing=False,
    client_id=123,
    app=None,
    output_format='dict',
    merged_filename='portfolio_prices.csv',
    contract_specs=None,
    what_to_show='TRADES',
):
    """Fetch historical OHLCV data for one or more symbols.

    Parameters
    ----------
    symbols : str or list[str]
        Ticker(s) to fetch.  Defaults to DEFAULT_SYMBOLS.
    duration : str
        How far back from *end_date* (e.g. '1 Y', '6 M', '30 D').
    bar_size : str
        Bar granularity (e.g. '1 day', '1 hour', '5 mins').
    end_date : str
        End date in 'YYYYMMDD HH:MM:SS' UTC format.  '' = now.
    output_dir : str or Path, optional
        Directory in which to persist output files.  If None, nothing is
        written to disk regardless of output_format.
    skip_existing : bool
        When True, skip any ticker whose CSV already exists in *output_dir*.
        Ignored when output_dir is None (nothing to check against).
    app : IBApp, optional
        Existing connected app instance.  If omitted, this function will create
        its own temporary IBApp connection and close it on exit.
    output_format : str
        Controls the return type and what is saved to output_dir:
          'dict'     (default) — return dict[str, DataFrame], one entry per
                     symbol; if output_dir given, write stock_data_{sym}.csv
                     for each symbol.
          'combined' — merge all symbol close series into one wide DataFrame
                     (columns: Date + one per symbol); if output_dir given,
                     write a single merged_filename CSV instead of per-symbol
                     files.
    merged_filename : str
        Filename for the combined CSV when output_format='combined'.
        Ignored for output_format='dict'.  Default: 'portfolio_prices.csv'.
    contract_specs : dict[str, dict], optional
        Per-symbol keyword overrides passed to contract() — e.g.
        {'5MVL': {'con_id': 123456, 'exchange': 'LSE', 'currency': 'GBP'}}.
        Symbols absent from the mapping keep contract()'s STK/SMART/USD
        defaults, so existing US-only callers are unaffected.  Build one
        straight from an account snapshot with contract_specs_from_portfolio().
    what_to_show : str
        Price basis for reqHistoricalData ('TRADES', 'MIDPOINT', 'BID', ...).
        Some non-US listings carry no trade-data entitlement and return nothing
        for 'TRADES' while 'MIDPOINT' works.  Deliberately NOT auto-retried:
        a silent basis switch would change what the numbers mean.

    Returns
    -------
    dict[str, DataFrame] when output_format='dict'
        Mapping of symbol -> OHLCV DataFrame (with a 'return' column).
    pd.DataFrame when output_format='combined'
        Columns: 'Date' + one close-price column per fetched symbol.
        Empty DataFrame if no data was returned.
    """
    # --- Normalise input ---------------------------------------------------
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    if isinstance(symbols, str):
        symbols = [symbols]  # accept a single ticker string

    # --- Resolve save directory only when output_dir was given --------------
    # When output_dir is None we never touch the filesystem — no mkdir, no CSV.
    save_dir = pathlib.Path(output_dir) if output_dir is not None else None
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)

    # --- Skip tickers that already have saved CSVs -------------------------
    # skip_existing only makes sense when we have a directory to check; skip
    # the whole block when output_dir is None to avoid a spurious scan.
    if skip_existing and save_dir is not None:
        needed = []
        for sym in symbols:
            prefixed = save_dir / f'stock_data_{sym}.csv'
            bare = save_dir / f'{sym}.csv'
            if prefixed.exists() or bare.exists():
                print(f"Skipping {sym}: CSV already exists in {save_dir}")
            else:
                needed.append(sym)
        symbols = needed

    # if we have no symbols left to fetch after the above skipping logic, we 
    # can return early with an empty result, since there's nothing to fetch from IBKR.
    if not symbols:
        print("All tickers already have saved data — nothing to fetch.")
        # Return the appropriate empty sentinel for the requested output type.
        return {} if output_format == 'dict' else pd.DataFrame()

    # --- Ensure we have an app / connection -------------------------------
    # The important architecture point is that the request workflow lives here,
    # outside IBApp.  We may either reuse a caller-supplied app instance or
    # create a temporary one for this standalone fetch.
    # Calling app.start(host=host, port=port, client_id=client_id) is
    # effectively saying "use the IBApp convenience method that, in turn,
    # calls connect_ib(app, host, port, client_id) for me."
    app, owns_app = _ensure_connected_app(app, host=host, port=port, client_id=client_id)

    try:
        # --- Set up the request window / bookkeeping -----------------------
        # Default to "now" in UTC if no end_date was supplied.
        end_time = end_date or time.strftime('%Y%m%d-%H:%M:%S', time.gmtime())

        # Map reqId -> symbol so we can label DataFrames after collection.
        id_to_symbol = {}
        events = {}

        # Normalise the per-symbol contract overrides once so the request loop
        # can do a plain .get() without a None check on every iteration.
        contract_specs = contract_specs or {}

        # --- Fire one request per symbol ----------------------------------
        # Each symbol gets its own reqId and completion Event.  IBKR will
        # respond asynchronously via historicalData() / historicalDataEnd().
        for i, sym in enumerate(symbols):
            req_id = app.next_req_id()
            id_to_symbol[req_id] = sym
            events[req_id] = threading.Event()

            with app.lock:
                app.historical_data[req_id] = []
                app._hist_events[req_id] = events[req_id]
                # Drop any stale failure recorded against a recycled reqId, so a
                # previous run's rejection cannot be misread as this one's.
                app.req_errors.pop(req_id, None)

            # Build a Contract object — IBKR's way of specifying an instrument.
            # contract_specs lets the caller pin the exact listing (conId /
            # exchange / currency) for symbols that are not US-SMART lines;
            # anything not listed keeps the STK/SMART/USD defaults.
            req_contract = contract(sym, **contract_specs.get(sym, {}))

            # Send the request.  The app's callback methods do the collection;
            # this outer function just coordinates the workflow and assembles
            # the final DataFrames.
            app.reqHistoricalData(
                reqId=req_id,
                contract=req_contract,
                endDateTime=end_time,      # end of the window
                durationStr=duration,      # how far back from endDateTime
                barSizeSetting=bar_size,   # bar granularity
                whatToShow=what_to_show,   # price basis (see the kwarg's docstring)
                useRTH=1,                  # 1 = regular trading hours only
                formatDate=1,              # 1 = human-readable dates
                keepUpToDate=0,            # 0 = one-shot, no streaming
                chartOptions=[],           # reserved by IBKR, pass empty
            )

            # Respect IBKR pacing: a small delay helps avoid throttling.
            if i < len(symbols) - 1:
                time.sleep(0.25)

        # --- Wait for all symbols to finish -------------------------------
        # We wait per request rather than using one global flag, which makes
        # the logic cleaner when several symbols are in flight at once.
        #
        # A failed symbol is a WARNING, not an abort.  One unresolvable ticker
        # used to raise TimeoutError out of this loop and throw away the bars of
        # every other symbol that had already arrived; that is the opposite of
        # useful when the pull covers a whole account.  Collect the failures, name
        # them at the end, and let the caller decide.
        timeout_per_symbol = 30
        failed = {}
        for req_id, event in events.items():
            sym = id_to_symbol[req_id]
            try:
                _wait_for(event, timeout_per_symbol, f'historical data for {sym}')
            except TimeoutError:
                # Nothing at all came back within the window — TWS is unhealthy,
                # throttling us, or the request silently vanished.
                failed[sym] = 'timed out'
                print(f"Skipped {sym}: timed out after {timeout_per_symbol}s.")
                continue

            # The event can be set by EITHER historicalDataEnd (success) or by
            # error() recording a terminal rejection.  req_errors is what tells
            # the two apart — without this check a refused symbol looks like a
            # symbol that legitimately has no bars.
            with app.lock:
                err = app.req_errors.get(req_id)
            if err:
                failed[sym] = f'{err[0]} {err[1]}'
                print(f"Skipped {sym}: IB error {err[0]} - {err[1]}")

        # --- Build one DataFrame per symbol -------------------------------
        results = {}
        for req_id, sym in id_to_symbol.items():
            with app.lock:
                # here, the variable bars is a list of dicts, where each dict
                #  represents a historical bar with keys like:
                #   'datetime', 'open', 'high', 'low', 'close', and 'volume'. 
                # This list was populated by the historicalData() callback as 
                # the data arrived from IBKR, and collected in the app.historical_data 
                # dict under the key of this req_id.
                bars = list(app.historical_data.get(req_id, []))

            if not bars:
                # Already-reported failures don't need a second, vaguer line —
                # "Skipped X: IB error 200" above says strictly more than this.
                if sym not in failed:
                    print(f"Warning: no data returned for {sym}")
                    failed[sym] = 'no bars returned'
                results[sym] = pd.DataFrame()
                # Still drop the pending event: the request is over either way,
                # and a leftover entry would let a recycled reqId be set by a
                # stale callback.  No cancelHistoricalData — TWS already ended
                # (or refused) this request, so cancelling it invents an error.
                with app.lock:
                    app._hist_events.pop(req_id, None)
                continue

            df = pd.DataFrame(bars)
            df['return'] = df['close'].pct_change()  # simple bar-on-bar return
            df = df.dropna(subset=['return'])        # drop first row (NaN)
            df = df['datetime,open,high,low,close,volume,return'.split(',')]

            # Persist per-symbol CSV only in 'dict' mode and only when a save
            # directory was supplied — 'combined' mode writes one merged file
            # later, and output_dir=None means the caller wants no disk output.
            if save_dir is not None and output_format == 'dict':
                df.to_csv(save_dir / f'stock_data_{sym}.csv', index=False)

            # Store the completed DataFrame in the results dict under the symbol key.
            # results is a dict[str, DataFrame] — the same structure as `frames` in
            # sandbox/ibkr_data.py, where `frames[symbol] = df` does the identical
            # thing.  The difference is that ibkr_data.py collects into `frames`
            # sequentially (one symbol at a time, resetting app.data between requests),
            # whereas here all requests were fired first and we are now assembling
            # results after all have returned, mapping back from req_id → sym via
            # id_to_symbol.
            results[sym] = df

            # Clean up this request's temporary state now that we are done.
            #
            # NO cancelHistoricalData HERE. We only reach this line because
            # historicalDataEnd already fired, i.e. the request is FINISHED —
            # and cancelling a finished request makes TWS answer
            # "No historical data query found for ticker id:N" through the error
            # callback. Harmless, but it prints an alarming-looking IB Error in the
            # middle of an order printout, which is the last place to be crying wolf.
            # (Cancel is only correct for a request still streaming, e.g. one opened
            # with keepUpToDate=1.)
            with app.lock:
                app._hist_events.pop(req_id, None)

        # --- Say plainly what we got ---------------------------------------
        # Partial data that looks complete is the dangerous outcome: a backcast
        # or a weight calc silently drops the missing names and still prints a
        # confident number.  One summary line makes the gap impossible to miss.
        fetched = len(symbols) - len(failed)
        print(f"Fetched {fetched}/{len(symbols)} symbols.")
        if failed:
            print("Missing: " + ", ".join(f"{s} ({why})" for s, why in failed.items()))

        # ── Combined output path ───────────────────────────────────────────
        # When output_format='combined' we merge all per-symbol close series
        # into one wide DataFrame (one date column + one price column per
        # symbol) — the same shape as portfolio_prices.csv.  This replicates
        # merge_closes_by_date() from sandbox/ibkr_data.py but operates on
        # the 'datetime' column that get_equity_data() produces (ibkr_data.py
        # uses 'date').
        if output_format == 'combined':
            merged = None
            for sym, df in results.items():
                if df.empty:
                    # Symbol returned no bars — skip so it doesn't corrupt merge.
                    continue
                # Keep only the timestamp and close columns; rename close → symbol
                # so the merged frame has one unambiguous column per ticker.
                piece = df[['datetime', 'close']].rename(columns={'close': sym})
                # Outer join so all dates are retained even when symbols have
                # different listing histories (new ETF will carry NaN earlier dates).
                merged = piece if merged is None else merged.merge(piece, on='datetime', how='outer')

            if merged is None:
                # Every symbol was empty — return an empty frame so callers can check .empty.
                return pd.DataFrame()

            # Sort chronologically; rename 'datetime' → 'Date' to match the
            # portfolio_prices.csv convention and PanelBuilder's expected index name.
            merged = (
                merged
                .sort_values('datetime')
                .rename(columns={'datetime': 'Date'})
                .reset_index(drop=True)
            )

            # convert the datetime column to datetime and sort by date
            merged['Date'] = pd.to_datetime(merged['Date'])
            merged = merged.sort_values('Date').reset_index(drop=True)

            # Persist the merged CSV only when a directory was provided.
            if save_dir is not None:
                out_path = save_dir / merged_filename
                merged.to_csv(out_path, index=False)
                print(f"Wrote merged price panel ({len(merged)} rows, {len(merged.columns)} cols) -> {out_path}")

            return merged

        return results

    finally:
        if owns_app:
            app.close()



def get_historical_bars(
    app,
    contract_obj,
    duration,
    bar_size,
    what_to_show='TRADES',
    use_rth=1,
    end='',
    timeout=20,
):
    """Fetch historical OHLCV bars for ONE contract as a list of dicts.

    This is the intraday-friendly sibling of get_equity_data(): it uses the same
    per-reqId Event handshake, but it neither writes a CSV nor builds a DataFrame,
    so a live UI can pull a short calibration window and feed the bars straight
    into a model.

    Requires an already-connected app (the caller owns the connection lifecycle).
    Returns list[dict] with keys datetime/open/high/low/close/volume, oldest first
    (the same shape historicalData() stores).
    """
    # Allocate a unique reqId from the shared counter so this pull cannot collide
    # with a market-data subscription or any other in-flight request.
    req_id = app.next_req_id()
    event = threading.Event()
    with app.lock:
        app.historical_data[req_id] = []
        app._hist_events[req_id] = event

    # Default endDateTime to "now" (UTC) when not supplied.
    end_time = end or time.strftime('%Y%m%d-%H:%M:%S', time.gmtime())
    try:
        # Fire the async request. historicalData() collects bars into
        # app.historical_data[req_id]; historicalDataEnd() sets our event.
        #   what_to_show: TRADES for equities; MIDPOINT/BID_ASK for FX/crypto
        #     (which have no "trade" prints, so TRADES would return no bars).
        #   use_rth: 1 = regular trading hours only; 0 = include 24h / extended
        #     sessions (needed for crypto, FX, futures out of hours).
        app.reqHistoricalData(
            reqId=req_id,
            contract=contract_obj,
            endDateTime=end_time,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
            keepUpToDate=0,            # one-shot, not a streaming subscription
            chartOptions=[],
        )
        # Block until historicalDataEnd fires (or we time out) — see _wait_for.
        _wait_for(event, timeout, 'historical bars')
        with app.lock:
            # list(...) copies before the finally clause clears the store.
            return list(app.historical_data.get(req_id, []))
    finally:
        # Clean up this request's transient state whether or not it succeeded, so
        # a later reuse of the same reqId can't see stale bars.
        try:
            app.cancelHistoricalData(req_id)
        except Exception:
            pass
        with app.lock:
            app._hist_events.pop(req_id, None)
            app.historical_data.pop(req_id, None)


# ═══════════════════════════════════════════════════════════════════════════
# Account summary
# ═══════════════════════════════════════════════════════════════════════════

# note that the below are self defined methods that adust for IBKR's infrastructure:
# They first make a request through an instance, app, of the IBApp
# (e.g. through app.reqAllOpenOrders() [just like EClient.reqAllOpenOrders()],
# and then wait for the relevant callback to .set() a per-request threading.Event
# (stored in app._hist_events / _account_events / _pnl_events keyed by reqId, or
# one of the shared events like app.positions_event), at which point the data the
# callbacks deposited (app.historical_data, app.account_summary_rows, etc.) is
# read back and assembled into the return value.


def get_account_data(
    app=None,
    tags=DEFAULT_TAGS,
    group='All',
    host='127.0.0.1',
    port=7497,
    client_id=124
):
    """Fetch account summary for *tags* and return a DataFrame.

    Parameters
    ----------
    tags : str
        A **comma-separated string** of account tag names (NOT a list).
        The IBKR API explicitly requires ``tags: str``.
    group : str
        Account group.  'All' returns data for every linked account.
    app : IBApp, optional
        Existing connected app instance.  If omitted, this function creates a
        temporary app, runs the request, and closes it before returning.
    """
    # Create or reuse a connected app.  Using a separate client_id from the
    # historical-data default remains useful when the caller wants concurrent
    # sessions.
    app, owns_app = _ensure_connected_app(app, host=host, port=port, client_id=client_id)

    try:
        # --- Prepare this request's state ---------------------------------
        # Each account-summary request gets its own reqId and completion Event
        # so multiple summaries can be coordinated cleanly if needed later.
        req_id = app.next_req_id()
        event = threading.Event()

        # the below syntax is effectively saying "store this event in the app's
        #  _account_events dict, keyed by req_id, so that when the accountSummaryEnd
        #  callback is received with this req_id, it can look up the corresponding
        #  event and set it to signal completion of this request."
        with app.lock:
            app._account_events[req_id] = event

        # --- Send the request ---------------------------------------------
        # IBKR will stream one callback per tag, followed by accountSummaryEnd().
        print("Requesting account summary with reqId:", req_id)
        # finally calls the fundamental reqAccountSummary() method from the EClient class, 
        # which sends the request to TWS/Gateway, and then we wait for the callbacks to arrive
        #  and update our app's state accordingly.
        app.reqAccountSummary(
            reqId=req_id,
            groupName=group,
            tags=tags,
        )
        print("Account summary request sent. Waiting for response...")

        # --- Wait for completion ------------------------------------------
        _wait_for(event, 30, 'account summary')
        print("Account summary response received.")

        # --- Build the DataFrame ------------------------------------------
        #
        with app.lock:
            rows = [row for row in app.account_summary_rows if row['reqId'] == req_id]

        df = pd.DataFrame(rows)
        df = _coerce_account_values(df)
        return df

    finally:
        # Cancel / clear request-specific state even if the request fails.
        try:
            app.cancelAccountSummary(req_id)
        except Exception:
            pass

        with app.lock:
            app._account_events.pop(req_id, None)

        if owns_app:
            app.close()


def get_exchange_rates(app, group='All'):
    """FX rates from every currency the account holds into its BASE currency.

    ═══════════════════════════════════════════════════════════════════════════
    WHY THIS IS NEEDED — updatePortfolio DOES NOT CONVERT.
    `marketPrice`, `marketValue`, `averageCost` and `unrealizedPnL` on a portfolio
    row are all in the POSITION'S OWN currency. Only NetLiquidation from the
    account summary is in the base currency. So a book holding EUR, GBP and USD
    lines cannot have its weights computed by dividing position values by NetLiq
    — that silently adds euros to pounds. Every non-base position must be
    converted first, and this is where the rate comes from.

    The rate is IBKR'S OWN, taken from the account ledger, so the converted
    values agree with the NetLiquidation they will be divided by. An FX rate
    pulled from a market-data request would be a second opinion on a number the
    broker has already decided.
    ═══════════════════════════════════════════════════════════════════════════

    Implementation note: `$LEDGER:ALL` is a special account-summary "tag" that
    makes TWS return one block per currency instead of one row per tag, including
    an `ExchangeRate` row per currency. The base currency itself appears under
    the pseudo-currency `BASE` with a rate of 1.

    Returns
    -------
    dict[str, float] — currency code -> multiplier into the base currency, so
    ``value_in_base = value_in_ccy * rates[ccy]``. Empty if TWS returned no
    ledger (callers must treat that as "cannot convert", never as "rate 1").
    """
    # Reuses the ordinary account-summary path — same reqId handshake, same
    # accountSummary callbacks — with the ledger pseudo-tag instead of a tag list.
    ledger = get_account_data(app=app, tags='$LEDGER:ALL', group=group)
    if ledger is None or len(ledger) == 0 or 'tag' not in ledger.columns:
        return {}

    rates = {}
    for _, row in ledger.iterrows():
        if str(row.get('tag')) != 'ExchangeRate':
            continue
        ccy = str(row.get('currency') or '').upper()
        try:
            rate = float(row.get('value'))
        except (TypeError, ValueError):
            continue
        if ccy and math.isfinite(rate) and rate > 0:
            rates[ccy] = rate
    return rates


def get_positions_data(app, timeout=15):
    """Fetch current positions using an existing connected IBApp."""
    # --- Reset the destination store --------------------------------------
    # reqPositions() returns a fresh snapshot, so we clear the previous one.
    app.positions_event.clear()
    with app.lock:
        app.positions = {}

    # --- Fire the request / wait for callbacks ----------------------------
    app.reqPositions()
    _wait_for(app.positions_event, timeout, 'positions')
    # if the callback event has occurred or not, we still want to cancel the positions
    #  subscription to avoid any future updates from coming in and overwriting our data,
    #  since we only want a snapshot of the current positions at the time of the request,
    #  and not a live stream of updates.
    app.cancelPositions()

    # note that with the built-in methods in the EWrapper class,
    # the responses to our request will 'automatically' change the state of our app instance, app, 
    # by invoking the relevant callback methods (e.g. position() and positionEnd()) that we have defined
    #  in our IBApp class, which will in turn update the relevant variables stored inapp.positions and
    #  app.positions_event respectively.

    # --- Convert the collected rows to a DataFrame ------------------------
    with app.lock:
        return pd.DataFrame(list(app.positions.values()))


def get_account_updates(app, account = "DUP102412", timeout=15):
    """Fetch portfolio rows for one account via reqAccountUpdates()."""
    # --- Reset the destination store --------------------------------------
    # updatePortfolio() callbacks will repopulate app.portfolio until
    # accountDownloadEnd() signals the initial snapshot is complete.
    app.account_updates_event.clear()
    with app.lock:
        app.portfolio = {}

    # --- Subscribe long enough to receive the initial snapshot ------------
    app.reqAccountUpdates(True, account)
    _wait_for(app.account_updates_event, timeout, f'account updates for {account}')
    app.reqAccountUpdates(False, account)

    # --- Build the result DataFrame ---------------------------------------
    with app.lock:
        return pd.DataFrame(list(app.portfolio.values()))


def submit_rebalance_orders(
    order_app,
    target_positions_df,
    *,
    symbol_col='symbol',
    currency_col='currency',
    units_col='units_diff',
    dry_run=True,
    contract_specs=None,
):
    """Submit a batch of market orders implied by a rebalance table.

    Lives here, in the library, rather than in a runnable script. It was originally defined
    in ``orders/rebalance_port.py`` (now ``rebalance_port_basic.py``) — but that file is a
    CELL SCRIPT with module-level side effects: importing it opens an IBKR connection and
    executes its bottom cells, which include a live ``dry_run=False`` rebalance and a stray
    market order. Any pipeline that imported the function from there would have transmitted
    those orders as an import side effect, before a single one of its own safety checks ran.
    That is precisely the failure the repo rule about import-clean library code exists to
    prevent, and it is why this function now has a home that can be imported safely.

    How order IDs are handled
    -------------------------
    You do NOT need to manually increment order IDs.

    Each call to `order_app.submit_market_order(...)` funnels into
    `OrderApp.place_order(...)`, which calls `ib_app.reserve_order_id()`.
    That method returns the next available orderId (from the last `nextValidId`
    callback) and increments it under a lock.

    Parameters
    ----------
    order_app : OrderApp
        An instance of `portutils.ingestion.ibkr_requests.OrderApp` that wraps
        a connected `IBApp`. This is the object that actually sends orders via
        `order_app.submit_market_order(...)`.
    target_positions_df : pd.DataFrame
        The rebalance table (typically the output of `calculate_target_positions`).
        Must contain at least:
        - a symbol column (default `symbol`)
        - a currency column (default `currency`, optional)
        - a units delta column (default `units_diff`), where:
            * positive = BUY that many units
            * negative = SELL that many units
            * zero     = no trade
    symbol_col : str
        Column name in `target_positions_df` containing the ticker/symbol.
    currency_col : str
        Column name in `target_positions_df` containing the currency. If
        missing/NaN for a row, we default to 'USD'.
    units_col : str
        Column name in `target_positions_df` containing the unit difference
        (signed). This is typically computed as value_diff / marketPrice.
    dry_run : bool
        When True, prints the intended orders but does not send them.
        This is a safety switch while iterating on the rebalance logic.
    contract_specs : dict[str, dict] | None
        Per-symbol contract overrides, same shape as ``get_equity_data`` takes:
        ``{'BARC': {'con_id': 123456, 'exchange': 'LSE', 'currency': 'GBP'}}``.
        Build one from an account snapshot with ``contract_specs_from_portfolio``.

        WHY THIS EXISTS. Without it every order is a bare symbol on STK/SMART/USD,
        because that is what ``submit_market_order`` defaults to. For a US ETF that
        is correct; for a London line it is a different instrument in a different
        currency, or an outright rejection. A symbol absent from the mapping keeps
        the old behaviour (defaults, with ``currency_col`` applied), so existing
        callers are unaffected.
    """
    submitted = []
    # Normalise once so the row loop can .get() without a None check each time.
    contract_specs = contract_specs or {}

    # Iterate row-by-row so the action and quantity can be derived from the
    # signed `units_diff`. This keeps the logic explicit and debuggable.
    # an itterows object is two values, the index and the row, we can ignore
    # the index with _ and just use the row
    for _, row in target_positions_df.iterrows():
        symbol = row.get(symbol_col)
        currency = row.get(currency_col, 'USD')
        units = row.get(units_col)

        # Guard against missing/NaN rows.
        # - If the rebalance logic produced NaNs (e.g., missing marketPrice)
        #   then we simply skip those lines here rather than submitting
        #   incorrect orders.
        if pd.isna(symbol) or pd.isna(units):
            continue

        # `units_diff` often comes out as numpy scalars (e.g., np.float64) or
        # as a float because of division/rounding earlier. We normalize to an
        # int so order quantities are valid.
        units_int = int(units)
        if units_int == 0:
            # Nothing to do for this symbol.
            continue

        # Convert the signed delta into (action, quantity).
        # - Positive means we need to increase the position => BUY
        # - Negative means we need to reduce the position   => SELL
        action = 'BUY' if units_int > 0 else 'SELL'
        quantity = abs(units_int)

        # Resolve the contract for this leg. The spec wins over currency_col where both
        # speak about currency: the spec came from the broker's own record of the position,
        # the column is whatever the caller's table happened to carry.
        spec = dict(contract_specs.get(symbol, {}))
        spec.setdefault('currency', currency if not pd.isna(currency) else 'USD')
        # A one-line human description of what is actually being sent. This string is the
        # whole point of the dry run: the old version printed only symbol + currency, which
        # is exactly the pair that looked right while the contract underneath was wrong.
        detail = (f"{spec.get('sec_type', 'STK')} {spec.get('exchange', 'SMART')} "
                  f"{spec['currency']}"
                  + (f" conId={spec['con_id']}" if spec.get('con_id') else " conId=- (by symbol)"))

        if dry_run:
            # Print exactly what would be sent. Keeping this as a single line
            # makes it easy to scan the proposed rebalance in the console.
            print(f"DRY RUN: {action} {quantity} {symbol} [{detail}]")
            submitted.append({'symbol': symbol, 'currency': spec['currency'],
                              'exchange': spec.get('exchange', 'SMART'),
                              'con_id': spec.get('con_id'), 'action': action,
                              'quantity': quantity, 'orderId': None})
            continue

        # This call reserves and increments `orderId` internally.
        # Internally:
        # - submit_market_order() creates a contract object with the specifications of the asset we are buying/selling
        # e.g. symbol, currency, exchange, etc., and then calls place_order() with that contract object
        # - in turn, it calls market_order to OrderApp.place_order() which creates the specifications of that order,
        # under ibkr's 'order' object class, importantly specifying buy, market and of what quantity,
        # - in tuurn, it calls ou own OrderApp's place_order() method with the contract object and order object
        # - in turn, the place_order() method calls calls ib_app.reserve_order_id()
        # - in turn, reserve_order_id() returns the next valid orderId and increments it so that
        # - the next time we send an order and do the same thing, we have a valid id to use
        # - in turn, once it has the valid id, it calls the IBAPi's built-in app.placeOrder() method,
        # (or rather, Eclient.placeOrder(), sinc our app object will be an instance of ECLient)
        # which sends the order to IBKR with the contract, order specifications and orderId.
        # through the TWS gateway
        # this naturally returns an order_id that ibkr assigns to the order,
        # This means you can submit multiple orders in a loop without manually
        # managing order IDs.
        # **spec carries currency and, where known, con_id / exchange / sec_type — so the
        # order names the same contract the dry run printed, not a re-resolution of the bare
        # ticker. The keys match submit_market_order's parameter names exactly.
        order_id = order_app.submit_market_order(symbol, action, quantity, **spec)

        # confirms that the order was submitted with the given specifications and the order ID that
        #  IBKR assigned to it. This is useful for tracking and debugging.
        print(f"Submitted: orderId={order_id} {action} {quantity} {symbol} [{detail}]")

        # we append this to a self-created list of submitted orders, which we can then convert to a
        # dataframe at the end. This is useful for tracking what we intended to submit, especially
        #  in dry run mode where we don't have actual order IDs from IBKR.
        submitted.append({'symbol': symbol, 'currency': spec['currency'],
                          'exchange': spec.get('exchange', 'SMART'),
                          'con_id': spec.get('con_id'), 'action': action,
                          'quantity': quantity, 'orderId': order_id})

    return pd.DataFrame(submitted)


def wait_for_order_ack(app, order_ids, timeout=15, settle=1.0, poll=0.25):
    """Block until TWS has acknowledged each submitted order — or report that it did not.

    ═══════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS — "Submitted" WAS NEVER EVIDENCE OF ANYTHING.
    ``EClient.placeOrder`` writes a message to the socket and returns. It does not
    wait, it does not confirm, and it does not raise when TWS ignores the order.
    Acceptance arrives LATER, asynchronously, through the ``openOrder`` and
    ``orderStatus`` callbacks on the reader thread.

    A caller that submits and then disconnects therefore destroys its own evidence:
    ``EClient.disconnect()`` closes the socket and stops the reader, so anything
    still buffered is discarded and any callback in flight is never read. That is
    exactly how seven orders were "Submitted" to an empty TWS Orders panel — the
    print statements only proved the calls had not raised.

    This function closes that gap. It reads the state the callbacks were already
    filling in (``app.order_status``, ``app.open_orders``) and, critically, marks
    every id TWS never mentioned as ``acknowledged=False`` so an unreceived order
    is REPORTED rather than assumed successful.
    ═══════════════════════════════════════════════════════════════════════════

    Parameters
    ----------
    app : IBApp
        The connected app the orders were placed through.
    order_ids : iterable[int]
        Order ids returned by ``place_order`` / ``submit_rebalance_orders``.
    timeout : float
        Seconds to wait for the last outstanding acknowledgement.
    settle : float
        Initial pause before the first check. The reader thread needs a scheduling
        slot to process what was just written; polling instantly would report a
        false negative on a perfectly healthy submission.
    poll : float
        Interval between checks.

    Returns
    -------
    pd.DataFrame
        One row per order id: orderId, status, filled, remaining, avgFillPrice,
        whyHeld, error, acknowledged. Never raises on a missing acknowledgement —
        the caller is expected to report it, which is the entire point.
    """
    ids = [int(i) for i in order_ids if i is not None and not pd.isna(i)]
    if not ids:
        return pd.DataFrame()

    # Give the reader thread a moment before the first look.
    time.sleep(settle)

    deadline = time.time() + timeout
    while True:
        with app.lock:
            seen = {i for i in ids
                    if i in app.order_status or i in app.open_orders
                    or i in app.req_errors or i in app.req_notices}
        if len(seen) == len(ids) or time.time() >= deadline:
            break
        time.sleep(poll)

    rows = []
    with app.lock:
        for order_id in ids:
            status = dict(app.order_status.get(order_id, {}))
            openo = dict(app.open_orders.get(order_id, {}))
            # IB delivers an order REJECTION through error(), with the ORDER ID in the
            # reqId slot — so req_errors is where a refusal shows up, not order_status.
            #
            # CAVEAT: order ids and request ids are separate counters that share one
            # numeric space and DO collide (a single run has held historical reqIds 2-4
            # alongside orderIds 1-7). Reading req_errors here is safe because we only
            # ask about ids we just submitted, in a window where no other request is in
            # flight. Never use it in the other direction to conclude a data request failed.
            err = app.req_errors.get(order_id)
            # A non-terminal message is still proof TWS saw the order. 10311 in
            # particular means "accepted, but held for manual confirmation" — an order
            # sitting in the Pending panel with a Transmit button, which is neither
            # working nor lost and must not be reported as either.
            notice = app.req_notices.get(order_id)

            # BELT AND BRACES: an order TWS reports as live was not rejected, whatever
            # message came with it. Code lists go stale — IB adds warnings faster than
            # anyone updates a constant — but the order's own status is authoritative
            # and needs no maintenance. If the two disagree, believe the status and
            # demote the message to a notice.
            live_status = str(status.get('status') or openo.get('status') or '')
            if err and live_status in _LIVE_ORDER_STATES:
                notice = notice or err
                err = None

            rows.append({
                'orderId': order_id,
                'symbol': openo.get('symbol'),
                'action': openo.get('action'),
                'quantity': openo.get('totalQuantity'),
                'status': status.get('status') or openo.get('status'),
                'filled': status.get('filled'),
                'remaining': status.get('remaining'),
                'avgFillPrice': status.get('avgFillPrice'),
                'whyHeld': status.get('whyHeld'),
                'error': f"{err[0]} {err[1]}" if err else None,
                'notice': f"{notice[0]} {notice[1]}" if notice else None,
                # The column that matters. False = TWS never said a word about this
                # order, so assume it did not arrive. A notice counts: it is TWS
                # talking about this order id, which silence is not.
                'acknowledged': bool(status or openo or err or notice),
            })
    return pd.DataFrame(rows)


def _cancel_kwargs(func):
    """Build the trailing argument ibapi's cancel calls expect on THIS build.

    ibapi changed these signatures: 10.47 takes an ``OrderCancel`` object
    (``cancelOrder(orderId, orderCancel)`` / ``reqGlobalCancel(orderCancel)``),
    older builds took a bare id, or an id plus a ``manualOrderCancelTime`` string.
    Chosen by inspecting the actual signature rather than comparing a version
    string — the version is a proxy for the thing we can just look at directly,
    and a proxy is what breaks on the next release.
    """
    import inspect
    try:
        params = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        return []
    # Drop `self` when present (unbound) — bound methods already exclude it.
    extras = [p for p in params if p.name not in ('self', 'orderId')]
    if not extras:
        return []
    if extras[0].name == 'orderCancel':
        from ibapi.order_cancel import OrderCancel
        return [OrderCancel()]
    # Legacy `manualOrderCancelTime`: empty string means "cancel now".
    return ['']


def cancel_order(app, order_id):
    """Cancel one working order by id.

    The other half of placing an order, and the half this repo did not have: there
    was no ``cancelOrder`` call anywhere, so a mistaken live order could be watched
    but not stopped.
    """
    args = _cancel_kwargs(app.cancelOrder)
    app.cancelOrder(int(order_id), *args)
    print(f"Cancel requested for orderId={order_id}")
    return int(order_id)


def cancel_all_orders(app):
    """THE PANIC BUTTON — cancel every open order on the account.

    ``reqGlobalCancel`` covers orders placed by ANY client id, not just this
    session's, which is what you want when something has gone wrong and you are not
    certain what is working. Cancellation is a request like any other: confirm the
    result with ``get_open_orders_data`` rather than assuming it took effect.
    """
    args = _cancel_kwargs(app.reqGlobalCancel)
    app.reqGlobalCancel(*args)
    print("Global cancel requested for ALL open orders on this account.")


def get_executions_data(app, days_back=7, symbols=None, sec_type='', side='',
                        client_id=0, timeout=15):
    """Fetch recent executions (fills) with their commissions, as a DataFrame.

    ⚠ THE 7-DAY CEILING — read this before trusting an empty result.
    TWS serves only a SHORT recent window of executions: today's fills by default, and at
    most roughly the **last 7 days** even with an explicit ExecutionFilter time. This is a
    TWS limitation, not a bug here and not something a bigger `days_back` can defeat. An
    empty frame therefore means "no fills in the window TWS will serve", NOT "no trades
    ever" — do not build a P&L history on the assumption that it is the latter.

    For genuine full history you need IBKR's Flex Web Service (a query + token created in
    Account Management) or a manually downloaded activity statement. Neither is available
    through this API connection.

    Parameters
    ----------
    app : IBApp
        A connected app instance.
    days_back : int
        How far back to ask for. Values beyond ~7 are accepted but TWS will simply return
        what it has; the request does not fail, it just returns less than you asked for.
    symbols : str | list[str] | None
        Optional symbol filter. A list is applied client-side after the request, because
        ExecutionFilter carries only ONE symbol — asking for several server-side would
        need one request per symbol.
    sec_type, side : str
        Passed straight through to ExecutionFilter ('STK', 'BUY'/'SELL'); '' means no filter.
    client_id : int
        0 = executions from every client id on the account. Any other value narrows to
        orders placed by that client, which is rarely what you want when auditing a book.

    Returns
    -------
    pd.DataFrame
        One row per fill, with the commission columns joined on execId. Empty (with no
        columns) when TWS returns nothing for the window.
    """
    # --- Reset the destination stores -------------------------------------
    # Both executions and commissions are cleared: a stale commission row from a previous
    # call would otherwise join onto this call's fills and misstate their cost.
    app.executions_event.clear()
    with app.lock:
        app.executions = []
        app.commissions = {}

    # --- Build the filter -------------------------------------------------
    exec_filter = ExecutionFilter()
    # IB expects "yyyymmdd HH:MM:SS" for the filter's start time. We anchor the window at
    # midnight `days_back` days ago so a request made mid-afternoon still covers whole days
    # rather than a ragged part-day at the far end.
    start = (datetime.now() - timedelta(days=days_back)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    exec_filter.time = start.strftime('%Y%m%d %H:%M:%S')
    exec_filter.clientId = client_id
    if sec_type:
        exec_filter.secType = sec_type
    if side:
        exec_filter.side = side
    # ExecutionFilter holds a single symbol; only push it server-side when exactly one was
    # asked for, otherwise filter client-side below.
    if isinstance(symbols, str):
        exec_filter.symbol = symbols

    # --- Fire the request / wait for execDetailsEnd -----------------------
    app.reqExecutions(app.next_req_id(), exec_filter)
    _wait_for(app.executions_event, timeout, f'executions since {exec_filter.time}')

    # --- Join fills to their commissions ----------------------------------
    with app.lock:
        rows = [dict(r) for r in app.executions]
        commissions = dict(app.commissions)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Left-join so a fill whose commissionReport has not arrived still appears, with a NaN
    # commission rather than being dropped — a missing cost must be visible, not silent.
    if commissions:
        df = df.merge(pd.DataFrame(list(commissions.values())), on='execId',
                      how='left', suffixes=('', '_comm'))

    # Multi-symbol filtering happens here, since ExecutionFilter could not carry the list.
    if symbols is not None and not isinstance(symbols, str):
        df = df[df['symbol'].isin(list(symbols))]

    # IB stamps execution times as "yyyymmdd HH:MM:SS" (sometimes with a trailing timezone).
    # Parse to real timestamps so downstream code can sort and index by them; anything
    # unparseable becomes NaT rather than raising, and stays visible in the frame.
    if 'time' in df.columns:
        df['ts'] = pd.to_datetime(df['time'].str.split(' ').str[:2].str.join(' '),
                                  format='%Y%m%d %H:%M:%S', errors='coerce')
        df = df.sort_values('ts').reset_index(drop=True)

    return df


def get_open_orders_data(app, timeout=15, all_clients=False):
    """Fetch current open orders using an existing connected IBApp."""
    # --- Reset the destination store --------------------------------------
    # We clear only the open-order snapshot itself; order_status is left intact
    # because it is also useful as the rolling live status store.
    app.open_orders_event.clear()
    with app.lock:
        app.open_orders = {}

    # --- Fire the request / wait for callbacks ----------------------------
    if all_clients:
        app.reqAllOpenOrders()
    else:
        app.reqOpenOrders()

    _wait_for(app.open_orders_event, timeout, 'open orders')

    # --- Merge order definitions with latest statuses ---------------------
    rows = []
    with app.lock:
        for order_id, order_row in app.open_orders.items():
            merged = dict(order_row)
            merged.update(app.order_status.get(order_id, {}))
            rows.append(merged)

    return pd.DataFrame(rows)


def get_pnl_data(app, account, model_code='', timeout=10, keep_subscription=False):
    """Fetch one account-level PnL snapshot using an existing connected IBApp."""
    # --- Allocate request-specific state ----------------------------------
    req_id = app.next_req_id()
    event = threading.Event()

    with app.lock:
        app._pnl_events[req_id] = event

    try:
        # --- Subscribe / wait for the first snapshot ----------------------
        app.reqPnL(req_id, account, model_code)
        _wait_for(event, timeout, 'PnL')

        # --- Return the collected snapshot --------------------------------
        with app.lock:
            snapshot = dict(app.account_pnl.get(req_id, {}))
        return snapshot

    finally:
        # PnL requests are subscriptions, so cancel unless the caller
        # explicitly wants to keep the stream alive.
        if not keep_subscription:
            try:
                app.cancelPnL(req_id)
            except Exception:
                pass

        with app.lock:
            if not keep_subscription:
                app._pnl_events.pop(req_id, None)


def get_pnl_single_data(
    app,
    account,
    con_id,
    model_code='',
    timeout=10,
    keep_subscription=False,
):
    """Fetch one contract-level PnL snapshot using an existing connected IBApp."""
    # --- Allocate request-specific state ----------------------------------
    req_id = app.next_req_id()
    event = threading.Event()

    with app.lock:
        app._pnl_events[req_id] = event

    try:
        # --- Subscribe / wait for the first snapshot ----------------------
        app.reqPnLSingle(req_id, account, model_code, con_id)
        _wait_for(event, timeout, f'PnL for conId {con_id}')

        # --- Return the collected snapshot --------------------------------
        with app.lock:
            snapshot = dict(app.contract_pnl.get(req_id, {}))
        return snapshot

    finally:
        if not keep_subscription:
            try:
                app.cancelPnLSingle(req_id)
            except Exception:
                pass

        with app.lock:
            if not keep_subscription:
                app._pnl_events.pop(req_id, None)


# ═══════════════════════════════════════════════════════════════════════════
# Submit and manage orders and trades
# ═══════════════════════════════════════════════════════════════════════════

class OrderApp:
    """Trading helper that operates on an existing connected IBApp instance.

    This is intentionally *not* a subclass of IBApp.  It is a thin wrapper
    around a live app instance and issues order/account requests through that
    shared connection.
    """

    def __init__(self, app):
        if not app.connected:
            raise ConnectionError(
                'IBApp is not connected — call app.start() before passing it to OrderApp.'
            )
        self.app = app
        print(f'OrderApp ready (account(s): {", ".join(app.managed_accounts) or "unknown"})')

    def place_order(self, contract_obj, order):
        """Submit an order through the wrapped IBApp and return its order ID."""
        # Reserve the next valid order ID from the shared app so we remain in
        # sync with the TWS session across every order helper.
        order_id = self.app.reserve_order_id()
        # note that the above returns the next valid order ID but also increments
        # the app's internal counter and next_order_id to be this same order_id returned
        # to us just now +1:
        # this is so the next call to reserve_order_id() will return a different ID, 
        # which is important for keeping our orders in sync with TWS/Gateway.

        # Place the order on the live TWS connection, using the built-in 
        # EClient method.  
        # The app's callbacks will update its internal state as the order
        # is processed.
        self.app.placeOrder(order_id, contract_obj, order)
        return order_id

    def submit_market_order(
        self,
        symbol,
        action,
        quantity,
        sec_type='STK',
        exchange='SMART',
        currency='USD',
        primary_exchange=None,
        con_id=None,
    ):
        """Place a market order for the specified contract."""
        # Build the IB contract first so the order points at the right
        # instrument.
        #
        # con_id is the important one for anything not US-SMART. The defaults here
        # (STK/SMART/USD) describe a US listing, and a bare symbol is AMBIGUOUS across
        # venues — the same ticker can name a London line and a US one. On a historical
        # request that ambiguity costs an error 200; on an ORDER it can mean buying the
        # wrong instrument in the wrong currency. Callers holding a position already have
        # its conId from the account snapshot (contract_specs_from_portfolio) and should
        # pass it rather than let TWS re-resolve the name.
        contract_obj = contract(
            symbol=symbol,
            sec_type=sec_type,
            exchange=exchange,
            currency=currency,
            primary_exchange=primary_exchange,
            con_id=con_id,
        )

        # Build a market order and submit it through the shared app.
        order = market_order(action, quantity)
        return self.place_order(contract_obj, order)

    def submit_limit_order(
        self,
        symbol,
        action,
        quantity,
        limit_price,
        sec_type='STK',
        exchange='SMART',
        currency='USD',
        primary_exchange=None,
        con_id=None,
    ):
        """Place a limit order for the specified contract."""
        # Build the IB contract first so the order points at the right
        # instrument. See the con_id note in submit_market_order — it matters
        # identically here, and a limit price is quoted in the CONTRACT's currency,
        # so naming the wrong listing also silently changes what the price means.
        contract_obj = contract(
            symbol=symbol,
            sec_type=sec_type,
            exchange=exchange,
            currency=currency,
            primary_exchange=primary_exchange,
            con_id=con_id,
        )

        # Build a limit order and submit it through the shared app.
        order = limit_order(action, quantity, limit_price)
        return self.place_order(contract_obj, order)

    def trading_dashboard(self, account=None, tags=DEFAULT_TAGS, timeout=30):
        """Return a combined view of account, portfolio, orders, and PnL."""
        # --- Pull the core account snapshots -------------------------------
        account_df = get_account_data(tags=tags, group='All', app=self.app)
        positions_df = get_positions_data(self.app, timeout=timeout)
        open_orders_df = get_open_orders_data(self.app, timeout=timeout)

        # --- Decide which account to use for portfolio / PnL --------------
        chosen_account = account
        if chosen_account is None and not account_df.empty:
            chosen_account = str(account_df.iloc[0]['account'])

        # --- Pull account-specific portfolio / PnL if available -----------
        portfolio_df = pd.DataFrame()
        pnl_snapshot = None

        if chosen_account:
            try:
                portfolio_df = get_account_updates(self.app, chosen_account, timeout=min(timeout, 15))
            except TimeoutError:
                portfolio_df = pd.DataFrame()

            try:
                pnl_snapshot = get_pnl_data(self.app, chosen_account, timeout=min(timeout, 10))
            except TimeoutError:
                pnl_snapshot = None

        # --- Return the combined snapshot ---------------------------------
        return {
            'account_summary': account_df,
            'positions': positions_df,
            'portfolio': portfolio_df,
            'open_orders': open_orders_df,
            'pnl': pnl_snapshot,
        }
