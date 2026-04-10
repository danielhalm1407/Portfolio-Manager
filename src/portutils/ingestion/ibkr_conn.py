"""IBKR connection plumbing — base app class and connect/disconnect helpers.

Every IBKR request module subclasses IBKRApp and reuses connect_ib / disconnect_ib
so connection logic is defined once.
"""
import time
from threading import Thread

from ibapi.client import EClient
from ibapi.wrapper import EWrapper


class IBKRApp(EWrapper, EClient):
    """Base bridge class for the IB Python API.

    Subclasses override specific EWrapper callbacks (e.g. historicalData,
    accountSummary) to collect the data they care about.  Shared behaviour
    — handshake detection, error filtering, the ``data`` / ``finished``
    flags — lives here.
    """

    def __init__(self):
        # EClient.__init__ requires a reference to the wrapper instance so it
        # knows where to route incoming messages.  Since IBKRApp is both client
        # and wrapper, we pass *self* for both roles.
        EClient.__init__(self, self)
        self.data = []           # subclasses append request-specific dicts here
        self.finished = False    # flipped to True by the relevant *End() callback
        self.connected = False   # flipped to True by nextValidId()

    def nextValidId(self, orderId):
        # IBKR fires this callback immediately after a successful connection
        # handshake.  connect_ib() polls on this flag before sending requests.
        self.connected = True

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
        # IBKR routes all errors, warnings, and info messages through this
        # single callback.  Codes 2104, 2106, 2158 are routine connection-
        # status notifications — not real errors.
        info_codes = {2104, 2106, 2158}
        if errorCode in info_codes:
            return
        print(f'ERROR {reqId} {errorCode} {errorString}')


# ---------------------------------------------------------------------------
# Connection lifecycle helpers
# ---------------------------------------------------------------------------

def connect_ib(app, host='127.0.0.1', port=7497, client_id=123):
    """Open the IBKR socket on a daemon thread and block until the handshake completes.

    Raises ``ConnectionError`` if TWS/Gateway doesn't respond within 10 s.
    """
    def _run():
        try:
            app.connect(host, port, client_id)
            app.run()  # blocks, processing incoming IBKR messages
        except Exception as e:
            print(f"Connection error: {e}")

    Thread(target=_run, daemon=True).start()

    # Poll up to 10 s (100 × 0.1 s) for nextValidId() + valid server version.
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
    """Close the TCP socket and stop the background event loop."""
    try:
        app.disconnect()
        print("Disconnected from IBKR")
    except Exception as e:
        print(f"Disconnect error: {e}")
