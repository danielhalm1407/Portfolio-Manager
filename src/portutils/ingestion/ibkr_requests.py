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
from datetime import datetime  # stamp each emitted tick so on_tick consumers can bar-aggregate
from threading import Thread  # connect_ib spins app.run() on a daemon reader thread

import pandas as pd
from ibapi.client import EClient    # outgoing request side of the IB API
from ibapi.wrapper import EWrapper  # incoming callback side of the IB API
from ibapi.contract import Contract
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
):
    """Build a Contract object for the requested instrument."""
    c = Contract()
    c.symbol = symbol
    c.secType = sec_type
    c.exchange = exchange
    c.currency = currency

    if primary_exchange:
        c.primaryExchange = primary_exchange
    if last_trade_date_or_contract_month:
        c.lastTradeDateOrContractMonth = last_trade_date_or_contract_month

    return c


def market_order(action, quantity):
    """
    Builds a basic market order, in the standard IBKR Order
    object format
    """
    order = Order()
    order.action = action.upper()
    order.orderType = 'MKT'
    order.totalQuantity = quantity
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
        self.account_pnl = {}
        self.contract_pnl = {}

        # Shared completion events for request types that IBKR finishes with an
        # "...End" callback.
        self.positions_event = threading.Event()
        self.open_orders_event = threading.Event()
        self.account_updates_event = threading.Event()

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
        errorCode = None
        errorString = ""
        for a in args:
            if isinstance(a, int) and errorCode is None:
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
                'symbol': contract_obj.symbol,
                'conId': contract_obj.conId,
                'side': execution.side,
                'shares': execution.shares,
                'price': execution.price,
                'time': execution.time,
                'orderId': execution.orderId,
                'permId': execution.permId,
            })

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
    skip_existing=True,
    client_id=123,
    app=None,
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
        Directory in which to write ``stock_data_{sym}.csv`` files.
        If None, files are written to the current working directory.
    skip_existing : bool
        When True, skip any ticker whose CSV already exists in *output_dir*.
    app : IBApp, optional
        Existing connected app instance.  If omitted, this function will create
        its own temporary IBApp connection and close it on exit.

    Returns
    -------
    dict[str, DataFrame]
        Mapping of symbol -> OHLCV DataFrame (with a 'return' column).
    """
    # --- Normalise input ---------------------------------------------------
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    if isinstance(symbols, str):
        symbols = [symbols]  # accept a single ticker string

    # --- Skip tickers that already have saved CSVs -------------------------
    save_dir = pathlib.Path(output_dir) if output_dir is not None else pathlib.Path('.')
    save_dir.mkdir(parents=True, exist_ok=True)

    # check if we already have a saved CSV for each ticker
    if skip_existing:
        needed = []
        for sym in symbols:
            prefixed = save_dir / f'stock_data_{sym}.csv'
            bare = save_dir / f'{sym}.csv'
            if prefixed.exists() or bare.exists():
                print(f"Skipping {sym}: CSV already exists in {save_dir}")
            else:
                needed.append(sym)
        symbols = needed

    if not symbols:
        print("All tickers already have saved data — nothing to fetch.")
        return {}

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

            # Build a Contract object — IBKR's way of specifying an instrument.
            req_contract = contract(sym)

            # Send the request.  The app's callback methods do the collection;
            # this outer function just coordinates the workflow and assembles
            # the final DataFrames.
            app.reqHistoricalData(
                reqId=req_id,
                contract=req_contract,
                endDateTime=end_time,      # end of the window
                durationStr=duration,      # how far back from endDateTime
                barSizeSetting=bar_size,   # bar granularity
                whatToShow='TRADES',       # price basis
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
        timeout_per_symbol = 30
        for req_id, event in events.items():
            _wait_for(event, timeout_per_symbol, f'historical data for {id_to_symbol[req_id]}')

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
                print(f"Warning: no data returned for {sym}")
                results[sym] = pd.DataFrame()
                continue

            df = pd.DataFrame(bars)
            df['return'] = df['close'].pct_change()  # simple bar-on-bar return
            df = df.dropna(subset=['return'])        # drop first row (NaN)
            df = df['datetime,open,high,low,close,volume,return'.split(',')]
            df.to_csv(save_dir / f'stock_data_{sym}.csv', index=False)
            results[sym] = df

            # Clean up this request's temporary state now that we are done.
            app.cancelHistoricalData(req_id)
            with app.lock:
                app._hist_events.pop(req_id, None)

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
    ):
        """Place a market order for the specified contract."""
        # Build the IB contract first so the order points at the right
        # instrument.
        contract_obj = contract(
            symbol=symbol,
            sec_type=sec_type,
            exchange=exchange,
            currency=currency,
            primary_exchange=primary_exchange,
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
    ):
        """Place a limit order for the specified contract."""
        # Build the IB contract first so the order points at the right
        # instrument.
        contract_obj = contract(
            symbol=symbol,
            sec_type=sec_type,
            exchange=exchange,
            currency=currency,
            primary_exchange=primary_exchange,
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
