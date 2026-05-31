"""
Kalman Filter Trading System (KTS) — OU Mean-Level Estimation
-------------------------------------------------------------
The Kalman filter estimates the current mean level (fair value) of an Ornstein–Uhlenbeck (OU)
process. The horizontal line on the chart is this estimated mean level and updates every tick.
You can calibrate the OU model to historical data and control how much the filter trusts the
OU model vs. observed prices via the noise lever. IBKR API only.

• Calibrate OU: Fits AR(1) to the last N bars (bar size + calib window). Produces φ, μ, σ
  and builds the Kalman filter. Use "Refresh & Calibrate" or start stream (which calibrates first).

• Kalman update: On every tick we run predict (OU step) then update (blend with price).
  The state x is the estimated mean level; the horizontal line is drawn at x.

• Noise lever: "Trust prices" = filter follows price closely; "Trust OU" = filter stays near
  OU prediction. Implemented via observation noise R (high R ⇒ trust OU more).

• OU forecast: Purple dots show pure OU forward prediction from current state (no new observations).
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import os
import math
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle
import matplotlib.dates as mdates
from collections import deque
from datetime import datetime
from decimal import Decimal

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

# -----------------------------------------------------------------------------
# Lightweight debug logger.
# Writes one CSV-ish line per event to both stdout and logs/kts_session.log so a
# user can grep the file after a session to see exactly which tick built which
# bar. Flip DEBUG_LOG to False to silence everything cheaply.
# -----------------------------------------------------------------------------
DEBUG_LOG = True
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "kts_session.log")
_LOG_LOCK = threading.Lock()
_LOG_FH = None


def _open_log():
    global _LOG_FH
    if not DEBUG_LOG or _LOG_FH is not None:
        return
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        _LOG_FH = open(_LOG_PATH, "a", buffering=1, encoding="utf-8")
        _LOG_FH.write(f"\n==== SESSION START {datetime.now().isoformat()} ====\n")
    except Exception as e:
        print(f"[log] could not open {_LOG_PATH}: {e}")
        _LOG_FH = None


def dlog(tag, **fields):
    """Append a single timestamped line. Fields rendered as key=value pairs."""
    if not DEBUG_LOG:
        return
    if _LOG_FH is None:
        _open_log()
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    parts = [f"{k}={v}" for k, v in fields.items()]
    line = f"{ts} {tag} " + " ".join(parts)
    with _LOG_LOCK:
        print(line)
        if _LOG_FH is not None:
            try:
                _LOG_FH.write(line + "\n")
            except Exception:
                pass

# -----------------------------------------------------------------------------
# OU / AR(1) estimation from historical closes (discrete approximation to OU)
# -----------------------------------------------------------------------------
def estimate_ar1(closes):
    """
    Fit an AR(1) model to a calibration window of closing prices.
    Returns (phi, mu, sigma).

    AR(1) model:  price[t] = c + phi * price[t-1] + noise
    Equivalently: price[t] = mu*(1-phi) + phi*price[t-1] + noise  (OU form)

    Variables:
      mu    — long-run mean: sample average of the window. This is the level
              the price is expected to revert toward. The Kalman filter then
              tracks how this mean drifts tick-by-tick.
      phi   — mean-reversion speed (0 < phi < 1). Near 1 = slow reversion
              (persistent series). Near 0 = fast reversion (noisy).
      c     — OLS intercept. Not returned; used only to recover phi cleanly.
      sigma — residual std of the regression = typical one-step noise.
              Used to set process noise Q and observation noise R in the
              Kalman filter.

    np.linalg.lstsq explanation:
      We stack a column of ones next to x_lag to form X = [[1, p0],
                                                           [1, p1], ...]
      lstsq solves:  X @ beta ≈ x_curr  (ordinary least squares).
      beta[0] = c (intercept), beta[1] = phi (AR coefficient).
      rcond=None silences a deprecation warning; it sets the cutoff for
      treating small singular values as zero (irrelevant for well-conditioned
      price data).

    x_lag  — prices[0 .. n-2]: the "yesterday" values (lagged by 1 bar).
    x_curr — prices[1 .. n-1]: the "today" values aligned with x_lag.
    Together they form the (y, X) pair for the AR(1) regression.
    """
    # Guard: need at least 5 bars to fit anything meaningful
    if closes is None or len(closes) < 5:
        return None
    try:
        # Convert to float array
        y = np.array(closes, dtype=float)
        # Drop NaN / Inf bars that would corrupt the regression
        y = y[np.isfinite(y)]
        # Re-check length after dropping bad values
        if len(y) < 5:
            return None
        # mu = long-run mean; anchors the OU mean level for this window
        mu = float(np.mean(y))
        # x_lag: every bar except the last  (t-1 side of the regression)
        x_lag = y[:-1]
        # x_curr: every bar except the first (t side, what we're predicting)
        x_curr = y[1:]
        # Build design matrix X = [ones | lagged prices] for OLS,
        # this is similar to how we might do sm.add_constant(x_lag) but without the
        # statsmodels dependency
        X = np.column_stack([np.ones_like(x_lag), x_lag])
        # Solve X @ beta ≈ x_curr via least squares; [0] pulls the coefficients
        beta = np.linalg.lstsq(X, x_curr, rcond=None)[0]
        # Unpack: c = intercept, phi = AR(1) coefficient
        c, phi = float(beta[0]), float(beta[1])
        # Clip phi to (0.01, 0.99): enforces stationarity and mean-reversion
        phi = np.clip(phi, 0.01, 0.99)
        # Residuals: actual minus fitted values
        resid = x_curr - (c + phi * x_lag)
        # sigma_eps = RMS residual = AR(1) one-step INNOVATION std (per-step shock).
        sigma_eps = np.sqrt(np.mean(resid ** 2))
        # Convert innovation std -> STATIONARY std of the price level.
        # AR(1) stationary variance:
        #   Var(x) = phi^2 * Var(x) + sigma_eps^2
        #   => sigma_x^2 = sigma_eps^2 / (1 - phi^2)
        # Returning sigma_x (stationary std) makes the value directly comparable
        # to the sample std of prices — intuitive scale for the Trust-prices/Trust-OU
        # lever. KalmanOU.__init__ multiplies sigma^2 by (1 - phi^2) to recover the
        # innovation variance sigma_eps^2 for Q, so the two conventions stay
        # consistent end-to-end.
        denom = max(1 - phi ** 2, 1e-6)
        sigma = sigma_eps / np.sqrt(denom)
        # Fallback if sigma is degenerate (flat price series, etc.).
        # Use the raw sample std as a sensible stationary-scale fallback (no 0.01
        # downscale — we want it on the price-level scale, not innovation scale).
        if sigma <= 0 or not np.isfinite(sigma):
            sigma = max(np.std(y), 1e-9)
        return phi, mu, sigma
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Kalman filter: state = mean level (OU state), observation = price
# The horizontal line is drawn at self.x (updated every tick).
# -----------------------------------------------------------------------------
class KalmanOU:
    """
    One-dimensional Kalman filter with OU transition. State x is the estimated mean level
    (fair value). Parameters (phi, mu, Q, R) are set at construction; only (x, P) update on each tick.
    """

    def __init__(self, 
                 phi, 
                 mu, 
                 # when running, the parameter passed in for sigma_process is the sigma that we estimated from the last
                 # OU estimate that we ran, which was calculated as just:
                 # the sqrt of the mean of the squared residuals
                 # 
                 sigma_process,
                 obs_noise_scale=1.0):
        # FROZEN OU PARAMETERS (set once by estimate_ar1, never mutated by update()).
        # Together with Q, R these define the filter; only x, P evolve tick-by-tick.
        self.phi = phi                    # AR(1) coefficient (mean-reversion speed)
        self.mu = mu                      # long-run mean the state reverts toward
        # Q = process noise variance derived from OU stationary relation:
        #   Var(stationary) = sigma^2 / (1 - phi^2)  =>  Q = sigma^2 * (1 - phi^2)
        # interpreting sigma_process as the *stationary* std of the OU level.
        # note that sigma_process is just a draw from a 
        self.Q = (sigma_process ** 2) * max(1 - phi ** 2, 1e-6)
        # R = observation noise variance. Scaled by the "Trust prices ↔ Trust OU" lever.
        # High R => filter distrusts price ticks => Kalman gain K -> 0 => x stays on OU path.
        self.R = (sigma_process ** 2) * max(obs_noise_scale, 0.01)
        # MUTABLE STATE — these two are the only things update()/predict() change.
        self.x = mu                       # current mean-level estimate (the green line on the chart)
        # P = state variance = uncertainty around the mean level. 
        # which is a combination of observation noise R and process noise Q. 
        # It is initialized to R because at the start we have no reason to trust the initial state x
        #  more than the first observation; as ticks arrive, P evolves according to the OU transition
        #  and is reduced by blending with price ticks (the KF update step).
        self.P = self.R                   # state variance (uncertainty around x)

    def predict(self):
        """OU transition: mean level and variance evolve one step without observation."""
        self.x = self.phi * self.x + (1 - self.phi) * self.mu
        # because P is the state variance, it is a combination of the previous variance evolved through
        #  the OU transition (phi^2 * P) plus the new process noise Q added at each step.
        self.P = self.phi ** 2 * self.P + self.Q

    def update(self, z):
        """Predict then measurement update with observed price z. State (x, P) only."""
        # first, we need the model prediction for the new state before blending in the observation z,
        # so we run the .predict() method that forecasts the next state if it were to evolve purely 
        # according to the OU process without any new data.
        self.predict()
        # calculate the Kalman gain K = P / (P + R): how much we should blend the new observation z vs. the OU prediction.
        K = self.P / (self.P + self.R)
        # Update state x by blending the prediction with the new observation z, weighted by K.
        self.x = self.x + K * (z - self.x)
        # Update state variance P: after blending with the new observation, our uncertainty 
        # around x is reduced by a factor of (1 - K), reflecting the information gained from the new observation
        self.P = (1 - K) * self.P

    def forecast(self, steps):
        """Pure OU prediction: mean level at 1..steps ahead, no new data."""
        x, mu, phi = self.x, self.mu, self.phi
        return [mu + (phi ** k) * (x - mu) for k in range(1, steps + 1)]


# -----------------------------------------------------------------------------
# IBKR API wrapper: historical bars, live ticks, orders
# -----------------------------------------------------------------------------
class IBApp(EWrapper, EClient):
    """TWS/Gateway connection: stores historical bars by reqId, forwards last price to on_tick."""
    # -------------------------------------------------------------------------
    # Note on the `if self.on_tick:` guards used in tickPrice and _maybe_emit_mid
    # -------------------------------------------------------------------------
    # The constructor accepts on_tick=None, so IBApp can be used standalone for
    # historical-only pulls, REPL testing, or scripts that only need positions /
    # account values, with no live tick consumer wired in. Calling None as a
    # function would raise TypeError on every tick — and tickPrice runs on the
    # ibapi EReader thread, where an uncaught exception can destabilise the
    # reader loop, silently kill the stream, and not surface in main-thread
    # tracebacks. The guard is a cheap branch that keeps IBApp decoupled from
    # the strategy layer (KalmanTradingApp) while staying robust on the reader
    # thread.
    # -------------------------------------------------------------------------

    def __init__(self, on_tick=None):
        # initializes the shared client/wrapper plumbing
        # this effectively initialises our IBApp as an instance of both the
        # Eclient and EWrapper, which means it can both send requests and receive callbacks. 
        # -----------
        # this object can both SEND requests and RECEIVE asynchronous callbacks.
        # -----
        # By calling super().__init__() first, we ensure that all necessary base
        # setup is complete before we add our own state containers below.
        # This extends to inheriting the built-in constructor for EClient, which sets up an EWrapper
        # first, as is typically required. This is similar to using 
        # IBApp(EClient, EWrapper) and then within its __init__(), having:
        # EClient.__init__(self, self) directly [first self is the client, second self is the wrapper]
        #  but super() is more robust to future changes in the class hierarchy.
        EClient.__init__(self, self)

        # initialise key parameters
        # NOTE: none of these are built-in attrs of EClient/EWrapper. They are
        # our own state containers. ibapi is asynchronous: EClient sends requests
        # and returns immediately; results arrive later on the EReader thread via
        # EWrapper callbacks. We need instance attrs as buffers so the callback
        # thread can write and the main thread can read.

        # Our own connection flag, flipped True in connectAck() and False in
        # connectionClosed(). EClient has isConnected() internally, but we keep
        # this for quick local checks and to gate startup logic.
        self.connected = False
        # Next valid order id, populated by the nextValidId() EWrapper callback
        # right after connect. Required by placeOrder; we increment per order.
        self.next_order_id = None
        # User-supplied callback fired on each price update (LAST or synthesized
        # mid). Lets the Kalman/strategy layer consume ticks without subclassing.
        #
        # Type: a *callable* — any Python object implementing __call__ with the
        # signature on_tick(price: float, ts: datetime) -> None. In practice it
        # is the bound method KalmanTradingApp.on_tick (see line ~894), passed
        # in at construction: IBApp(on_tick=self.on_tick) at line ~460. Bound
        # methods are first-class objects in Python, so storing one as an attr
        # is just a reference — no copy, no subclassing needed.
        #
        # How it is "updated": it isn't, really. The attribute is set once at
        # __init__ and stays pointing at the same callable for the lifetime of
        # the IBApp. What changes are the *arguments* we invoke it with on each
        # tick. It is called from:
        #   - tickPrice() when tickType == LAST (line ~360): self.on_tick(price, datetime.now())
        #   - _maybe_emit_mid() when synthesizing a mid from bid/ask (line ~398)
        # Both fire on the EReader thread, so the callee must be thread-safe
        # w.r.t. any Tk/GUI state it touches.
        #
        # What it feeds into: KalmanTradingApp.on_tick consumes the price and
        #   (a) appends to the raw tick deque,
        #   (b) advances the rolling bar (self.current_bar / self.bar_start),
        #   (c) on bar close, calls self.kalman.update(price) to advance the
        #       Kalman filter state, appends to self.kalman_prices, and may
        #       dispatch orders if the price breaches kalman.x ± k*sigma.
        # If on_tick is None (no consumer wired in), tickPrice simply skips
        # the call — see `if self.on_tick:` guards at lines ~354 and ~396.
        self.on_tick = on_tick
        # Latest trade price, written by tickPrice() when tickType == LAST (4).
        self.last_price = None
        # Latest top-of-book quote, written by tickPrice() for BID (1) / ASK (2).
        # Used to synthesize mid when LAST is stale (FX/CRYPTO).
        self.bid = self.ask = None
        # reqId -> list of BarData, accumulated in historicalData() callback and
        # finalised in historicalDataEnd(). Dict so multiple concurrent reqs
        # don't collide.
        self.historical_data = {}
        # Signalled by historicalDataEnd() so a waiting thread can block on
        # .wait() until the bar dump is complete.
        self.hist_done = threading.Event()
        # symbol/conId -> position info, populated by position() callback after
        # reqPositions(). Snapshot of current account holdings.
        self.positions = {}
        # Net liquidation / cash value, written by updateAccountValue() callback
        # after reqAccountUpdates(). Used for sizing.
        self.account_value = None
        # Provenance tag set right before each on_tick call so the consumer can
        # log where the price came from (LAST tick vs synthesized mid).
        self._last_tick_source = "?"
        # Active secType, set by KalmanTradingApp.toggle_stream right before
        # reqMktData. Used to gate the LAST-short-circuit in _maybe_emit_mid:
        # FX/CRYPTO need continuous mid updates because LAST is sparse / stale.
        self.sectype = "STK"
        # Dedup guard so identical consecutive mids don't spam on_tick.
        self._last_emitted_mid = None

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
        if errorCode in (2104, 2106, 2158, 2176):
            return
        print(f"IB Error {reqId}: {errorCode} - {errorString}")

    def nextValidId(self, orderId: int):
        # EWrapper callback fired by TWS once after the API handshake completes
        # (and again on demand via reqIds(-1)). `orderId` is the next integer
        # safe to use as an outgoing placeOrder() id.
        #
        # Order IDs vs request IDs:
        #   - Order IDs (placeOrder) MUST be unique and monotonically increasing
        #     per session. TWS seeds the starting value here; we increment our
        #     local copy after each placeOrder.
        #   - Request IDs (reqMktData, reqHistoricalData, reqPositions, ...) are
        #     a separate namespace chosen by us and are not affected by this.
        #
        # Setting self.connected = True here (not in connectAck) treats the
        # arrival of nextValidId as the true "API ready" signal: the socket is
        # up AND the server has acknowledged we can send orders/requests.
        self.connected = True
        self.next_order_id = orderId

    def tickPrice(self, reqId, tickType, price, attrib):
        # IB delivers different tickType IDs depending on the data tier:
        #   LIVE  (entitlement / paid sub):  1=BID, 2=ASK, 4=LAST
        #   DELAYED (free, ~15min lag):     66=BID, 67=ASK, 68=LAST
        #   FROZEN/DELAYED-FROZEN: same IDs as their non-frozen counterparts.
        # The tier is selected by IBApp.reqMarketDataType(...) before reqMktData.
        # If we only listen on 4 we miss every delayed feed entirely (which is what
        # was happening for BTC on a Sunday: ticks arrived as type 68, were ignored,
        # so on_tick never fired and the green Kalman line sat frozen).
        dlog("tick", reqId=reqId, ttype=tickType, price=price,
             bid=self.bid, ask=self.ask, last=self.last_price)
        if price <= 0:
            return
        if tickType in (4, 68):          # LAST (live) or LAST (delayed)
            self.last_price = price
            # Guard: on_tick may be None when IBApp is used without a strategy
            # consumer (see class-level note). Skip the call rather than blow
            # up the EReader thread with a TypeError on None(...).
            if self.on_tick:
                self._last_tick_source = f"LAST({tickType})"
                # now that we have received a clear last price tick,
                # we trigger the on_tick callback with this price, 
                # which will then drive the Kalman filter update,
                # and chart redraw in the main app.
                self.on_tick(price, datetime.now())
        elif tickType in (1, 66):        # BID (live) or BID (delayed)
            self.bid = price
            # now that we have a new bid, we can attempt to synthesize a mid price
            #  if we also have an ask, but the last price is stale or missing
            #  (common for FX/crypto).
            self._maybe_emit_mid()
        elif tickType in (2, 67):        # ASK (live) or ASK (delayed)
            self.ask = price
            self._maybe_emit_mid()

    def _maybe_emit_mid(self):
        # FX (and frequently crypto on PAXOS) never sends LAST ticks — only BID/ASK.
        # Without this, on_tick would never fire and the Kalman line would freeze
        # even though quotes are streaming. Synthesize a "last" from the mid of
        # the most recent bid/ask pair and route it through on_tick.
        # Guarded: only when no real LAST has been seen, so equities keep using
        # their genuine print ticks rather than mid-of-book.
        # Equities (STK) prefer real LAST prints; if one has arrived, suppress
        # mid synth so on_tick is driven by genuine trades. FX/CRYPTO have no
        # meaningful LAST stream — bid/ask is the continuous signal there, so
        # we keep emitting mids regardless of any stale LAST that may have been
        # cached for the price label.
        if self.last_price is not None and self.sectype not in ("CASH", "CRYPTO"):
            dlog("mid_skip", reason="last_alr_set", last=self.last_price, bid=self.bid, ask=self.ask)
            return
        if self.bid is None or self.ask is None or self.ask <= self.bid:
            dlog("mid_skip", reason="not_ready", bid=self.bid, ask=self.ask)
            return
        mid = 0.5 * (self.bid + self.ask)
        # Skip duplicate emits when neither side moved.
        if self._last_emitted_mid == mid:
            return
        self._last_emitted_mid = mid
        dlog("mid_emit", mid=mid, bid=self.bid, ask=self.ask, spread=self.ask - self.bid)

        # Guard: same rationale as the LAST branch in tickPrice — on_tick may
        # be None for IBApp instances with no strategy consumer attached.
        if self.on_tick:
            self._last_tick_source = "MID"
            self.on_tick(mid, datetime.now())

    def historicalData(self, reqId, bar):
        if reqId not in self.historical_data:
            self.historical_data[reqId] = []
        self.historical_data[reqId].append({
            'date': bar.date, 'open': bar.open, 'high': bar.high,
            'low': bar.low, 'close': bar.close, 'volume': bar.volume
        })
        dlog("hist_bar", reqId=reqId, date=bar.date, o=bar.open, h=bar.high, l=bar.low, c=bar.close)

    def historicalDataEnd(self, reqId, start, end):
        bars = self.historical_data.get(reqId, [])
        first = bars[0]['date'] if bars else None
        last = bars[-1]['date'] if bars else None
        dlog("hist_end", reqId=reqId, n=len(bars), first=first, last=last, start=start, end=end)
        self.hist_done.set()

    def position(self, account: str, contract: Contract, position: float, avgCost: float):
        self.positions[contract.symbol] = {'position': position, 'avgCost': avgCost}

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        if tag == "NetLiquidation":
            try:
                self.account_value = float(value)
            except ValueError:
                pass


# -----------------------------------------------------------------------------
# Map noise lever [0, 100] to observation noise scale: 0 = trust prices, 100 = trust OU
# -----------------------------------------------------------------------------
def noise_lever_to_scale(lever_percent):
    """
    lever 0 -> scale 0.1 (low R, trust prices); lever 100 -> scale 1e8 (R >> P so K≈0, pure OU).
    At 100% Trust OU the Kalman gain is effectively zero so the KF mean equals the OU process.
    """
    p = max(0, min(100, lever_percent)) / 100.0
    if p >= 1.0:
        return 1e8
    return 0.1 + (10.0 - 0.1) * p


# -----------------------------------------------------------------------------
# Main app: GUI + OU calibration + Kalman (mean-level) + trading
# -----------------------------------------------------------------------------
class KalmanTradingApp:
    def __init__(self, root):
        # first, initialise an attribute of this app class instance to hold the root Tk window,
        #  so we can refer to it later when building the GUI and setting its properties.
        # a root is just a Tk() instance that serves as the main window for the application. 
        # We store it as self.root so we can call methods on it to set the title, size, 
        # background color, and to place other widgets inside it.
        self.root = root
        self.root.title("Kalman Filter Trading System — OU Mean-Level Estimation")
        self.root.geometry("1150x800")
        self.root.configure(bg='#0d1117')

        # OBJECT MODEL — read this once and the rest of the file makes sense:
        #   self           = KalmanTradingApp (the Tk app). Owns the GUI, the chart,
        #                    the OU params (phi/mu/sigma), and the KalmanOU instance.
        #   self.ib        = IBApp (the EClient/EWrapper). Owns only the TWS socket,
        #                    raw historical bars, raw ticks. It knows NOTHING about
        #                    OU, Kalman, plotting. It just forwards each tick to
        #                    self.on_tick via the on_tick=... callback below.
        # So phi/mu/sigma/kalman/ohlc_bars/forecast_prices are attributes of the
        # Tk app — NOT of self.ib. ibApp is just the data pipe.
        self.ib = IBApp(on_tick=self.on_tick)
        self.connected = False
        self.streaming = False
        self.api_thread = None
        # set default symbol to btc given 24/7 live market data
        self.symbol_var = tk.StringVar(value="BTC")
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.port_var = tk.StringVar(value="7497")
        # secType + exchange feed straight into Contract() in self.contract().
        # STK   -> SMART  (US equities, ETFs)
        # CASH  -> IDEALPRO (FX, 24/5, free delayed feed)
        # CRYPTO-> PAXOS  (spot crypto, 24/7, needs PAXOS sub)
        self.sectype_var = tk.StringVar(value="CRYPTO")
        self.exchange_var = tk.StringVar(value="PAXOS")
        # Market data tier passed to reqMarketDataType() before reqMktData().
        # Default = Delayed (3) because it is free, works for crypto without
        # PAXOS sub, and avoids "competing live session" errors (10197) when
        # another IB surface (phone app, web, second TWS) holds the single
        # live MD line. User can flip to Live (1) via the UI combobox.
        #   1 = Live              (needs entitlement; only one session at a time)
        #   2 = Frozen            (last live snapshot, no updates)
        #   3 = Delayed           (~10-15 min lag, free, always available)
        #   4 = Delayed-Frozen    (delayed snapshot, no updates)
        self.mkt_data_type_var = tk.StringVar(value="Delayed")

        self.bar_size_var = tk.StringVar(value="1 m")
        self.calib_window_var = tk.StringVar(value="60")
        self.online_params_var = tk.BooleanVar(value=False)
        self.noise_lever_var = tk.DoubleVar(value=50.0)
        # Trading-band controls. Bands = mu ± k * sigma (stationary std).
        self.band_mult_var = tk.DoubleVar(value=0.8)
        self.show_bands_var = tk.BooleanVar(value=True)
        # Forecast cone: line + ±1σ shaded region instead of dots.
        self.show_forecast_bounds_var = tk.BooleanVar(value=True)
        self.forecast_horizon_var = tk.IntVar(value=10)
        self._bar_sec = 60
        self.max_bars = 120
        self.ohlc_bars = deque(maxlen=self.max_bars)
        self.current_bar = None
        self.bar_start = None
        self.prices = deque(maxlen=500)
        self.kalman_prices = []
        self.forecast_prices = []

        # OU MODEL STATE lives HERE on the Tk app. Lifecycle:
        #   - Born None at startup.
        #   - First populated by refresh_30m() -> _recalibrate_from_bars() ->
        #     estimate_ar1(closes), which returns (phi, mu, sigma). Those land in
        #     self.phi/self.mu/self.sigma and a fresh KalmanOU(phi,mu,sigma,...) is
        #     stored in self.kalman.
        #   - On every live tick, on_tick() calls self.kalman.update(price); this
        #     mutates self.kalman.x (and .P) in place. phi/mu/sigma stay frozen
        #     UNLESS "Recalibrate OU each new bar" is ticked, in which case
        #     on_tick re-runs _recalibrate_from_bars at every bar close and
        #     overwrites all four (phi, mu, sigma, kalman).
        # So the answer to "where is the current mu / phi / sigma at time t?" is
        # always: self.phi / self.mu / self.sigma. And the current Kalman mean
        # (the live horizontal line) is self.kalman.x.
        self.phi = self.mu = self.sigma = None
        self.kalman = None

        # ---- AUTO-TRADE STATE ----
        # Toggleable mean-reversion signal driven by the OU/Kalman model already on screen.
        # Logic (evaluated at bar-close cadence):
        #   if last_price > upper_band  AND  H-step OU forecast lies INSIDE [lower, upper]
        #       -> SHORT (price rich, expected to revert down toward mean)
        #   if last_price < lower_band  AND  H-step OU forecast lies INSIDE [lower, upper]
        #       -> LONG  (price cheap, expected to revert up toward mean)
        # Bands       = mu ± k*sigma   (k = self.band_mult_var; same bands drawn on chart)
        # Horizon H   = self.forecast_horizon_var (reuses the existing UI control)
        # Cap         = self.max_position_var: never let |projected position| exceed it.

        # Master on/off switch wired to a Checkbutton in the trading frame.
        self.auto_trade_var = tk.BooleanVar(value=False)
        # How many BAR CLOSES between successive signal evaluations (1 = every bar).
        # Bounded to small integers via a readonly combobox in the UI.
        self.auto_eval_bars_var = tk.IntVar(value=1)
        # Absolute cap on |self.position|. The signal will refuse to dispatch an order
        # that would push the position past this magnitude in either direction.
        self.max_position_var = tk.IntVar(value=100)
        # Internal counter incremented on each bar close; when it reaches the eval
        # cadence we run the signal check and reset it back to 0.
        self._bars_since_eval = 0

        self.position = 0
        self.entry_price = 0.0
        self.cash = 100000.0
        self.order_id = None
        self._chart_update_scheduled = False
        self._last_redraw_time = 0.0

        self.setup_styles()
        self.setup_ui()
        self.setup_chart()
        self.refresh_timer()

    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')
        bg, fg = '#0d1117', '#c9d1d9'
        self.style.configure('TFrame', background=bg)
        self.style.configure('TLabelframe', background=bg, foreground=fg)
        self.style.configure('TLabelframe.Label', background=bg, foreground=fg, font=('Segoe UI', 10, 'bold'))
        self.style.configure('TLabel', background=bg, foreground=fg)
        self.style.configure('TButton', background='#238636', foreground='white', padding=(8, 4))
        self.style.map('TButton', background=[('active', '#2ea043')])
        self.style.configure('Accent.TButton', background='#da3633')
        self.style.map('Accent.TButton', background=[('active', '#f85149')])

    def setup_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.grid(row=0, column=0, sticky='nsew')
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(6, weight=1)

        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(header, text="Kalman Filter Trading System — OU Mean-Level", font=('Segoe UI', 14, 'bold')).pack(side='left')
        self.status_lbl = tk.Label(header, text="● Disconnected", font=('Segoe UI', 10, 'bold'), bg='#0d1117', fg='#f85149')
        self.status_lbl.pack(side='right')

        ctrl = ttk.LabelFrame(main, text="Connection & Symbol", padding=8)
        ctrl.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        ctrl.columnconfigure(1, weight=1)
        row0 = ttk.Frame(ctrl)
        row0.grid(row=0, column=0, sticky='ew')
        ttk.Label(row0, text="Host").pack(side='left', padx=(0, 4))
        ttk.Entry(row0, textvariable=self.host_var, width=12).pack(side='left', padx=(0, 12))
        ttk.Label(row0, text="Port").pack(side='left', padx=(0, 4))
        ttk.Entry(row0, textvariable=self.port_var, width=6).pack(side='left', padx=(0, 12))
        self.connect_btn = ttk.Button(row0, text="Connect", command=self.connect_ib)
        self.connect_btn.pack(side='left', padx=(0, 6))
        self.disc_btn = ttk.Button(row0, text="Disconnect", command=self.disconnect_ib, state='disabled', style='Accent.TButton')
        self.disc_btn.pack(side='left')
        row1 = ttk.Frame(ctrl)
        row1.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        ttk.Label(row1, text="Symbol").pack(side='left', padx=(0, 4))
        ttk.Entry(row1, textvariable=self.symbol_var, width=10).pack(side='left', padx=(0, 8))
        # secType combobox: feeds Contract.secType.
        ttk.Label(row1, text="SecType").pack(side='left', padx=(0, 4))
        sectype_combo = ttk.Combobox(row1, textvariable=self.sectype_var,
                                     values=("STK", "CASH", "CRYPTO"),
                                     width=7, state="readonly")
        sectype_combo.pack(side='left', padx=(0, 8))
        # When secType changes, auto-suggest the conventional exchange so user
        # doesn't have to remember (STK->SMART, CASH->IDEALPRO, CRYPTO->PAXOS).
        # They can still override the exchange manually.
        sectype_combo.bind("<<ComboboxSelected>>", self._on_sectype_change)
        ttk.Label(row1, text="Exchange").pack(side='left', padx=(0, 4))
        ttk.Combobox(row1, textvariable=self.exchange_var,
                     values=("SMART", "IDEALPRO", "PAXOS"),
                     width=10).pack(side='left', padx=(0, 8))
        # Market-data tier selector. Read once by toggle_stream() right before
        # reqMarketDataType + reqMktData open the live subscription. Placing it
        # next to the stream button makes the flow obvious: pick tier -> start.
        # Mapping label -> int code is centralised in _mkt_data_type_code() so
        # toggle_stream and refresh_30m share one source of truth.
        ttk.Label(row1, text="MD").pack(side='left', padx=(0, 4))
        ttk.Combobox(row1, textvariable=self.mkt_data_type_var,
                     values=("Live", "Frozen", "Delayed", "Delayed-Frozen"),
                     width=14, state="readonly").pack(side='left', padx=(0, 8))
        self.stream_btn = ttk.Button(row1, text="▶ Start stream", command=self.toggle_stream, state='disabled')
        self.stream_btn.pack(side='left', padx=(0, 8))
        self.refresh_btn = ttk.Button(row1, text="⟳ Refresh & Calibrate OU", command=self.refresh_30m, state='disabled')
        self.refresh_btn.pack(side='left', padx=(0, 8))
        ttk.Button(row1, text="Clear chart", command=self.clear_chart, style='Accent.TButton').pack(side='left', padx=(0, 8))
        self.price_lbl = tk.Label(row1, text="Last: ---", font=('Consolas', 12, 'bold'), bg='#0d1117', fg='#7ee787')
        self.price_lbl.pack(side='right')

        ou_frame = ttk.LabelFrame(main, text="OU Model & Calibration", padding=8)
        ou_frame.grid(row=2, column=0, sticky='ew', pady=(0, 4))
        ou_frame.columnconfigure(1, weight=1)
        row_ou = ttk.Frame(ou_frame)
        row_ou.grid(row=0, column=0, sticky='ew')
        ttk.Label(row_ou, text="Bar size").pack(side='left', padx=(0, 4))
        bar_combo = ttk.Combobox(row_ou, textvariable=self.bar_size_var, values=("30 s", "1 m", "5 m"), width=8, state="readonly")
        bar_combo.pack(side='left', padx=(0, 12))
        ttk.Label(row_ou, text="Calib window (bars)").pack(side='left', padx=(0, 4))
        ttk.Entry(row_ou, textvariable=self.calib_window_var, width=6).pack(side='left', padx=(0, 12))
        self.online_cb = ttk.Checkbutton(row_ou, text="Recalibrate OU each new bar", variable=self.online_params_var)
        self.online_cb.pack(side='left', padx=(0, 16))
        ttk.Label(row_ou, text="φ (phi):").pack(side='left', padx=(0, 2))
        self.phi_lbl = tk.Label(row_ou, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.phi_lbl.pack(side='left', padx=(0, 8))
        ttk.Label(row_ou, text="μ (mean):").pack(side='left', padx=(0, 2))
        self.mu_lbl = tk.Label(row_ou, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.mu_lbl.pack(side='left', padx=(0, 8))
        ttk.Label(row_ou, text="σ:").pack(side='left', padx=(0, 2))
        self.sigma_lbl = tk.Label(row_ou, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.sigma_lbl.pack(side='left')

        noise_frame = ttk.LabelFrame(main, text="Kalman: Trust prices ↔ Trust OU model", padding=8)
        noise_frame.grid(row=3, column=0, sticky='ew', pady=(0, 4))
        noise_frame.columnconfigure(1, weight=1)
        row_noise = ttk.Frame(noise_frame)
        row_noise.grid(row=0, column=0, sticky='ew')
        ttk.Label(row_noise, text="Trust prices").pack(side='left', padx=(0, 6))
        self.noise_slider = ttk.Scale(row_noise, from_=0, to=100, variable=self.noise_lever_var, orient='horizontal', length=280, command=self._on_noise_lever)
        self.noise_slider.pack(side='left', padx=(0, 6))
        ttk.Label(row_noise, text="Trust OU").pack(side='left', padx=(0, 8))
        self.noise_val_lbl = tk.Label(row_noise, text="50%", font=('Consolas', 10), bg='#0d1117', fg='#58a6ff')
        self.noise_val_lbl.pack(side='left')

        # Trading bands + forecast-display controls. Bands at mu ± k*sigma where
        # sigma is the stationary std from estimate_ar1. Forecast cone uses the
        # k-step OU conditional variance: Var(x_{t+k}|x_t) = sigma^2*(1-phi^(2k)).
        bands_frame = ttk.LabelFrame(main, text="Trading Bands & Forecast Display", padding=8)
        bands_frame.grid(row=4, column=0, sticky='ew', pady=(0, 4))
        row_bands = ttk.Frame(bands_frame)
        row_bands.grid(row=0, column=0, sticky='ew')
        ttk.Checkbutton(row_bands, text="Show bands", variable=self.show_bands_var,
                        command=self.redraw_chart).pack(side='left', padx=(0, 12))
        ttk.Label(row_bands, text="Band width (k·σ):").pack(side='left', padx=(0, 4))
        self.band_slider = ttk.Scale(row_bands, from_=0.1, to=3.0, variable=self.band_mult_var,
                                     orient='horizontal', length=180, command=self._on_band_mult)
        self.band_slider.pack(side='left', padx=(0, 6))
        self.band_val_lbl = tk.Label(row_bands, text=f"{self.band_mult_var.get():.2f}",
                                     font=('Consolas', 10), bg='#0d1117', fg='#58a6ff')
        self.band_val_lbl.pack(side='left', padx=(0, 16))
        ttk.Checkbutton(row_bands, text="Forecast as line + ±1σ cone",
                        variable=self.show_forecast_bounds_var,
                        command=self.redraw_chart).pack(side='left', padx=(0, 12))
        ttk.Label(row_bands, text="Horizon (bars):").pack(side='left', padx=(0, 4))
        ttk.Entry(row_bands, textvariable=self.forecast_horizon_var, width=4).pack(side='left')

        trade = ttk.LabelFrame(main, text="Trading & Portfolio", padding=8)
        trade.grid(row=5, column=0, sticky='ew', pady=(0, 8))
        row2 = ttk.Frame(trade)
        row2.grid(row=0, column=0, sticky='ew')
        ttk.Button(row2, text="Long", command=lambda: self.place_trade(1)).pack(side='left', padx=(0, 6))
        ttk.Button(row2, text="Short", command=lambda: self.place_trade(-1)).pack(side='left', padx=(0, 6))
        ttk.Button(row2, text="Close", command=lambda: self.place_trade(0), style='Accent.TButton').pack(side='left', padx=(0, 12))
        ttk.Label(row2, text="Position:").pack(side='left', padx=(0, 4))
        self.pos_lbl = tk.Label(row2, text="0", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#8b949e')
        self.pos_lbl.pack(side='left', padx=(0, 12))
        ttk.Label(row2, text="Portfolio:").pack(side='left', padx=(0, 4))
        self.port_lbl = tk.Label(row2, text="100000.00", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#58a6ff')
        self.port_lbl.pack(side='left')

        # ---- AUTO-TRADE ROW ----
        # Second row inside the same "Trading & Portfolio" frame so the auto controls
        # sit visually next to the manual Long/Short/Close buttons but on their own line.
        row_auto = ttk.Frame(trade)
        row_auto.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        # Master toggle. Unchecked = on_tick never dispatches signal-driven orders.
        ttk.Checkbutton(row_auto, text="Auto-trade (OU mean-reversion)",
                        variable=self.auto_trade_var).pack(side='left', padx=(0, 12))
        # Label + combobox for the evaluation cadence (every N bar closes).
        ttk.Label(row_auto, text="Eval every N bars:").pack(side='left', padx=(0, 4))
        ttk.Combobox(row_auto, textvariable=self.auto_eval_bars_var,
                     values=(1, 2, 3, 5, 10), width=4,
                     state="readonly").pack(side='left', padx=(0, 12))
        # Label + combobox for the position-size cap. Editable so user can type
        # an arbitrary value if the preset list doesn't include their target.
        ttk.Label(row_auto, text="Max |position|:").pack(side='left', padx=(0, 4))
        ttk.Combobox(row_auto, textvariable=self.max_position_var,
                     values=(10, 50, 100, 250, 500, 1000),
                     width=6).pack(side='left', padx=(0, 12))
        # Small status label so user can SEE that auto-trade fired (or was capped)
        # without having to grep the log file in real time.
        self.auto_status_lbl = tk.Label(row_auto, text="auto: idle",
                                        font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.auto_status_lbl.pack(side='left', padx=(8, 0))

        chart_frame = ttk.LabelFrame(main, text="OHLC | Orange = Kalman mean (history) | Solid line = live mean (updates every tick) | Purple = OU forecast", padding=6)
        chart_frame.grid(row=6, column=0, sticky='nsew')
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.rowconfigure(0, weight=1)
        self.chart_container = ttk.Frame(chart_frame)
        self.chart_container.grid(row=0, column=0, sticky='nsew')
        self.chart_container.columnconfigure(0, weight=1)
        self.chart_container.rowconfigure(0, weight=1)

    def _on_band_mult(self, _):
        # Slider commits a float; refresh label and trigger redraw of bands.
        self.band_val_lbl.config(text=f"{self.band_mult_var.get():.2f}")
        self.redraw_chart()

    def _on_noise_lever(self, _):
        v = self.noise_lever_var.get()
        self.noise_val_lbl.config(text=f"{v:.0f}%")
        if self.kalman is not None and self.sigma is not None:
            scale = noise_lever_to_scale(v)
            self.kalman.R = (self.sigma ** 2) * max(scale, 0.01)
            self.redraw_chart()

    def setup_chart(self):
        plt.style.use('dark_background')
        self.fig, self.ax = plt.subplots(figsize=(11, 5), facecolor='#0d1117')
        self.ax.set_facecolor('#161b22')
        self.ax.tick_params(colors='#8b949e')
        self.ax.spines['bottom'].set_color('#30363d')
        self.ax.spines['top'].set_color('#30363d')
        self.ax.spines['left'].set_color('#30363d')
        self.ax.spines['right'].set_color('#30363d')
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_container)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky='nsew')

        # Hover annotation showing OHLC of the bar under the cursor. Single
        # reusable Annotation toggled visible/invisible to avoid leaking artists.
        self._hover_annot = self.ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords='offset points',
            bbox=dict(boxstyle='round,pad=0.4', fc='#161b22', ec='#58a6ff', alpha=0.95),
            color='#c9d1d9', fontsize=9, family='Consolas', zorder=10,
        )
        self._hover_annot.set_visible(False)
        self.canvas.mpl_connect('motion_notify_event', self._on_hover)
        self.canvas.mpl_connect('axes_leave_event', self._on_hover_leave)
        self._last_log_time = 0.0

    def _on_hover_leave(self, _evt):
        if self._hover_annot.get_visible():
            self._hover_annot.set_visible(False)
            self.canvas.draw_idle()

    def _on_hover(self, event):
        if event.inaxes != self.ax or event.xdata is None:
            if self._hover_annot.get_visible():
                self._hover_annot.set_visible(False)
                self.canvas.draw_idle()
            return
        # Bars are drawn at integer x indices 0..n-1, with the forming bar at n.
        i = int(round(event.xdata))
        n = len(self.ohlc_bars)
        bar = None
        label_suffix = ""
        if 0 <= i < n:
            bar = self.ohlc_bars[i]
        elif i == n and self.current_bar is not None:
            bar = {'t': self.current_bar[0], 'o': self.current_bar[1],
                   'h': self.current_bar[2], 'l': self.current_bar[3],
                   'c': self.current_bar[4]}
            label_suffix = " (forming)"
        if bar is None:
            if self._hover_annot.get_visible():
                self._hover_annot.set_visible(False)
                self.canvas.draw_idle()
            return
        t = bar['t']
        t_str = t.strftime('%Y-%m-%d %H:%M:%S') if hasattr(t, 'strftime') else str(t)
        text = (f"idx {i}{label_suffix}\n"
                f"t  {t_str}\n"
                f"O  {bar['o']:.5f}\n"
                f"H  {bar['h']:.5f}\n"
                f"L  {bar['l']:.5f}\n"
                f"C  {bar['c']:.5f}")
        self._hover_annot.xy = (i, bar['c'])
        self._hover_annot.set_text(text)
        self._hover_annot.set_visible(True)
        self.canvas.draw_idle()

    def _mkt_data_type_code(self):
        # Translate the UI combobox label into the integer code expected by
        # ibapi's reqMarketDataType(). Kept as a small helper so both
        # toggle_stream (live ticks) and refresh_30m (historical bars) read
        # the same setting — otherwise it's easy to flip Delayed in one place
        # and forget the other, leading to confusing partial-live state.
        return {
            "Live": 1,
            "Frozen": 2,
            "Delayed": 3,
            "Delayed-Frozen": 4,
        }.get(self.mkt_data_type_var.get(), 3)

    def contract(self):
        # Builds the Contract object passed to every reqMktData / reqHistoricalData
        # / placeOrder. The (secType, exchange) pair drives which venue IB routes to
        # and which entitlement tier applies — wrong combo is the most common cause
        # of 10089 / "no data" errors. secType + exchange now come from the UI.
        c = Contract()
        c.symbol = self.symbol_var.get().strip().upper()
        c.secType = self.sectype_var.get().strip().upper()
        c.exchange = self.exchange_var.get().strip().upper()
        c.currency = "USD"
        return c

    def _on_sectype_change(self, _evt):
        # Convenience: snap exchange to the conventional default whenever the user
        # changes the secType dropdown. They can still override afterward.
        mapping = {"STK": "SMART", "CASH": "IDEALPRO", "CRYPTO": "PAXOS"}
        self.exchange_var.set(mapping.get(self.sectype_var.get(), "SMART"))

    def connect_ib(self):
        if self.connected:
            return
        try:
            port = int(self.port_var.get())
            self.ib.connect(self.host_var.get(), port, 98)
            self.api_thread = threading.Thread(target=self.ib.run, daemon=True)
            self.api_thread.start()
            for _ in range(50):
                time.sleep(0.1)
                if self.ib.connected:
                    break
            if self.ib.connected:
                self.connected = True
                self.order_id = self.ib.next_order_id
                self.connect_btn.config(state='disabled')
                self.disc_btn.config(state='normal')
                self.stream_btn.config(state='normal')
                self.refresh_btn.config(state='normal')
                self.status_lbl.config(text="● Connected", fg='#3fb950')
                self.ib.reqAccountSummary(9001, "All", "NetLiquidation")
                self.ib.reqPositions()
                time.sleep(0.3)
                sym = self.symbol_var.get().strip().upper()
                if sym in self.ib.positions:
                    self.position = int(self.ib.positions[sym]['position'])
                    self.entry_price = self.ib.positions[sym]['avgCost']
            else:
                messagebox.showerror("Connection", "Could not connect to TWS/Gateway")
        except Exception as e:
            messagebox.showerror("Connection", str(e))

    def disconnect_ib(self):
        if not self.connected:
            return
        if self.streaming:
            self.toggle_stream()
        self.ib.disconnect()
        self.connected = False
        self.connect_btn.config(state='normal')
        self.disc_btn.config(state='disabled')
        self.stream_btn.config(state='disabled')
        self.refresh_btn.config(state='disabled')
        self.status_lbl.config(text="● Disconnected", fg='#f85149')

    def clear_chart(self):
        if self.streaming:
            self.ib.cancelMktData(1)
            self.streaming = False
            self.stream_btn.config(text="▶ Start stream")
            self.status_lbl.config(text="● Connected", fg='#3fb950')
        self.ohlc_bars.clear()
        self.current_bar = None
        self.bar_start = None
        self.prices.clear()
        self.kalman_prices = []
        self.forecast_prices = []
        self.kalman = None
        self.phi = self.mu = self.sigma = None
        self.phi_lbl.config(text="—")
        self.mu_lbl.config(text="—")
        self.sigma_lbl.config(text="—")
        self.price_lbl.config(text="Last: ---")
        # Reset auto-trade cadence counter so a stale partial count doesn't carry
        # across symbol changes / chart clears.
        self._bars_since_eval = 0
        # Wipe the auto-trade status hint so an old "auto: LONG @..." text doesn't
        # linger after the chart is cleared.
        if hasattr(self, 'auto_status_lbl'):
            self.auto_status_lbl.config(text="auto: idle", fg='#8b949e')
        self.redraw_chart()

    def toggle_stream(self):
        # Toggle live tick subscription on/off. Bound to the "Start/Stop stream" button.
        # There is NO internal while-loop here — IBKR's reader thread (started in connect_ib)
        # pushes tickPrice callbacks on its own. This method only flips the subscription.
        #
        # IMPORTANT: this method runs EXACTLY ONCE per user click of the stream button
        # (wired in setup_ui as: self.stream_btn = ttk.Button(..., command=self.toggle_stream)).
        # It is not polled, not scheduled, not called from on_tick. So when it fires we know
        # the user just clicked — meaning they want to flip whatever the current state is.
        # That's why the branch reads as "if self.streaming: turn OFF" rather than the
        # other way round: self.streaming reflects state UP TO this click, and the click
        # itself is the request to invert it.
        if not self.connected:
            return
        if self.streaming:
            # Up to this click we were streaming -> user clicked to STOP. Tear down subscription.
            # cancelMktData(reqId=1) tells TWS to stop sending ticks for the request that was
            # opened below with reqMktData(1, ...). It is NOT a loop — it's a single cancel
            # message. After this, tickPrice callbacks for reqId 1 stop arriving, so on_tick
            # is no longer fed. The IB reader thread itself keeps running (still alive for
            # historical data, account updates, orders); only this market-data stream stops.
            self.ib.cancelMktData(1)
            self.streaming = False
            self.stream_btn.config(text="▶ Start stream")
            self.status_lbl.config(text="● Connected", fg='#3fb950')
            # Reset auto-trade cadence counter so the count restarts from 0 on the
            # next stream start rather than carrying over a stale partial count.
            self._bars_since_eval = 0
        else:
            # Up to this click we were idle -> user clicked to START. Calibrate, then subscribe.
            # refresh_30m is a BLOCKING call on the Tk main thread: it requests historical
            # bars and waits (up to 20s) for historicalDataEnd before returning. Calibration
            # must finish before live ticks start, otherwise on_tick would fire with
            # self.kalman == None and the KF update branch would be skipped until the user
            # manually clicked Refresh.
            self.refresh_30m()
            # Market-data tier selection. MUST be called BEFORE reqMktData, otherwise
            # IB defaults to live and may refuse with error 10089 ("Requested market
            # data requires additional subscription...") for symbols we don't have a
            # paid sub on (e.g. BTC, most crypto, some intl equities).
            #   1 = Live (default; needs entitlement)
            #   2 = Frozen (last live snapshot, no updates)
            #   3 = Delayed (~10-15 min lag, free) <- what we use here
            #   4 = Delayed-Frozen (delayed snapshot, no updates)
            # With type 3 (Delayed), ticks arrive as tickType 66/67/68 instead
            # of 1/2/4 — the IBApp.tickPrice handler above accepts both sets so
            # on_tick fires either way. Side effect: the green Kalman line
            # moves on Sundays / out-of-hours for symbols that have delayed
            # feeds (e.g. crypto on PAXOS, FX, equities during ext-hours).
            # Tier is now user-selectable via the MD combobox in the toolbar.
            self.ib.reqMarketDataType(self._mkt_data_type_code())
            # Tell IBApp which secType is active so _maybe_emit_mid can decide
            # whether to keep emitting mids when LAST has been seen (FX/CRYPTO)
            # or suppress them in favor of LAST (STK).
            self.ib.sectype = self.sectype_var.get().strip().upper()
            self.ib._last_emitted_mid = None  # reset dedup across symbols
            # reqMktData opens a streaming subscription. reqId=1 is the handle we later pass
            # to cancelMktData. After this returns, the IB reader thread will start invoking
            # IBApp.tickPrice for every tick — which in turn calls self.on_tick (see below).
            # This is the "engine": from here on, every tick from TWS drives one pass through
            # on_tick. No polling, no while loop on our side.
            #
            # Args: (reqId, contract, genericTickList="", snapshot=False,
            #        regulatorySnapshot=False, mktDataOptions=[]).
            # snapshot=False => streaming (what we want). True would deliver one frame.
            self.ib.reqMktData(1, self.contract(), "", False, False, [])
            self.streaming = True
            self.stream_btn.config(text="■ Stop stream")
            self.status_lbl.config(text="● Streaming", fg='#58a6ff')

    def on_tick(self, price, ts):
        # ============================================================================
        # THE HEARTBEAT. Invoked by IBApp.tickPrice on the IB reader thread (NOT the Tk
        # main thread) for every "last trade" tick TWS pushes after reqMktData was opened.
        # One tick in -> one pass through this method. No while loops anywhere — the loop
        # is implicit: TWS keeps pushing ticks, ibapi reads them off the socket in its
        # reader thread, dispatches to tickPrice, which calls us. Cancel the subscription
        # (cancelMktData) and this method stops being called.
        # ============================================================================

        kx_before = self.kalman.x if self.kalman is not None else None
        src = getattr(self.ib, "_last_tick_source", "?")
        dlog("on_tick_in", price=price, src=src, kx_before=kx_before,
             bar_start=self.bar_start, cur_bar=self.current_bar)

        # 1) Raw tick stream buffer (bounded by deque maxlen=500). Used for nothing
        #    structural right now, just kept around.
        self.prices.append(price)

        # 2) Bar aggregation. We build OHLC bars locally from ticks rather than asking
        #    IB for live bars. current_bar = [start_ts, open, high, low, close].
        if self.current_bar is None:
            # First tick of a new bar (or first tick ever). Seed all four prices to this
            # tick and remember when the bar started.
            self.current_bar = [ts, price, price, price, price]
            self.bar_start = ts
        else:
            # Mid-bar tick: update high, low, close. Open stays fixed.
            self.current_bar[2] = max(self.current_bar[2], price)  # high
            self.current_bar[3] = min(self.current_bar[3], price)  # low
            self.current_bar[4] = price                            # close = latest tick

        # 3) Kalman update on EVERY tick (not just bar-close). self.kalman.x is the
        #    estimated mean level — this is the green horizontal line on the chart.
        #    kalman.update(price) runs predict (OU step) then correct (blend with z=price),
        #    mutating self.kalman.x and self.kalman.P in place. Forecast is recomputed so
        #    the purple dots track current state.
        if self.kalman is not None:
            self.kalman.update(price)
            self.forecast_prices = self.kalman.forecast(5)

        # 4) Bar-close check. _bar_sec is 30/60/300 depending on bar-size dropdown.
        delta = (ts - self.bar_start).total_seconds() if self.bar_start else 0
        if delta >= self._bar_sec:
            # ---- Bar just closed. Push finalized OHLC into the history deque. ----
            finalized = {
                't': self.bar_start, 'o': self.current_bar[1], 'h': self.current_bar[2],
                'l': self.current_bar[3], 'c': self.current_bar[4]
            }
            self.ohlc_bars.append(finalized)
            dlog("bar_close", idx=len(self.ohlc_bars) - 1, **{k: v for k, v in finalized.items() if k != 't'},
                 t=str(finalized['t']))

            # ---- OU calibration policy at bar close ----
            # CASE A: user ticked "Recalibrate OU each new bar".
            #   We rebuild the AR(1) fit from scratch using the most recent calib-window
            #   bars (closes only), get fresh (phi, mu, sigma), and BUILD A NEW KalmanOU.
            #   The KF history line (kalman_prices) is also rebuilt by replaying the
            #   filter over all stored bars. So phi/mu/sigma drift as the market moves.
            # CASE B: checkbox unchecked (default).
            #   We DO NOT touch phi/mu/sigma. They remain frozen at whatever values
            #   refresh_30m produced at the start of the stream. The Kalman filter still
            #   keeps updating self.kalman.x every tick (step 3 above) — only the OU
            #   *parameters* are frozen. We just append the current self.kalman.x to
            #   kalman_prices so the orange "Kalman mean (history)" dots get a new point
            #   at this bar's index.
            # Net effect: case B = "calibrate once, run forever"; case A = "rolling
            # window recalibration each bar." In both cases the live KF mean is alive.
            if self.online_params_var.get() and self.kalman is not None:
                scale = noise_lever_to_scale(self.noise_lever_var.get())
                result = self._recalibrate_from_bars(list(self.ohlc_bars), scale)
                if result is not None:
                    # _recalibrate_from_bars already overwrote self.phi/mu/sigma.
                    # Swap in the new KF and replace history with the replayed series.
                    self.kalman = result[3]
                    self.kalman_prices = result[4]
            elif self.kalman is not None:
                # Frozen-params branch: just record current KF mean as one new history point.
                self.kalman_prices.append(self.kalman.x)

            # ---- AUTO-TRADE EVALUATION (bar-close cadence) ----
            # We increment a counter on every bar close. When the counter reaches the
            # user-selected cadence (1..10 bars), we run the OU mean-reversion signal
            # check and then reset the counter. Keeping this inside the bar-close
            # branch — rather than in the per-tick path — guarantees:
            #   (a) the signal aligns with the discrete OU step the model is built on,
            #   (b) we never spam orders multiple times within a single bar,
            #   (c) the cadence dropdown maps 1:1 to bar closes (intuitive UX).
            # Bump the "bars since we last evaluated" counter by one.
            self._bars_since_eval += 1
            # Read the cadence (N) from the UI; coerce to >=1 to avoid an infinite
            # fire on N=0 (defensive — combobox shouldn't allow it but be safe).
            try:
                eval_n = max(1, int(self.auto_eval_bars_var.get()))
            except (TypeError, ValueError):
                eval_n = 1
            # If we've accumulated enough bar closes, reset the counter and evaluate.
            if self._bars_since_eval >= eval_n:
                self._bars_since_eval = 0
                # `price` here is the closing tick of the bar we JUST finalized — it
                # is the most authoritative "last price" for signal evaluation.
                self._check_auto_signal(price)

            # Start a fresh forming bar anchored at this tick.
            self.current_bar = [ts, price, price, price, price]
            self.bar_start = ts
            if self.kalman is not None:
                self.forecast_prices = self.kalman.forecast(5)

            # Schedule redraw via Tk's event loop. We are on the IB thread; matplotlib
            # + Tk widgets MUST be touched on the main thread. root.after(0, fn) queues
            # fn onto the Tk mainloop as soon as it's free. The boolean guard prevents
            # piling up multiple pending redraws.
            if not self._chart_update_scheduled:
                self._chart_update_scheduled = True
                self.root.after(0, self._deferred_chart_update)
            return

        # 5) Mid-bar throttled redraw. Without throttling, every tick (potentially many
        #    per second) would queue a redraw and Tk would lag. We allow at most one
        #    redraw every 80ms.
        now = time.time()
        if not self._chart_update_scheduled and (now - self._last_redraw_time) >= 0.08:
            self._chart_update_scheduled = True
            self.root.after(0, self._deferred_chart_update)

    def _deferred_chart_update(self):
        self._chart_update_scheduled = False
        self._last_redraw_time = time.time()
        if self.ib.last_price is not None:
            # 6 significant figures keeps FX (1.16418) readable while not
            # bloating high-priced equities into scientific notation.
            self.price_lbl.config(text=f"Last: {self.ib.last_price:.6g}")
        self.redraw_chart()

    def _bar_size_to_sec_and_ib(self):
        bs = self.bar_size_var.get().strip()
        if bs == "30 s":
            return 30, "30 secs"
        if bs == "5 m":
            return 300, "5 mins"
        return 60, "1 min"

    def _get_calib_window(self):
        try:
            return max(5, min(500, int(self.calib_window_var.get())))
        except ValueError:
            return 60

    def _recalibrate_from_bars(self, bars_list, obs_scale):
        n = len(bars_list)
        if n < 5:
            return None
        w = min(self._get_calib_window(), n)

        def close(b):
            return b.get('c', b.get('close'))

        closes = [close(b) for b in bars_list[-w:]]
        params = estimate_ar1(closes)
        if params is None:
            return None
        phi, mu, sigma = params
        self.phi, self.mu, self.sigma = phi, mu, sigma
        kalman = KalmanOU(phi, mu, sigma, obs_noise_scale=obs_scale)
        kalman_prices = []
        for b in bars_list:
            kalman.update(close(b))
            kalman_prices.append(kalman.x)
        return phi, mu, sigma, kalman, kalman_prices

    def _update_ou_labels(self):
        if self.phi is not None and np.isfinite(self.phi):
            self.phi_lbl.config(text=f"{self.phi:.4f}")
        else:
            self.phi_lbl.config(text="—")
        if self.mu is not None and np.isfinite(self.mu):
            self.mu_lbl.config(text=f"{self.mu:.2f}")
        else:
            self.mu_lbl.config(text="—")
        if self.sigma is not None and np.isfinite(self.sigma):
            self.sigma_lbl.config(text=f"{self.sigma:.4f}")
        else:
            self.sigma_lbl.config(text="—")

    def refresh_30m(self):
        # ============================================================================
        # ONE-SHOT historical pull + OU calibration. Called from:
        #   (a) "Refresh & Calibrate OU" button (manual recalibration), and
        #   (b) toggle_stream() right before opening the live tick subscription.
        # It is NOT periodic. After it returns, on_tick takes over; refresh_30m only
        # runs again if the user clicks the button (or restarts the stream).
        # Runs on the Tk MAIN thread — this is important because it blocks waiting for
        # the IB reader thread to deliver historical bars.
        # ============================================================================
        if not self.connected:
            return

        # 1) Read UI inputs: number of bars to use for the AR(1) calibration window,
        #    and the bar size (30s / 1m / 5m). Map bar size to (seconds, IB string).
        num_bars = self._get_calib_window()
        self.calib_window_var.set(str(num_bars))
        bar_sec, bar_size_setting = self._bar_size_to_sec_and_ib()
        self._bar_sec = bar_sec  # on_tick uses this to know when to close a forming bar

        # 2) Translate "I want N bars of size X" into IB's durationStr ("60 S" or "1 D").
        total_sec = num_bars * bar_sec
        if total_sec >= 86400:
            duration_str = f"{max(1, total_sec // 86400)} D"
        else:
            duration_str = f"{max(60, total_sec)} S"

        # 3) Noise-lever -> observation-noise scale R. High scale => trust OU more.
        scale = noise_lever_to_scale(self.noise_lever_var.get())

        # 4) Cross-thread handshake setup.
        #    historical_data[reqId] will be populated by IBApp.historicalData callbacks
        #    fired on the IB reader thread. hist_done is a threading.Event that the IB
        #    thread will .set() when historicalDataEnd arrives. We clear both first so
        #    we don't see stale data from a prior request.
        self.ib.historical_data.clear()
        self.ib.hist_done.clear()

        # 4b) Same delayed-data opt-in we use for the live stream. Historical bars
        #     do NOT strictly require this — reqHistoricalData generally returns
        #     bars even without a live entitlement, because historical data has its
        #     own (usually free) entitlement tier. But setting it here keeps the
        #     two calls consistent: if the user picks a symbol with no live sub
        #     (e.g. BTC), historical still works, and the subsequent reqMktData
        #     won't error out with 10089. Calling reqMarketDataType more than
        #     once is safe — IB just remembers the latest choice.
        # Read the tier from the same UI combobox that toggle_stream uses, so
        # historical and live stay in lockstep when the user flips Live<->Delayed.
        self.ib.reqMarketDataType(self._mkt_data_type_code())

        # 5) Fire the async historical request (reqId=2). Returns immediately; the IB
        #    thread will start streaming bars into historical_data[2] via the
        #    IBApp.historicalData callback, terminated by historicalDataEnd which
        #    sets the hist_done event we wait on below.
        #
        # Args: (reqId, contract, endDateTime="", durationStr, barSizeSetting,
        #        whatToShow="TRADES", useRTH=1, formatDate=1, keepUpToDate=False,
        #        chartOptions=[]).
        #   whatToShow="TRADES" -> traded prices (for non-trading symbols like FX
        #     use "MIDPOINT" or "BID_ASK" instead, else IB returns no bars).
        #   useRTH=1 -> regular trading hours only. Set to 0 to include extended
        #     hours / 24h sessions (relevant for crypto, FX, futures on a Sunday).
        #   keepUpToDate=False -> one-shot pull, not a streaming subscription.
        # Pick whatToShow + useRTH by secType:
        #   STK    -> TRADES, RTH only (typical equity behavior).
        #   CASH   -> MIDPOINT, useRTH=0 (FX has no trades; 24h session).
        #   CRYPTO -> MIDPOINT, useRTH=0 (PAXOS quotes ~24/7, no "trade" data either).
        sectype = self.sectype_var.get().strip().upper()
        if sectype == "CASH":
            what_to_show, use_rth = "MIDPOINT", 0
        elif sectype == "CRYPTO":
            what_to_show, use_rth = "MIDPOINT", 0
        else:
            what_to_show, use_rth = "TRADES", 1
        self.ib.reqHistoricalData(2, self.contract(), "", duration_str, bar_size_setting, what_to_show, use_rth, 1, False, [])

        # 6) BLOCK the main thread until the IB thread signals "done" (or 20s timeout).
        #    This is why refresh_30m feels synchronous even though IB's API is async:
        #    we wait on the Event the IB thread will set. If the user clicked Refresh
        #    mid-stream, the Tk UI is unresponsive for up to 20s here — acceptable
        #    because this only runs at calibration time, not per-tick.
        self.ib.hist_done.wait(timeout=20)

        # 7) Pull the bars the IB thread deposited. If empty, abort with a popup.
        bars = self.ib.historical_data.get(2, [])
        if not bars:
            messagebox.showwarning("Data", "No historical bars received. Check symbol and market hours.")
            return

        # 8) Normalize bars: parse timestamps, coerce close to float, drop garbage rows.
        bar_list = []
        for b in bars:
            try:
                # IB sends bar dates like "20260524 17:55:00 US/Eastern" — single
                # space and an optional timezone token. Old code used [:16] +
                # '%Y%m%d  %H:%M' (two spaces) which silently failed for every
                # bar and dropped to datetime.now(), making hover tooltips lie.
                parts = b['date'].split()
                t = datetime.strptime(f"{parts[0]} {parts[1]}", '%Y%m%d %H:%M:%S')
            except Exception:
                t = datetime.now()
            try:
                c = float(b['close'])
            except (TypeError, ValueError):
                continue
            if not np.isfinite(c) or c <= 0:
                continue
            bar_list.append({'t': t, 'o': float(b['open']), 'h': float(b['high']), 'l': float(b['low']), 'c': c})
        if len(bar_list) < 5:
            messagebox.showwarning("Fit", "Need at least 5 valid bars for AR(1). Increase calib window or try different bar size.")
            return

        # 9) Fit AR(1) and build the Kalman filter.
        #    _recalibrate_from_bars(closes_window) -> (phi, mu, sigma, kalman, kalman_prices)
        #    It also writes self.phi/mu/sigma. The replayed kalman_prices give us a
        #    history of the orange "Kalman mean" dots aligned to historical bars, so
        #    the chart isn't empty before live ticks arrive.
        result = self._recalibrate_from_bars(bar_list, scale)
        if result is None:
            messagebox.showwarning("Fit", "Could not estimate AR(1) from bars. Increase calib window or check data.")
            return

        # 10) Commit the new model + seed chart state.
        #     IMPORTANT: this kalman object (phi, mu, sigma frozen at these values) is
        #     what on_tick will keep .update()-ing on every live tick. 
        #     Unless the user ticks "Recalibrate each bar",
        #     these parameters never change again until
        #     refresh_30m is called again (button or stream restart).
        self.kalman = result[3]
        self.kalman_prices = result[4]
        self.ohlc_bars = deque(bar_list, maxlen=self.max_bars)
        self.forecast_prices = self.kalman.forecast(5)
        self._update_ou_labels()
        self.redraw_chart()

    def _check_auto_signal(self, last_price):
        # ============================================================================
        # AUTO-TRADE SIGNAL CHECK — OU mean-reversion.
        # Called from on_tick at bar-close cadence (controlled by Eval-every-N-bars
        # dropdown). Returns silently if the signal is off, the model is not yet
        # calibrated, or the signal condition is not met. When the signal fires, an
        # order is DISPATCHED via root.after(0, ...) so it runs on the Tk main thread
        # (we are on the IB reader thread here — Tk widgets are NOT thread-safe).
        # ============================================================================

        # Gate 1: master toggle. If the user untoggled Auto-trade, do nothing.
        if not self.auto_trade_var.get():
            return
        # Gate 2: the model must be calibrated. Without phi/mu/sigma and a KalmanOU
        # instance, there are no bands and no forecast — nothing to act on.
        if self.kalman is None or self.sigma is None or self.mu is None:
            return
        # Gate 3: sanitize the price. Bad ticks (None / NaN / inf / 0) must never
        # reach the band comparison or the order dispatch.
        if last_price is None or not np.isfinite(last_price) or last_price <= 0:
            return

        # Read the band multiplier k from the slider — same value used by redraw_chart
        # to draw the dotted yellow bands, so the trader sees exactly what the algo
        # is reasoning about.
        k = float(self.band_mult_var.get())
        # Compute upper trading band: mu + k * sigma (stationary std).
        upper = self.mu + k * self.sigma
        # Compute lower trading band: mu - k * sigma.
        lower = self.mu - k * self.sigma

        # Read the forecast horizon H (number of OU steps to project forward).
        # Reuses the same Horizon control already shown in the chart frame, so the
        # purple cone on the chart and the signal evaluation point are consistent.
        try:
            H = max(1, int(self.forecast_horizon_var.get()))
        except (TypeError, ValueError):
            H = 5

        # Run the OU forecast: returns a list of H projected mean levels at steps
        # 1..H ahead. We take the LAST element (step H) — that is the level we
        # expect the process to be at after H bars if it evolves purely under OU.
        forecast_path = self.kalman.forecast(H)
        # Defensive: empty/short forecast list -> bail.
        if not forecast_path:
            return
        # `fc_end` = the H-step-ahead OU mean projection. This is the "where we
        # expect to be in H bars" number that the signal pivots on.
        fc_end = forecast_path[-1]
        # Reject non-finite forecasts (shouldn't happen with sane OU params).
        if not np.isfinite(fc_end):
            return

        # Core mean-reversion test: is the H-step forecast INSIDE the trading bands?
        # If yes, the OU model is telling us "we will be back inside [lower, upper]
        # within H steps" — i.e. any current band breach is expected to revert.
        forecast_inside = (lower <= fc_end <= upper)
        # If the forecast itself sits outside the bands, the model is implying the
        # breach is persistent (not mean-reverting in our horizon). Do nothing.
        if not forecast_inside:
            return

        # Decide trade direction based on which side of the bands the SPOT price is
        # currently on. The forecast-inside condition above already confirmed we
        # expect to revert back inward.
        # side: +1 = long, -1 = short, None = no trade.
        side = None
        # Spot price is ABOVE the upper band -> rich -> short, expect down-revert.
        if last_price > upper:
            side = -1
        # Spot price is BELOW the lower band -> cheap -> long, expect up-revert.
        elif last_price < lower:
            side = +1
        # Spot price already inside the bands -> no breach, nothing to fade.
        if side is None:
            return

        # ---- POSITION-SIZE CAP ----
        # We never let auto-trades push |position| past the user-set Max value.
        # The order quantity must match what place_trade actually submits, otherwise
        # the projected-position check below would be wrong:
        #   - CRYPTO: 0.001 (Decimal in place_trade, but we just need its magnitude).
        #   - STK / CASH: 10 units (matches the integer qty in place_trade).
        is_crypto = self.sectype_var.get().strip().upper() == "CRYPTO"
        # Per-fire order quantity (magnitude only — sign comes from `side`).
        step_qty = 0.001 if is_crypto else 10
        # Read the absolute cap from the Max |position| combobox. Coerce to float
        # so it can be compared to either int (STK) or fractional (CRYPTO) positions.
        try:
            cap = float(self.max_position_var.get())
        except (TypeError, ValueError):
            cap = 100.0
        # Signed delta this order would add to our running position.
        delta = side * step_qty
        # Projected new position if we DID place this order.
        projected = self.position + delta
        # Cap check: refuse the trade if it would breach |max position|. We also
        # surface a lightweight UI hint + log line so the user understands WHY a
        # visually-obvious band breach failed to fire.
        if abs(projected) > cap:
            dlog("auto_skip", reason="cap", pos=self.position, cap=cap,
                 side=side, last=last_price, fc_end=fc_end)
            # Tk widget mutation must happen on the main thread — schedule it.
            self.root.after(0, lambda: self.auto_status_lbl.config(
                text=f"auto: capped @{int(cap)}", fg='#d29922'))
            return

        # All gates passed. Log the fire event with everything a post-mortem needs:
        # direction, spot, H-step forecast, both bands, and the current position.
        dlog("auto_fire", side=side, last=last_price, fc_end=fc_end,
             upper=upper, lower=lower, pos=self.position, cap=cap, H=H)
        # Surface a one-line status in the GUI so the user sees the fire live.
        direction_str = "LONG" if side == 1 else "SHORT"
        self.root.after(0, lambda s=direction_str, p=last_price: self.auto_status_lbl.config(
            text=f"auto: {s} @ {p:.4g}", fg='#7ee787'))
        # Dispatch the trade on the Tk main thread. place_trade touches Tk widgets
        # (status labels, position counters) AND calls ibapi.placeOrder — we keep
        # all of that off the IB reader thread by going through root.after.
        # Bind `side` as a default arg so the lambda captures the value, not the
        # name (no late-binding surprise if this is somehow rescheduled).
        self.root.after(0, lambda s=side: self.place_trade(s))

    def place_trade(self, side):
        # ============================================================================
        # MANUAL order entry. There is NO automated signal logic anywhere in this file.
        # This method is only invoked via the three Tkinter buttons wired up in setup_ui:
        #   "Long"  -> place_trade( 1)   (BUY  100 shares market)
        #   "Short" -> place_trade(-1)   (SELL 100 shares market)
        #   "Close" -> place_trade( 0)   (flatten current position with opposite side)
        # The buttons use lambdas:
        #     ttk.Button(..., command=lambda: self.place_trade(1)).pack(...)
        # so Tk fires this method on the MAIN thread when the user clicks. on_tick does
        # NOT call place_trade — the Kalman mean is displayed but never compared to
        # price to generate a signal. If you wanted auto-trading, you'd add a check in
        # on_tick (e.g. if price < kalman.x - k*sigma) and dispatch the order via
        # root.after(0, lambda: self.place_trade(1)) so it lands on the main thread.
        # ============================================================================

        # Guard: need a live connection and an allocated order id from nextValidId.
        if not self.connected or self.ib.next_order_id is None:
            messagebox.showerror("Trade", "Not connected or no order ID.")
            return

        contract = self.contract()
        is_crypto = contract.secType == "CRYPTO"

        # Default open size:
        #   - STK/CASH: 100 units (shares / base-currency units).
        #   - CRYPTO: 0.001 BTC (~$70 at $70k BTC). 100 BTC default would be
        #     a multi-million-dollar order on PAXOS and instantly rejected.
        # Must be Decimal for ibapi >=10.19 to preserve fractional precision —
        # passing a float here can serialize as "0.0010000000000001" and break
        # tick-size validation on PAXOS.
        if is_crypto:
            qty = Decimal("0.001")
        else:
            qty = 10

        if side == 0:
            # CLOSE: flatten by sending the opposite-direction order for
            # |current position| units. If we're flat already, nothing to do.
            if self.position == 0:
                messagebox.showinfo("Close", "No position to close.")
                return
            # For crypto, self.position is stored as a number; wrap in Decimal
            # so the IOC limit order below carries fractional size correctly.
            qty = Decimal(str(abs(self.position))) if is_crypto else abs(self.position)
            order = Order()
            order.action = "SELL" if self.position > 0 else "BUY"
        else:
            # OPEN long (side=+1) or short (side=-1).
            order = Order()
            order.action = "BUY" if side == 1 else "SELL"

        order.totalQuantity = qty

        if is_crypto:
            # PAXOS (IB's crypto venue) DOES NOT accept MKT orders. It only
            # supports LMT with tif="IOC" (immediate-or-cancel marketable
            # limit). Submitting MKT here is the most common cause of silent
            # crypto-order rejection (error 201 / "Order rejected - reason:").
            #
            # Strategy: build a "marketable" limit by crossing the spread by a
            # small buffer so the IOC fills against the resting opposite side.
            # If the order doesn't fill within the IOC window, TWS cancels it
            # rather than resting it on the book.
            #
            # Buffer = 10 bps (0.1%). Tight enough to avoid bad fills, wide
            # enough to clear normal PAXOS spread (~5-20 bps on BTC/USD).
            ref = self.ib.ask if order.action == "BUY" else self.ib.bid
            if ref is None:
                # No top-of-book yet — fall back to last_price or refuse.
                ref = self.ib.last_price
            if ref is None:
                messagebox.showerror("Trade", "No price reference for crypto LMT order.")
                return
            buf = 1.001 if order.action == "BUY" else 0.999
            raw_px = ref * buf
            # PAXOS BTC/USD uses a coarse minTick (currently $0.50, sometimes
            # higher at very high BTC prices). Submitting a price with $0.01
            # precision triggers Error 110 "price does not conform to the
            # minimum price variation". Snap to the next valid grid point in
            # the safe direction: round UP for BUY (so the marketable limit
            # still crosses), round DOWN for SELL. Using $0.50 as a
            # conservative default — works for all current PAXOS crypto
            # symbols. For a more robust impl, query reqContractDetails and
            # use details.minTick.
            tick = 0.50
            if order.action == "BUY":
                # note that mat.ceil means round up: 
                # the fact that we are rounding up raw_px / tick means we are 
                # rounding up the number of ticks to quote
                snapped = math.ceil(raw_px / tick) * tick
            else:
                snapped = math.floor(raw_px / tick) * tick
            order.orderType = "LMT"
            order.lmtPrice = round(snapped, 2)
            order.tif = "IOC"
            # PAXOS quotes 24/7; without this flag, off-hours orders (which is
            # most of the time for crypto) get rejected as "outside RTH".
            order.outsideRth = True
        else:
            # Equities / FX: plain market order is fine.
            order.orderType = "MKT"

        # eTradeOnly / firmQuoteOnly were REMOVED from the Order class in
        # ibapi >=10.19. On older builds they default to True and block routing
        # for retail; on newer builds setting them either raises AttributeError
        # or makes TWS reject with "Error validating request" (321). Guard with
        # hasattr so the same code runs on both old and new ibapi.
        if hasattr(order, "eTradeOnly"):
            order.eTradeOnly = False
        if hasattr(order, "firmQuoteOnly"):
            order.firmQuoteOnly = False

        # Send the order to TWS. placeOrder is async — TWS will reply via
        # orderStatus/execDetails/openOrder callbacks (not handled in this file),
        # so we update our local "position" optimistically without waiting for fills.
        self.ib.placeOrder(self.ib.next_order_id, contract, order)
        # Each order needs a unique id; bump locally rather than re-asking IB.
        self.ib.next_order_id += 1

        # Optimistic local bookkeeping. If the order partially fills or is rejected,
        # this number will drift from reality — IBApp.position callbacks (populated by
        # reqPositions) are the authoritative source and are read on connect.
        # Cast qty to float for the running total so we don't accidentally mix
        # Decimal (crypto) with int (equity) in self.position arithmetic.
        delta_q = float(qty)
        self.position = 0 if side == 0 else (self.position + (delta_q if side == 1 else -delta_q))
        self.update_portfolio_display()

    def update_portfolio_display(self):
        self.pos_lbl.config(text=str(self.position))
        val = self.ib.account_value if self.ib.account_value is not None else self.cash
        self.port_lbl.config(text=f"{val:,.2f}")

    def redraw_chart(self):
        self.ax.clear()
        self.ax.set_facecolor('#161b22')
        self.ax.tick_params(colors='#8b949e')
        # Reattach hover annotation after ax.clear() (clear() removed it).
        if hasattr(self, '_hover_annot'):
            self._hover_annot = self.ax.annotate(
                "", xy=(0, 0), xytext=(12, 12), textcoords='offset points',
                bbox=dict(boxstyle='round,pad=0.4', fc='#161b22', ec='#58a6ff', alpha=0.95),
                color='#c9d1d9', fontsize=9, family='Consolas', zorder=10,
            )
            self._hover_annot.set_visible(False)
        has_current = self.current_bar is not None and (self.ohlc_bars or self.streaming)
        n = len(self.ohlc_bars)
        # Throttled snapshot log: at most once per 500ms.
        now_t = time.time()
        if DEBUG_LOG and (now_t - getattr(self, '_last_log_time', 0.0)) >= 0.5:
            self._last_log_time = now_t
            last_b = self.ohlc_bars[-1] if self.ohlc_bars else None
            kx = self.kalman.x if self.kalman is not None else None
            dlog("redraw", n=n, last_bar=last_b, cur_bar=self.current_bar,
                 kx=kx, mu=self.mu, sigma=self.sigma)
        if not self.ohlc_bars and not has_current:
            self.ax.set_ylabel("Price", color='#c9d1d9')
            self.ax.set_xlabel("Bar index", color='#8b949e')
            self.canvas.draw_idle()
            return
        if self.ohlc_bars:
            t = [b['t'] for b in self.ohlc_bars]
            o = [b['o'] for b in self.ohlc_bars]
            h = [b['h'] for b in self.ohlc_bars]
            l = [b['l'] for b in self.ohlc_bars]
            c = [b['c'] for b in self.ohlc_bars]
            for i in range(len(t)):
                color = '#3fb950' if c[i] >= o[i] else '#f85149'
                body_bottom, body_top = min(o[i], c[i]), max(o[i], c[i])
                # Doji fallback: zero-body candles need a visible sliver. The
                # previous absolute 0.01 was a 1-cent flake for equities but
                # ~1% of price on FX (0.01 ≈ 1000 pips on EUR/USD), drawing
                # candles ~1.164 → ~1.174. Use price-relative epsilon instead.
                height = body_top - body_bottom or max(abs(c[i]) * 1e-5, 1e-9)
                self.ax.plot([i, i], [l[i], body_bottom], color=color, linewidth=1)
                self.ax.plot([i, i], [body_top, h[i]], color=color, linewidth=1)
                self.ax.add_patch(Rectangle((i - 0.35, body_bottom), 0.7, height, facecolor=color, edgecolor=color))
            self.ax.plot(range(len(t)), c, color='#58a6ff', alpha=0.5, linewidth=0.8, label='Close')
        if has_current:
            i = n
            o_cur = self.current_bar[1]
            h_cur = self.current_bar[2]
            l_cur = self.current_bar[3]
            c_cur = self.current_bar[4]
            color = '#3fb950' if c_cur >= o_cur else '#f85149'
            body_bottom, body_top = min(o_cur, c_cur), max(o_cur, c_cur)
            # Same doji-sliver fix for the forming bar.
            height = body_top - body_bottom or max(abs(c_cur) * 1e-5, 1e-9)
            self.ax.plot([i, i], [l_cur, body_bottom], color=color, linewidth=1)
            self.ax.plot([i, i], [body_top, h_cur], color=color, linewidth=1)
            self.ax.add_patch(Rectangle((i - 0.35, body_bottom), 0.7, height, facecolor=color, edgecolor='#8b949e', linewidth=1.5))
        n_draw = n + (1 if has_current else 0)

        # ---- ORANGE DOTS: historical Kalman mean, one per closed bar. ----
        # self.kalman_prices is appended to in on_tick at every bar-close (or fully
        # rebuilt by _recalibrate_from_bars). Each entry = self.kalman.x at the
        # moment that bar closed. Anchored to the right edge of the bar history so
        # the most recent dot sits at index n-1.
        if self.kalman_prices and n > 0:
            k_len = min(len(self.kalman_prices), n)
            idx = list(range(n - k_len, n))
            self.ax.scatter(idx, self.kalman_prices[-k_len:], color='#f0883e', s=18, zorder=5, label='Kalman mean (history)')

        # ---- GREEN HORIZONTAL LINE: the LIVE Kalman mean (self.kalman.x). ----
        # This is what the user came for: the current mean-level estimate as of
        # the latest tick. on_tick mutates self.kalman.x via kalman.update(price);
        # the deferred redraw then reads it here. Spans the full x-axis as a
        # horizontal because "the OU level right now" has no x-coordinate — it's
        # a scalar that we're claiming is fair value at this instant.
        if self.kalman is not None and np.isfinite(self.kalman.x):
            self.ax.axhline(y=self.kalman.x, color='#7ee787', linestyle='-', linewidth=2, alpha=0.95, zorder=4, label='Live mean level (Kalman x)')

        # ---- GREY DASHED LINE: long-run OU mean μ from the calibration window. ----
        # Frozen unless online recalibration is enabled. Useful visual reference
        # for how far the live KF mean has drifted from the calibration anchor.
        if self.mu is not None and np.isfinite(self.mu):
            self.ax.axhline(y=self.mu, color='#8b949e', linestyle='--', linewidth=1, alpha=0.7, zorder=3, label='Long-run mean (μ)')

        # ---- TRADING BANDS: μ ± k * σ (stationary std) ----
        # σ from estimate_ar1 is the stationary-level std. Bands give a quick
        # visual reference for "rich"/"cheap" vs OU equilibrium. k toggleable
        # via slider; visibility via checkbox.
        if (self.show_bands_var.get() and self.mu is not None and self.sigma is not None
                and np.isfinite(self.mu) and np.isfinite(self.sigma)):
            k = float(self.band_mult_var.get())
            upper = self.mu + k * self.sigma
            lower = self.mu - k * self.sigma
            self.ax.axhline(y=upper, color='#d29922', linestyle=':', linewidth=1.2,
                            alpha=0.8, zorder=3, label=f'Upper band (μ + {k:.2f}σ)')
            self.ax.axhline(y=lower, color='#d29922', linestyle=':', linewidth=1.2,
                            alpha=0.8, zorder=3, label=f'Lower band (μ − {k:.2f}σ)')

        # ---- OU FORECAST: dots OR line + ±1σ cone ----
        # Mean path: x_k = μ + φ^k * (x - μ), k = 1..H (= KalmanOU.forecast).
        # k-step conditional variance (OU):
        #   Var(x_{t+k}|x_t) = σ_eps^2 * (1 - φ^(2k)) / (1 - φ^2)
        #                    = σ_stationary^2 * (1 - φ^(2k))
        # 1σ cone half-width at step k: σ * sqrt(1 - φ^(2k)). As k→∞, → σ.
        if self.show_forecast_bounds_var.get() and self.kalman is not None and self.sigma is not None:
            H = max(1, int(self.forecast_horizon_var.get()))
            mean_path = self.kalman.forecast(H)
            phi = self.kalman.phi
            ks = np.arange(1, H + 1)
            cone = self.sigma * np.sqrt(np.maximum(1.0 - phi ** (2 * ks), 0.0))
            fc_x = list(range(n_draw, n_draw + H))
            upper_fc = np.array(mean_path) + cone
            lower_fc = np.array(mean_path) - cone
            self.ax.plot(fc_x, mean_path, color='#a371f7', linewidth=1.6,
                         alpha=0.9, zorder=5, label='OU forecast')
            self.ax.fill_between(fc_x, lower_fc, upper_fc, color='#a371f7', alpha=0.18,
                                 zorder=4, linewidth=0, label='OU ±1σ cone')
        elif self.forecast_prices:
            fc_x = list(range(n_draw, n_draw + len(self.forecast_prices)))
            self.ax.scatter(fc_x, self.forecast_prices, color='#a371f7', s=22, zorder=5, label='OU forecast')
        self.ax.set_ylabel("Price", color='#c9d1d9')
        self.ax.set_xlabel("Bar index", color='#8b949e')
        handles, labels = self.ax.get_legend_handles_labels()
        if handles:
            self.ax.legend(loc='upper left', fontsize=8)
        self.canvas.draw_idle()

    def refresh_timer(self):
        self.update_portfolio_display()
        self.root.after(2000, self.refresh_timer)


def main():
    root = tk.Tk()
    app = KalmanTradingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
