"""IBKR connection plumbing — base app class and connect/disconnect helpers.

Architecture note — why a base class?
--------------------------------------
The IB Python API (ibapi) uses a dual-class design:

  • EClient  — the *outgoing* side: methods you call to send requests
               (reqHistoricalData, reqAccountSummary, etc.).
  • EWrapper — the *incoming* side: callbacks IBKR fires when data arrives
               (historicalData, accountSummary, error, etc.).

You must combine both into a single object so that (a) you can *send*
requests and (b) the same object *receives* the asynchronous responses.
IBKRApp inherits from both and wires them together in __init__.

Each request type needs its own set of EWrapper callback overrides (e.g.
historicalData vs. accountSummary).  Rather than cramming every callback into
one mega-class, we keep IBKRApp thin — just the shared handshake / error
plumbing — and let each request module in ibkr_requests.py provide a small
subclass with only the callbacks it needs.  This keeps each request self-
contained and easy to extend.

connect_ib / disconnect_ib are module-level helpers so every subclass reuses
the same connection lifecycle logic.
"""
import time
from threading import Thread

from ibapi.client import EClient
from ibapi.wrapper import EWrapper


# ═══════════════════════════════════════════════════════════════════════════
# Base app class — shared by all request-specific subclasses
# ═══════════════════════════════════════════════════════════════════════════

class IBKRApp(EWrapper, EClient):
    """Base bridge class for the IB Python API.

    Subclasses override specific EWrapper callbacks (e.g. historicalData,
    accountSummary) to collect the data they care about.  Shared behaviour
    — handshake detection, error filtering, the ``data`` / ``finished``
    flags — lives here.
    """

    def __init__(self):
        # EClient.__init__ needs the wrapper instance so it can route incoming
        # messages to the right callbacks.  Because IBKRApp is *both* client
        # and wrapper (dual inheritance), we pass `self` for both roles.
        EClient.__init__(self, self)

        # Subclasses append request-specific dicts here.
        # _HistDataApp overrides this with a dict keyed by reqId instead.
        self.data = []

        # Flipped to True by the relevant *End() callback in each subclass
        # (e.g. historicalDataEnd, accountSummaryEnd).  The get_*() entry
        # points poll on this flag to know when all data has arrived.
        self.finished = False

        # Flipped to True by nextValidId() — the first callback IBKR fires
        # after a successful TCP + handshake connection.
        self.connected = False

    # --- Handshake callback ------------------------------------------------

    def nextValidId(self, orderId):
        """Called by IBKR immediately after a successful connection handshake.

        The orderId argument is the next valid order ID (useful for placing
        trades, but we only use this callback as a "connection ready" signal).
        connect_ib() polls self.connected before letting callers send requests.
        """
        self.connected = True

    # --- Error / info callback ---------------------------------------------

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
        """Central error/info handler.

        IBKR funnels ALL diagnostic messages through this single callback —
        genuine errors, warnings, and routine status pings alike.  We filter
        out harmless info codes so they don't clutter the console.
        """
        # Codes 2104 ("Market data farm connection is OK"), 2106 ("HMDS data
        # farm connection is OK"), and 2158 ("Sec-def data farm connection is
        # OK") are routine connection-status pings, not errors.
        info_codes = {2104, 2106, 2158}
        if errorCode in info_codes:
            return
        print(f'ERROR {reqId} {errorCode} {errorString}')


# ═══════════════════════════════════════════════════════════════════════════
# Connection lifecycle helpers
# ═══════════════════════════════════════════════════════════════════════════

def connect_ib(app, host='127.0.0.1', port=7497, client_id=123):
    """Open the IBKR socket on a daemon thread and block until the handshake
    completes.

    Why a background thread?
    ------------------------
    app.run() enters an infinite read-loop that processes incoming IBKR
    messages.  If we ran it on the main thread it would block forever, so we
    spin it off on a daemon thread.  The main thread is then free to call
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
