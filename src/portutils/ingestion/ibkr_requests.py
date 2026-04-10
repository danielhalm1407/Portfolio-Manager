"""IBKR data request functions — each adds a thin subclass of IBKRApp
with the callbacks specific to that request type, plus a public
get_*() entry point that pipeline scripts can import.
"""
import time

import pandas as pd
from ibapi.contract import Contract

from .ibkr_conn import IBKRApp, connect_ib, disconnect_ib


# ---------------------------------------------------------------------------
# Historical equity data
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS = ['STX', 'LNG', 'JPM', 'FRES', 'RTX']


class _HistDataApp(IBKRApp):
    """Collects OHLCV bars from reqHistoricalData(), keyed by reqId."""

    def __init__(self):
        super().__init__()
        self.data = {}       # {reqId: [bar_dict, ...]}
        self.pending = set() # reqIds still awaiting historicalDataEnd()

    def historicalData(self, reqId, bar):
        self.data.setdefault(reqId, []).append({
            'datetime' : bar.date,
            'open'     : bar.open,
            'close'    : bar.close,
            'high'     : bar.high,
            'low'      : bar.low,
            'volume'   : bar.volume,
        })

    def historicalDataEnd(self, reqId, start, end):
        self.pending.discard(reqId)
        if not self.pending:
            self.finished = True


def get_equity_data(
    symbols=None,
    host='127.0.0.1',
    port=7497,
    duration='1 Y',
    bar_size='1 day',
    end_date='',
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

    Returns
    -------
    dict[str, DataFrame]
        Mapping of symbol -> OHLCV DataFrame (with a 'return' column).
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    if isinstance(symbols, str):
        symbols = [symbols]

    app = _HistDataApp()
    connect_ib(app, host, port)

    try:
        end_time = end_date or time.strftime('%Y%m%d-%H:%M:%S', time.gmtime())

        # Map reqId -> symbol so we can label DataFrames after collection.
        id_to_symbol = {}

        for i, sym in enumerate(symbols):
            req_id = i + 1
            id_to_symbol[req_id] = sym
            app.pending.add(req_id)

            contract = Contract()
            contract.symbol = sym
            contract.secType = 'STK'
            contract.exchange = 'SMART'
            contract.currency = 'USD'

            app.reqHistoricalData(
                reqId=req_id,
                contract=contract,
                endDateTime=end_time,
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='TRADES',
                useRTH=1,
                formatDate=1,
                keepUpToDate=0,
                chartOptions=[],
            )

            # Respect IBKR pacing: ~0.5 s between requests avoids throttling.
            if i < len(symbols) - 1:
                time.sleep(0.5)

        # Wait for all symbols to finish (timeout: 60 s per symbol, generous).
        timeout = max(60, 30 * len(symbols))
        for _ in range(timeout * 2):
            if app.finished:
                break
            time.sleep(0.5)

        if not app.finished:
            still_pending = [id_to_symbol[r] for r in app.pending]
            print(f"Warning: timed out waiting for: {still_pending}")

        # Build one DataFrame per symbol.
        results = {}
        for req_id, sym in id_to_symbol.items():
            bars = app.data.get(req_id, [])
            if not bars:
                print(f"Warning: no data returned for {sym}")
                continue
            df = pd.DataFrame(bars)
            df['return'] = df['close'].pct_change()
            df = df.dropna(subset=['return'])
            df = df['datetime,open,high,low,close,volume,return'.split(',')]
            df.to_csv(f'stock_data_{sym}.csv', index=False)
            results[sym] = df

        return results

    finally:
        disconnect_ib(app)


# ---------------------------------------------------------------------------
# Account summary
# ---------------------------------------------------------------------------

class _AccountApp(IBKRApp):
    """Collects tag/value rows from reqAccountSummary()."""

    def accountSummary(self, reqId, account, tag, value, currency):
        self.data.append({
            'account'  : account,
            'tag'      : tag,
            'value'    : value,
            'currency' : currency,
        })

    def accountSummaryEnd(self, reqId):
        self.finished = True


DEFAULT_TAGS = ','.join([
    'NetLiquidation',
    'TotalCashValue',
    'GrossPositionValue',
    'BuyingPower',
    'AvailableFunds',
    'ExcessLiquidity',
    'FullMaintMarginReq',
    'FullInitMarginReq',
])


def get_account_data(tags=DEFAULT_TAGS, group='All', host='127.0.0.1', port=7497):
    """Fetch account summary for *tags* and return a DataFrame."""
    app = _AccountApp()
    connect_ib(app, host, port, client_id=124)

    try:
        app.reqAccountSummary(
            reqId=9001,
            groupName=group,
            tags=tags,
        )

        for _ in range(60):          # timeout after 30 s
            if app.finished:
                break
            time.sleep(0.5)

        if not app.finished:
            print("Warning: account summary request timed out")

        df = pd.DataFrame(app.data)
        if not df.empty:
            df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(df['value'])
        return df

    finally:
        disconnect_ib(app)
