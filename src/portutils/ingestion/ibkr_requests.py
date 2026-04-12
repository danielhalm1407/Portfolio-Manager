"""IBKR data request functions — each adds a thin subclass of IBKRApp
with the callbacks specific to that request type, plus a public
get_*() entry point that pipeline scripts can import.

Why a separate App subclass per request type?
---------------------------------------------
The IB API is callback-driven: you send a request (e.g. reqHistoricalData)
and IBKR asynchronously fires specific EWrapper callbacks with the results
(e.g. historicalData, historicalDataEnd).  Different requests trigger
*different* callbacks and return *different* data shapes, so each request
type needs its own set of callback overrides.

We *could* put every callback on a single class, but that creates a
monolithic object whose state mixes historical bars, account rows, position
rows, etc.  Instead, each request type gets a small, focused subclass of
IBKRApp that only overrides the callbacks it cares about and stores results
in the shape that makes sense for that data (e.g. a dict keyed by reqId for
multi-symbol historical data, vs. a flat list for account summary rows).

Callers also need separate *connections* (separate App instances) because
each App object owns a single TCP socket and event loop.  The IB API
expects one ``app.run()`` loop per connection; you cannot multiplex
unrelated request types on the same socket without carefully interleaving
callbacks.  Using separate App instances with distinct client_id values is
cleaner and avoids callback collision.
"""
import pathlib
import time

import pandas as pd
from ibapi.contract import Contract

from .ibkr_conn import IBKRApp, connect_ib, disconnect_ib


# ═══════════════════════════════════════════════════════════════════════════
# Historical equity data
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_SYMBOLS = ['STX', 'LNG', 'JPM', 'FRES', 'RTX']


class _HistDataApp(IBKRApp):
    """Collects OHLCV bars from reqHistoricalData(), keyed by reqId.

    Overrides IBKRApp.data with a dict (not a list) because we fire one
    request per symbol, each with its own reqId, and need to keep the
    bars separated so we can build per-symbol DataFrames at the end.
    """

    def __init__(self):
        super().__init__()
        self.data = {}       # {reqId: [bar_dict, ...]}  — override base list
        self.pending = set() # reqIds still awaiting historicalDataEnd()

    # --- EWrapper callback: one bar of historical data ---------------------

    def historicalData(self, reqId, bar):
        """Called once per bar.  IBKR delivers bars oldest-first."""
        self.data.setdefault(reqId, []).append({
            'datetime' : bar.date,
            'open'     : bar.open,
            'close'    : bar.close,
            'high'     : bar.high,
            'low'      : bar.low,
            'volume'   : bar.volume,
        })

    # --- EWrapper callback: all bars for this reqId have been sent ---------

    def historicalDataEnd(self, reqId, start, end):
        """Called after the last bar for a given reqId.

        We track outstanding reqIds in self.pending; once the set is empty
        every symbol has finished and the main thread can stop polling.
        """
        self.pending.discard(reqId)  # this symbol is done
        if not self.pending:
            self.finished = True     # signal the polling loop to stop


def get_equity_data(
    symbols=None,
    host='127.0.0.1',
    port=7497,
    duration='1 Y',
    bar_size='1 day',
    end_date='',
    output_dir=None,
    skip_existing=True,
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
        If None, files are written to the current working directory
        (backwards-compatible behaviour).
    skip_existing : bool
        When True (default), skip any ticker whose CSV already exists
        in *output_dir*.  Set to False to re-download and overwrite
        (e.g. when changing *duration* or *bar_size*).

    Returns
    -------
    dict[str, DataFrame]
        Mapping of symbol -> OHLCV DataFrame (with a 'return' column).
    """
    # --- Normalise input ---------------------------------------------------
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    if isinstance(symbols, str):
        symbols = [symbols]          # accept a single ticker string

    # --- Skip tickers that already have saved CSVs -------------------------
    save_dir = pathlib.Path(output_dir) if output_dir is not None else pathlib.Path('.')
    save_dir.mkdir(parents=True, exist_ok=True)

    # check if we already have a saved CSV for each ticker, 
    if skip_existing:
        needed = []
        for sym in symbols:
            prefixed = save_dir / f'stock_data_{sym}.csv'
            bare     = save_dir / f'{sym}.csv'
            if prefixed.exists() or bare.exists():
                print(f"Skipping {sym}: CSV already exists in {save_dir}")
            else:
                needed.append(sym)
        symbols = needed

    if not symbols:
        print("All tickers already have saved data — nothing to fetch.")
        return {}

    # --- Connect -----------------------------------------------------------
    # Each get_*() function creates its own App subclass and its own
    # connection because the IB API event loop (app.run()) is per-socket.
    # See module docstring for the full rationale.
    app = _HistDataApp()
    connect_ib(app, host, port)

    try:
        # Default to "now" in UTC if no end_date was supplied.
        end_time = end_date or time.strftime('%Y%m%d-%H:%M:%S', time.gmtime())

        # Map reqId -> symbol so we can label DataFrames after collection.
        id_to_symbol = {}

        # --- Fire one request per symbol -----------------------------------
        for i, sym in enumerate(symbols):
            req_id = i + 1                     # reqIds start at 1
            id_to_symbol[req_id] = sym
            app.pending.add(req_id)            # track outstanding requests

            # Build a Contract object — IBKR's way of specifying an instrument.
            contract = Contract()
            contract.symbol   = sym            # e.g. 'AAPL'
            contract.secType  = 'STK'          # stock (vs. OPT, FUT, etc.)
            contract.exchange = 'SMART'        # IBKR's smart-routing engine
            contract.currency = 'USD'

            # Send the request.  IBKR will respond asynchronously via the
            # historicalData() and historicalDataEnd() callbacks above.
            app.reqHistoricalData(
                reqId=req_id,
                contract=contract,
                endDateTime=end_time,          # end of the window
                durationStr=duration,          # how far back from endDateTime
                barSizeSetting=bar_size,       # bar granularity
                whatToShow='TRADES',           # price basis (vs. MIDPOINT, BID, ASK)
                useRTH=1,                      # 1 = regular trading hours only
                formatDate=1,                  # 1 = human-readable dates
                keepUpToDate=0,                # 0 = one-shot, no streaming
                chartOptions=[],               # reserved by IBKR, pass empty
            )

            # Respect IBKR pacing: ~0.5 s between requests avoids throttling.
            # IBKR enforces a rate limit of ~60 hist-data requests per 10 min.
            if i < len(symbols) - 1:
                time.sleep(0.5)

        # --- Wait for all symbols to finish --------------------------------
        # Generous timeout: 30 s per symbol, minimum 60 s.
        timeout = max(60, 30 * len(symbols))
        for _ in range(timeout * 2):           # each tick is 0.5 s
            if app.finished:
                break
            time.sleep(0.5)

        if not app.finished:
            still_pending = [id_to_symbol[r] for r in app.pending]
            print(f"Warning: timed out waiting for: {still_pending}")

        # --- Build one DataFrame per symbol --------------------------------
        results = {}
        for req_id, sym in id_to_symbol.items():
            bars = app.data.get(req_id, [])
            if not bars:
                print(f"Warning: no data returned for {sym}")
                continue
            df = pd.DataFrame(bars)
            df['return'] = df['close'].pct_change()  # simple daily return
            df = df.dropna(subset=['return'])          # drop first row (NaN)
            df = df['datetime,open,high,low,close,volume,return'.split(',')]
            df.to_csv(save_dir / f'stock_data_{sym}.csv', index=False)
            results[sym] = df

        return results

    finally:
        disconnect_ib(app)


# ═══════════════════════════════════════════════════════════════════════════
# Account summary
# ═══════════════════════════════════════════════════════════════════════════

class _AccountApp(IBKRApp):
    """Collects tag/value rows from reqAccountSummary().

    Uses the base class's self.data list (a flat list of dicts) because
    account summary returns one row per tag — no need to key by reqId.
    """

    # --- EWrapper callback: one tag/value pair -----------------------------

    def accountSummary(self, reqId, account, tag, value, currency):
        """Called once per tag.  IBKR delivers each requested tag as a
        separate callback invocation with its current value."""
        self.data.append({
            'account'  : account,
            'tag'      : tag,
            'value'    : value,
            'currency' : currency,
        })

    # --- EWrapper callback: all tags have been sent ------------------------

    def accountSummaryEnd(self, reqId):
        """Called after the last tag for this reqId."""
        self.finished = True


# The tags parameter for reqAccountSummary() must be a single comma-separated
# STRING — not a list.  This is explicitly typed as `tags: str` in the ibapi
# source (ibapi/client.py, line ~1609) and documented as:
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
])


def get_account_data(tags=DEFAULT_TAGS, group='All', host='127.0.0.1', port=7497):
    """Fetch account summary for *tags* and return a DataFrame.

    Parameters
    ----------
    tags : str
        A **comma-separated string** of account tag names (NOT a list).
        The IBKR API (ibapi/client.py reqAccountSummary) explicitly requires
        ``tags: str``.  Passing a Python list will break serialisation.
        Defaults to DEFAULT_TAGS (pre-joined above).
    group : str
        Account group.  'All' returns data for every linked account.
    """
    # Create a fresh connection with a *different* client_id (124) than the
    # default (123) used by get_equity_data().  IBKR only allows one active
    # connection per client_id — if a historical-data connection is already
    # open on client_id 123, opening another on the same ID would kick the
    # first one off.  Using distinct IDs lets both run concurrently.
    app = _AccountApp()
    connect_ib(app, host, port, client_id=124)

    try:
        # Send the account summary request.  IBKR will respond via the
        # accountSummary() and accountSummaryEnd() callbacks above.
        app.reqAccountSummary(
            reqId=9001,              # arbitrary unique ID for this request
            groupName=group,         # 'All' = every linked account
            tags=tags,               # comma-separated STRING of tag names
        )

        # Poll until finished or 30 s timeout (60 × 0.5 s).
        for _ in range(60):
            if app.finished:
                break
            time.sleep(0.5)

        if not app.finished:
            print("Warning: account summary request timed out")

        # Build a DataFrame from the collected rows.
        df = pd.DataFrame(app.data)
        if not df.empty:
            # Try to convert value strings to numbers where possible,
            # keeping non-numeric values (like account type) as strings.
            df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(df['value'])
        return df

    finally:
        disconnect_ib(app)
