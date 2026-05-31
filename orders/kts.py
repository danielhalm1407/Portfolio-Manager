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

import sys
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import os
import pathlib
import math
import statistics
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle
import matplotlib.dates as mdates
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

# kts.py is a runnable script living in orders/ (not an installed package), so we
# add the repo's src/ directory to sys.path before importing the shared portutils
# library. parents[1] of orders/kts.py is the repo root; the library lives under
# src/portutils. This lets us reuse the single IBApp / order builders / historical
# helper that now own ALL the IBKR plumbing, instead of kts maintaining its own
# duplicate IBApp. (Side effects in a runnable script are fine — see src/CLAUDE.md;
# only library code must stay import-clean.)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from portutils.ingestion.ibkr_requests import (
    IBApp,                          # the single, shared EClient+EWrapper app
    OrderApp,                       # thread-safe order placement (locked id alloc)
    contract as build_contract,     # Contract builder (aliased so self.contract() can wrap it)
    market_order,                   # STK/CASH market order builder
    crypto_marketable_limit_order,  # PAXOS IOC marketable-limit builder
    get_historical_bars,            # one-shot intraday OHLCV pull (list[dict])
)

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
# RISK/RETURN VIEW OBJECT (plan §7.1 — "per-asset forecast distribution object").
# This is the single bundle the recommendation panel renders. It carries BOTH the
# forecast distribution (expected return + worst/best case) AND the concrete trade
# that distribution implies (direction, size, weight + value deltas). Computed once
# per bar close by _compute_recommendation and handed straight to
# _update_recommendation_panel — so the panel and the chart cone always agree
# because they were derived from the same model state at the same instant.
# -----------------------------------------------------------------------------
@dataclass
class ForecastView:
    # --- distribution (the "what do we expect" half) ---
    r_hat_h: float          # expected H-step simple return  (central/p_now - 1)
    q_low: float            # worst-case return  to reasonably account for (low quantile)
    q_high: float           # best-case  return  to reasonably account for (high quantile)
    central_price: float    # H-step OU mean projection (the purple cone's endpoint)
    mu_now: float           # current long-run mean μ (reversion target right now)
    p_now: float            # spot price the return/weights are measured against
    # --- trade (the "so what do we do" half — §3c order spec) ---
    direction: str          # "long" / "short" / "flat"
    qty: float              # signed units to trade to reach the target weight
    w_current: float        # current portfolio weight (value_current / account_value)
    w_target: float         # risk-capped target weight from r_hat_h
    w_delta: float          # w_target - w_current (what the recommendation moves)
    value_current: float    # $ exposure now
    value_target: float     # $ exposure after the trade
    value_delta: float      # $ to trade (value_target - value_current)
    binding_cap: str        # which cap set w_target: "none"/"idio_floor"/"factor"/"portfolio"


# -----------------------------------------------------------------------------
# FILL — one execution print, the atomic unit the accounting engine consumes
# (plan §7.3). Deliberately shaped to be a SUPERSET of what IBKR's execDetails
# returns (order_id/qty/price/side/time/perm_id) PLUS our own extras (source +
# the forecast context at decision time) so the SAME object works for a simulated
# replay fill and, later, a live IBKR fill — apply_fill never has to branch on which.
# -----------------------------------------------------------------------------
@dataclass
class Fill:
    order_id: int           # which order this fill belongs to (our counter in sim)
    ts: datetime            # fill timestamp (bar timestamp in replay)
    side: int               # +1 = BUY, -1 = SELL  (signed so apply_fill is direction-agnostic)
    qty: float              # ABSOLUTE units filled (magnitude only; sign carried by `side`)
    price: float            # fill price
    perm_id: int | None = None          # IBKR permId — None in pure sim
    source: str = "sim"                 # "sim" (replay) vs "live" (real IBKR fill) — our provenance field
    # forecast context stamped at decision time — OUR extra fields beyond IBKR (§7.3).
    r_hat: float | None = None
    q_low: float | None = None
    q_high: float | None = None
    binding_cap: str | None = None


# -----------------------------------------------------------------------------
# SIM EXECUTION BACKEND (plan §7.3, replay half only).
# Backend-agnostic design: the accounting engine (KalmanTradingApp.apply_fill)
# only ever sees a Fill, never knows whether it came from IBKR or from here. This
# backend is the REPLAY/PAPER source: given a recommended trade it synthesizes a
# Fill at the bar's price (no network, no order id from TWS) and funnels it through
# apply_fill. The LiveExecutionBackend (not built here — out of scope for the
# simulated path) would instead call OrderApp.place_order and build Fills from the
# execDetails callbacks. Keeping them behind one `execute` signature means the GUI
# code that requests a trade is identical in both modes.
# -----------------------------------------------------------------------------
class SimExecutionBackend:
    def __init__(self, app):
        # Hold a back-reference to the owning KalmanTradingApp so we can funnel the
        # synthesized fill into its apply_fill (the single mutator of position/P&L).
        self.app = app
        # Local monotone order-id counter. In sim there is no TWS to hand out ids,
        # so we mint our own starting at 1 — purely a ledger key, never sent anywhere.
        self._next_id = 1

    def execute(self, side, qty, price, ts, ctx=None):
        # Synthesize an immediate fill at `price` (no slippage model yet — a future
        # extension per §4c). `side` is +1/-1, `qty` is the absolute size. `ctx` is an
        # optional ForecastView whose context we stamp onto the fill (our extra fields).
        # Mint the next sim order id and advance the counter.
        oid = self._next_id
        self._next_id += 1
        # Build the Fill. source="sim" tags provenance so replay rows are
        # distinguishable from live ones later in the same state_df.
        fill = Fill(
            order_id=oid, ts=ts, side=int(side), qty=abs(float(qty)),
            price=float(price), source="sim",
            # Stamp the forecast context (if a recommendation drove the trade) so the
            # ledger remembers WHY each fill happened — our edge over IBKR's bare prints.
            r_hat=(ctx.r_hat_h if ctx is not None else None),
            q_low=(ctx.q_low if ctx is not None else None),
            q_high=(ctx.q_high if ctx is not None else None),
            binding_cap=(ctx.binding_cap if ctx is not None else None),
        )
        # Route through the app's accounting engine — the ONLY place position/P&L move.
        self.app.apply_fill(fill)
        return fill


# -----------------------------------------------------------------------------
# RISK / RETURN ENGINE CONSTANTS (plan §7.1 + §3a).
# Quantile probabilities define how wide "worst/best case to reasonably account
# for" is; the REC_* constants parameterise the v1 piecewise-linear weight map.
# Kept module-level so the panel, the chart bands, and auto-trade all agree.
# -----------------------------------------------------------------------------
Q_LOW_P = 0.05          # low quantile = worst-case return (≈ P5)
Q_HIGH_P = 0.95         # high quantile = best-case return (≈ P95)
REC_ALPHA = 2.0         # slope mapping expected return -> target weight (w = α·r̂)
REC_W_CAP = 0.15        # max |target weight| when downside risk is benign (15%)
REC_QLOW_FLOOR = -0.05  # if worst-case return < this (-5%), cap collapses to 0 (no trade)


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
        # OrderApp wrapper around self.ib; built once in connect_ib after the
        # handshake (it refuses an unconnected app). None until then.
        self.order_app = None
        # reqId for the live market-data subscription. The library hands out ids
        # from a counter that STARTS AT 1, and we must not clash with it: a
        # hardcoded reqId=1 (as the old kts used) would collide with the first
        # next_req_id() a historical pull allocates. So we reserve a real id from
        # the shared counter when the stream opens (toggle_stream) and reuse it for
        # the matching cancelMktData. None while not streaming.
        self._mkt_data_req_id = None
        self._chart_update_scheduled = False
        self._last_redraw_time = 0.0

        # ---- SIMULATED / REPLAY ACCOUNTING STATE (plan §7.3, replay half) ----
        # When sim_mode is on, trades do NOT go to IBKR — they are filled locally by
        # SimExecutionBackend and booked through apply_fill, which is the SOLE mutator
        # of the figures below. self.position (the optimistic live counter) is left
        # untouched in sim mode; these sim_* fields are the source of truth instead.
        #   sim_position    — signed open units (long > 0, short < 0).
        #   sim_avg_entry   — VWAP of the CURRENTLY-OPEN units (0 when flat).
        #   sim_realised    — cumulative crystallised P&L from closed units.
        # Unrealised P&L is derived on the fly (last_price vs sim_avg_entry), never stored
        # as authoritative — it changes every tick, so we recompute it when marking.
        self.sim_position = 0.0
        self.sim_avg_entry = 0.0
        self.sim_realised = 0.0
        # Units closed in the bar currently being marked — reset each bar, used by the
        # position-breakdown plot's "closed_units" scatter markers (§4c plot 2).
        self._sim_closed_this_bar = 0.0
        # Backend that turns a recommended/clicked trade into a Fill (no network).
        self.exec_backend = SimExecutionBackend(self)
        # STATE DATAFRAME — the single source of truth for ALL replay plots (§4b/§4c).
        # One row per marked bar, indexed by bar timestamp. The top widgets (position
        # + P&L) and every new axis are just VIEWS of this frame's latest row / columns.
        # Columns mirror the §4b table plus a few derived/contextual extras.
        self.state_df = pd.DataFrame(
            columns=[
                "position", "avg_entry_price", "entry_cost", "mark_value",
                "unrealised_pnl", "realised_pnl", "total_pnl", "last_price",
                "intended", "filled", "unfilled", "closed_units",
                # forecast context at the bar (our extras beyond IBKR) — for inspection.
                "r_hat", "q_low", "q_high",
            ]
        )

        # ---- TOGGLE VARS for the new stacked plots (§7.2) ----
        # Each flips a line's set_visible without a full recompute. Defaults keep the
        # P&L decomposition and position breakdown visible once state exists; price
        # chart is always on.
        self.show_realised_var = tk.BooleanVar(value=True)
        self.show_unrealised_var = tk.BooleanVar(value=True)
        self.show_total_var = tk.BooleanVar(value=True)
        self.show_filled_var = tk.BooleanVar(value=True)
        self.show_intended_var = tk.BooleanVar(value=True)
        self.show_entry_var = tk.BooleanVar(value=True)
        # Master switch for the lower analytics axes. Off by default so the app opens
        # looking exactly like before (price only); flip on to reveal P&L/position/entry.
        self.show_analytics_var = tk.BooleanVar(value=True)
        # Paper/simulated trading switch. Default ON so the accounting engine + replay
        # plots work with NO IBKR connection (pure replay). Off = route orders live.
        self.sim_mode_var = tk.BooleanVar(value=True)

        # ForecastView most recently computed — cached so manual redraws can reuse it
        # for the quantile bands without recomputing the model. None until first bar.
        self._last_forecast_view = None

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
        # Chart now lives on row 7 (a Recommendation frame was inserted at row 6),
        # so it is row 7 that must stretch to fill vertical space.
        main.rowconfigure(7, weight=1)

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
        # Paper/simulated trading toggle. ON (default) = trades are filled locally by
        # SimExecutionBackend and booked into state_df (no IBKR order goes out), so the
        # whole risk/return + P&L stack works with no live connection. OFF = route to
        # IBKR via OrderApp (the original live path). Placed here so it sits with the
        # other trade controls and the user can see at a glance which mode they're in.
        ttk.Checkbutton(row_auto, text="Simulated (paper)",
                        variable=self.sim_mode_var).pack(side='right', padx=(12, 0))

        # ---- RECOMMENDATION PANEL (plan §7.1, PRIORITY 1) ----
        # Surfaces the DECISION before any plotting: expected return, worst case (red),
        # best case (green), and the concrete recommended order (direction + size +
        # weight/value deltas) plus which cap is currently binding. Shown in BOTH manual
        # and auto modes so the user sees exactly what auto-trade would dispatch before
        # clicking anything. Populated by _update_recommendation_panel(fv) each bar close.
        rec = ttk.LabelFrame(main, text="Recommendation — expected / worst / best case + suggested trade", padding=8)
        rec.grid(row=6, column=0, sticky='ew', pady=(0, 8))
        rec_row = ttk.Frame(rec)
        rec_row.grid(row=0, column=0, sticky='ew')
        # Expected H-step return — neutral colour (it's the central estimate, not a risk).
        ttk.Label(rec_row, text="E[r]:").pack(side='left', padx=(0, 2))
        self.rec_ret_lbl = tk.Label(rec_row, text="—", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#c9d1d9')
        self.rec_ret_lbl.pack(side='left', padx=(0, 12))
        # Worst-case return (low quantile) — red, intensity scales with downside size.
        ttk.Label(rec_row, text="worst:").pack(side='left', padx=(0, 2))
        self.rec_worst_lbl = tk.Label(rec_row, text="—", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#f85149')
        self.rec_worst_lbl.pack(side='left', padx=(0, 12))
        # Best-case return (high quantile) — green, intensity scales with upside size.
        ttk.Label(rec_row, text="best:").pack(side='left', padx=(0, 2))
        self.rec_best_lbl = tk.Label(rec_row, text="—", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#3fb950')
        self.rec_best_lbl.pack(side='left', padx=(0, 16))
        # The concrete recommended order (direction + qty + value delta) — the §3c spec
        # collapsed to one readable line; this is exactly what auto-trade would send.
        ttk.Label(rec_row, text="trade:").pack(side='left', padx=(0, 2))
        self.rec_trade_lbl = tk.Label(rec_row, text="—", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#58a6ff')
        self.rec_trade_lbl.pack(side='left', padx=(0, 16))
        # Which cap (if any) is binding the target weight — explains a flat/clipped call.
        ttk.Label(rec_row, text="cap:").pack(side='left', padx=(0, 2))
        self.rec_cap_lbl = tk.Label(rec_row, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.rec_cap_lbl.pack(side='left')

        chart_frame = ttk.LabelFrame(main, text="OHLC | Orange = Kalman mean (history) | Solid line = live mean (updates every tick) | Purple = OU forecast", padding=6)
        chart_frame.grid(row=7, column=0, sticky='nsew')
        chart_frame.columnconfigure(0, weight=1)
        # Row 0 = plot-series toggles, row 1 = the canvas (which stretches).
        chart_frame.rowconfigure(1, weight=1)

        # ---- PLOT TOGGLE ROW (§7.2 series visibility, Tk checkbuttons) ----
        # Flipping these calls redraw_chart, which respects each var via set_visible /
        # by skipping a sub-axis. Lets the user isolate, e.g., just total P&L.
        toggles = ttk.Frame(chart_frame)
        toggles.grid(row=0, column=0, sticky='ew', pady=(0, 4))
        ttk.Checkbutton(toggles, text="Analytics axes", variable=self.show_analytics_var,
                        command=self._rebuild_chart_axes).pack(side='left', padx=(0, 12))
        ttk.Checkbutton(toggles, text="Realised", variable=self.show_realised_var,
                        command=self.redraw_chart).pack(side='left', padx=(0, 8))
        ttk.Checkbutton(toggles, text="Unrealised", variable=self.show_unrealised_var,
                        command=self.redraw_chart).pack(side='left', padx=(0, 8))
        ttk.Checkbutton(toggles, text="Total P&L", variable=self.show_total_var,
                        command=self.redraw_chart).pack(side='left', padx=(0, 12))
        ttk.Checkbutton(toggles, text="Filled", variable=self.show_filled_var,
                        command=self.redraw_chart).pack(side='left', padx=(0, 8))
        ttk.Checkbutton(toggles, text="Intended", variable=self.show_intended_var,
                        command=self.redraw_chart).pack(side='left', padx=(0, 12))
        ttk.Checkbutton(toggles, text="Entry vs mkt", variable=self.show_entry_var,
                        command=self.redraw_chart).pack(side='left', padx=(0, 8))

        self.chart_container = ttk.Frame(chart_frame)
        self.chart_container.grid(row=1, column=0, sticky='nsew')
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

    def _style_ax(self, ax):
        # Apply the shared dark GitHub-dark palette to one axis. Factored out because
        # we now have up to four stacked axes (price + P&L + position + entry) instead
        # of one, and every one of them needs the same facecolor / tick / spine colours.
        ax.set_facecolor('#161b22')
        ax.tick_params(colors='#8b949e')
        for side in ('bottom', 'top', 'left', 'right'):
            ax.spines[side].set_color('#30363d')

    def _build_axes(self):
        # (Re)build the axis layout on self.fig. Called by setup_chart and whenever the
        # "Analytics axes" toggle flips (_rebuild_chart_axes). Two layouts:
        #   • analytics ON  -> 4 stacked, shared-x axes (plan §7.2): price (tall),
        #     P&L decomposition, position breakdown, entry-vs-market. sharex=True so
        #     panning/zooming the price axis moves all of them together, and they line
        #     up bar-for-bar (everything is plotted against the same bar index).
        #   • analytics OFF -> a single price axis, identical to the original app.
        # We clear the figure first so toggling doesn't leak orphaned axes.
        self.fig.clear()
        if self.show_analytics_var.get():
            # height_ratios: price dominates; the three analytics panes are short.
            gs = self.fig.add_gridspec(4, 1, height_ratios=[3, 1, 1, 1], hspace=0.08)
            self.ax = self.fig.add_subplot(gs[0])
            self.ax_pnl = self.fig.add_subplot(gs[1], sharex=self.ax)
            self.ax_pos = self.fig.add_subplot(gs[2], sharex=self.ax)
            self.ax_entry = self.fig.add_subplot(gs[3], sharex=self.ax)
            for ax in (self.ax, self.ax_pnl, self.ax_pos, self.ax_entry):
                self._style_ax(ax)
            # Hide the x tick labels on every axis except the bottom one — they share x,
            # so repeating the labels four times is noise.
            for ax in (self.ax, self.ax_pnl, self.ax_pos):
                ax.tick_params(labelbottom=False)
        else:
            # Single-axis fallback: the analytics axes simply don't exist.
            self.ax = self.fig.add_subplot(1, 1, 1)
            self.ax_pnl = self.ax_pos = self.ax_entry = None
            self._style_ax(self.ax)

        # Hover annotation lives on the PRICE axis only. Rebuilt here because fig.clear()
        # destroyed any previous one. Single reusable Annotation toggled visible/invisible.
        self._hover_annot = self.ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords='offset points',
            bbox=dict(boxstyle='round,pad=0.4', fc='#161b22', ec='#58a6ff', alpha=0.95),
            color='#c9d1d9', fontsize=9, family='Consolas', zorder=10,
        )
        self._hover_annot.set_visible(False)

    def _rebuild_chart_axes(self):
        # Toggle handler for "Analytics axes": rebuild the layout then repaint. Kept
        # separate from redraw_chart because redraw assumes the axes already exist;
        # this is the only place that changes how many axes there ARE.
        self._build_axes()
        self.redraw_chart()

    def setup_chart(self):
        plt.style.use('dark_background')
        # Create the figure + canvas ONCE; the axes inside are (re)built by _build_axes
        # so we can switch between the single-axis and stacked-analytics layouts without
        # tearing down the Tk canvas.
        self.fig = plt.figure(figsize=(11, 6), facecolor='#0d1117')
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_container)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky='nsew')
        self._build_axes()

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
        # of 10089 / "no data" errors. secType + exchange come from the UI.
        #
        # We delegate the actual Contract construction to the library builder
        # (portutils.ingestion.ibkr_requests.contract, imported as build_contract)
        # so the Contract field-setting lives in ONE place shared with every other
        # request workflow. This method just reads the current Tk variables and
        # forwards them; the GUI keeps owning "what the user picked".
        return build_contract(
            symbol=self.symbol_var.get().strip().upper(),
            sec_type=self.sectype_var.get().strip().upper(),
            exchange=self.exchange_var.get().strip().upper(),
            currency="USD",
        )

    def _on_sectype_change(self, _evt):
        # Convenience: snap exchange to the conventional default whenever the user
        # changes the secType dropdown. They can still override afterward.
        mapping = {"STK": "SMART", "CASH": "IDEALPRO", "CRYPTO": "PAXOS"}
        self.exchange_var.set(mapping.get(self.sectype_var.get(), "SMART"))

    def connect_ib(self):
        # Already connected? Nothing to do — guard against a double-click on Connect.
        if self.connected:
            return
        try:
            # ── Part 1: open the connection via the shared library ──────────────
            # The raw connect + daemon-thread + poll loop that used to live here is
            # GONE — the library does all of it (and more) inside IBApp.start():
            #   • start() runs the module-level connect_ib(), which spins app.run()
            #     on a daemon reader thread and polls up to 10 s for the handshake,
            #     with an extra serverVersion()>0 guard the old kts loop lacked;
            #   • start() also auto-retries the client id on error 326
            #     (client_id -> +1 -> +2) if TWS says the id is already in use.
            # We pass our own UI-driven params; client_id 98 keeps this GUI on its
            # own id so it doesn't collide with notebook/pipeline sessions on 123.
            # start() raises ConnectionError on failure, caught below.
            self.ib.start(
                host=self.host_var.get(),
                port=int(self.port_var.get()),
                client_id=98,
            )

            # ── Part 2: GUI bookkeeping + post-connect seeding (stays in the GUI) ─
            # These touch Tk widgets and app state, so they CANNOT move into the
            # library — they live here by design.
            #
            # Mark our own connected flag. NOTE: self.ib.connected is a SEPARATE
            # flag living on the IBApp; we don't set it here. It is flipped to True
            # by the IBApp.nextValidId() EWrapper callback (the first message TWS
            # sends after the handshake) — possible only because self.ib is an
            # EClient+EWrapper, so it RECEIVES that callback on the reader thread.
            # start() already blocked until that happened, so by now it is True.
            self.connected = True
            # Seed our local order id from the value nextValidId() populated on the
            # app. (Order placement now goes through OrderApp, which reserves ids
            # under a lock — see place_trade — but we keep this for display.)
            self.order_id = self.ib.next_order_id
            # OrderApp wraps the live, connected app and serialises order-id
            # reservation. Build it ONCE here, now that app.connected is True
            # (OrderApp.__init__ refuses an unconnected app).
            self.order_app = OrderApp(self.ib)
            # Flip the toolbar button states to the connected configuration.
            self.connect_btn.config(state='disabled')
            self.disc_btn.config(state='normal')
            self.stream_btn.config(state='normal')
            self.refresh_btn.config(state='normal')
            self.status_lbl.config(text="● Connected", fg='#3fb950')
            # Fire two snapshot requests right after connect. Besides seeding the
            # data we want, a successful round-trip is our manual confirmation that
            # the API link really is live (start() proves the handshake; these
            # prove we can actually request and receive):
            #   • reqAccountSummary -> accountSummary() callback fills
            #     app.account_value (NetLiquidation), shown in the portfolio label;
            #   • reqPositions -> position() callbacks fill app.positions, so the
            #     GUI can display our current holding for the active symbol (and,
            #     later, surface the whole portfolio elsewhere in the UI).
            self.ib.reqAccountSummary(9001, "All", "NetLiquidation")
            self.ib.reqPositions()
            # Give the callbacks a brief moment to arrive on the reader thread
            # before we read app.positions below (these are async; 0.3 s is enough
            # for a local TWS round-trip without freezing the UI noticeably).
            time.sleep(0.3)
            # Pre-load our position/entry price for the symbol currently in the box,
            # so the GUI starts in sync with the real account rather than flat.
            # NOTE: the library keys app.positions by (account, conId or symbol) —
            # NOT by bare symbol as the old kts IBApp did — so we scan the values
            # for a matching symbol instead of doing a direct dict lookup.
            sym = self.symbol_var.get().strip().upper()
            for pos in self.ib.positions.values():
                if pos.get('symbol') == sym:
                    self.position = int(pos['position'])
                    self.entry_price = pos['avgCost']
                    break
        except Exception as e:
            # start() raises ConnectionError on failure; any other setup error also
            # lands here. Surface it in the same popup the old code used.
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
            # Cancel the live subscription using the SAME reqId we opened it with
            # (allocated from the shared counter in toggle_stream), not a literal 1.
            if self._mkt_data_req_id is not None:
                self.ib.cancelMktData(self._mkt_data_req_id)
                self._mkt_data_req_id = None
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
        # Wipe the sim/replay ledger + state_df so a fresh symbol/run starts from a
        # clean book (plan §7.3). Also resets the recommendation panel below.
        self._reset_accounting()
        if hasattr(self, 'rec_ret_lbl'):
            self._update_recommendation_panel(None)
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
            # cancelMktData(reqId) tells TWS to stop sending ticks for the request that was
            # opened below with reqMktData(reqId, ...). It is NOT a loop — it's a single cancel
            # message. After this, tickPrice callbacks for that reqId stop arriving, so on_tick
            # is no longer fed. The IB reader thread itself keeps running (still alive for
            # historical data, account updates, orders); only this market-data stream stops.
            # We cancel with the reqId we actually opened with (from next_req_id), not a
            # literal 1, so we don't accidentally cancel some other request.
            if self._mkt_data_req_id is not None:
                self.ib.cancelMktData(self._mkt_data_req_id)
                self._mkt_data_req_id = None
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
            # Allocate a UNIQUE reqId for this subscription from the app's shared
            # counter. This is the Blocker #6 fix: the counter starts at 1, so a
            # hardcoded reqId=1 here would collide with the first id a historical
            # pull (get_historical_bars -> next_req_id) hands out, and TWS would
            # mis-route ticks vs bars. We stash it so the STOP branch / clear_chart
            # cancel the exact same id.
            self._mkt_data_req_id = self.ib.next_req_id()
            # reqMktData opens a streaming subscription. The reqId we just reserved
            # is the handle we later pass to cancelMktData. After this returns, the
            # IB reader thread will start invoking IBApp.tickPrice for every tick —
            # which in turn calls self.on_tick (see below). This is the "engine":
            # from here on, every tick from TWS drives one pass through on_tick.
            # No polling, no while loop on our side.
            #
            # Args: (reqId, contract, genericTickList="", snapshot=False,
            #        regulatorySnapshot=False, mktDataOptions=[]).
            # snapshot=False => streaming (what we want). True would deliver one frame.
            self.ib.reqMktData(self._mkt_data_req_id, self.contract(), "", False, False, [])
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
        # in the below getattr, "_last_tick_source" is an optional attribute 
        # that IBApp.tickPrice sets to "LAST" or "BID/ASK/MID" depending on the tickType. 
        # It's just for logging/debugging so we can see which feed the tick came from; 
        # it has no functional role.
        # note that the syntax of getattr(obj, "attr", default) means "get obj.attr 
        # if it exists, else return default".
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

            # ---- SIM/REPLAY: mark the book + refresh the decision at bar close ----
            # Capture the CLOSED bar's timestamp and closing price BEFORE we reset
            # bar_start to the new forming bar below. Then dispatch the recommendation
            # refresh + state_df mark onto the Tk main thread (we're on the IB reader
            # thread here; both touch Tk widgets / pandas state owned by the main thread).
            closed_bar_ts = self.bar_start
            self.root.after(0, lambda t=closed_bar_ts, p=price: self._on_bar_close(t, p))

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

        # 4) Delayed-data opt-in (same tier we use for the live stream). Historical
        #    bars do NOT strictly require this — reqHistoricalData generally returns
        #    bars even without a live entitlement, because historical data has its
        #    own (usually free) tier. But setting it keeps historical and live in
        #    lockstep when the user flips Live<->Delayed, and means a symbol with no
        #    live sub (e.g. BTC) still calibrates and won't 10089 on the later
        #    reqMktData. Calling reqMarketDataType more than once is safe — IB keeps
        #    the latest choice.
        self.ib.reqMarketDataType(self._mkt_data_type_code())

        # 5) Pick whatToShow + useRTH by secType (TRADES on a non-trading symbol
        #    returns zero bars, so this matters):
        #      STK            -> TRADES,   RTH only (typical equity behavior).
        #      CASH (FX)      -> MIDPOINT, useRTH=0 (FX has no trades; 24h session).
        #      CRYPTO (PAXOS) -> MIDPOINT, useRTH=0 (~24/7, no "trade" data either).
        sectype = self.sectype_var.get().strip().upper()
        if sectype in ("CASH", "CRYPTO"):
            what_to_show, use_rth = "MIDPOINT", 0
        else:
            what_to_show, use_rth = "TRADES", 1

        # 6) Pull the bars via the shared library helper. This REPLACES the old
        #    hand-rolled reqId=2 + hist_done handshake: get_historical_bars allocates
        #    a UNIQUE reqId from the app counter and uses its own per-request Event,
        #    so it can never collide with the live market-data subscription. It still
        #    BLOCKS the Tk main thread until historicalDataEnd (or a 20s timeout) —
        #    acceptable because this only runs at calibration time, not per-tick —
        #    and returns a list of bar dicts (keys datetime/open/high/low/close/volume).
        bars = get_historical_bars(
            self.ib, self.contract(),
            duration=duration_str, bar_size=bar_size_setting,
            what_to_show=what_to_show, use_rth=use_rth, timeout=20,
        )
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
                # NOTE: the library helper stores the date under the 'datetime' key
                # (matching historicalData()), not 'date' as the old kts IBApp did.
                parts = b['datetime'].split()
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
        # Projected new position if we DID place this order. Use the mode-appropriate
        # current position: the sim accounting engine's figure in paper mode, the
        # optimistic live counter otherwise — so the cap reflects the book we actually
        # have in the active mode (in sim self.position stays 0 and would never cap).
        cur_pos = self.sim_position if self.sim_mode_var.get() else self.position
        projected = cur_pos + delta
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
        # Order entry. Invoked two ways, BOTH on the Tk MAIN thread:
        #   (a) the three Tkinter buttons wired up in setup_ui:
        #         "Long"  -> place_trade( 1)   (BUY  open)
        #         "Short" -> place_trade(-1)   (SELL open)
        #         "Close" -> place_trade( 0)   (flatten current position)
        #       via lambdas, e.g. ttk.Button(..., command=lambda: self.place_trade(1));
        #   (b) the auto-trade signal: _check_auto_signal (on the IB reader thread)
        #       dispatches self.root.after(0, lambda s=side: self.place_trade(s)) so the
        #       actual order placement still lands on the Tk main thread.
        # Order CONSTRUCTION is delegated to the shared library builders, and SENDING
        # goes through OrderApp (which reserves the order id under a lock) — so this
        # method only does GUI-side concerns: sizing, the crypto price reference, and
        # optimistic position bookkeeping.
        # ============================================================================

        # Mode split. In SIMULATED (paper) mode trades are filled locally — no IBKR,
        # so no connection / order id is required; pure replay works offline. In LIVE
        # mode we need the connection + an allocated order id + a built OrderApp (all
        # established in connect_ib once the handshake completes).
        sim = self.sim_mode_var.get()
        if not sim and (not self.connected or self.ib.next_order_id is None or self.order_app is None):
            messagebox.showerror("Trade", "Not connected or no order ID.")
            return

        contract_obj = self.contract()
        is_crypto = contract_obj.secType == "CRYPTO"

        # Default open size:
        #   - STK/CASH: 10 units (shares / base-currency units).
        #   - CRYPTO: 0.001 BTC (~$70 at $70k BTC). A 100-BTC default would be
        #     a multi-million-dollar order on PAXOS and instantly rejected.
        # Must be Decimal for ibapi >=10.19 to preserve fractional precision —
        # passing a float here can serialize as "0.0010000000000001" and break
        # tick-size validation on PAXOS.
        if is_crypto:
            qty = Decimal("0.001")
        else:
            qty = 10

        # Current position depends on mode: sim_position (accounting engine) when
        # simulating, else the optimistic live counter.
        cur_pos = self.sim_position if sim else self.position

        # Decide action + final qty for OPEN vs CLOSE.
        if side == 0:
            # CLOSE: flatten by sending the opposite-direction order for
            # |current position| units. If we're flat already, nothing to do.
            if cur_pos == 0:
                messagebox.showinfo("Close", "No position to close.")
                return
            # For crypto, the position is stored as a number; wrap in Decimal
            # so the IOC limit order carries fractional size correctly.
            qty = Decimal(str(abs(cur_pos))) if is_crypto else abs(cur_pos)
            action = "SELL" if cur_pos > 0 else "BUY"
        else:
            # OPEN long (side=+1) or short (side=-1).
            action = "BUY" if side == 1 else "SELL"

        # Signed side for the accounting engine / fill: +1 BUY, -1 SELL.
        fill_side = 1 if action == "BUY" else -1

        # ── SIMULATED FILL (plan §7.3, replay half — no IBKR) ──────────────────
        if sim:
            # Pick a fill reference price. Crypto uses ask (BUY) / bid (SELL) when a
            # live quote exists; everything else (and offline replay) falls back to the
            # current mark (_last_mark_price). This is the price the synthesized Fill
            # books at — a slippage model can layer on later (§4c note).
            if is_crypto:
                fill_price = self.ib.ask if action == "BUY" else self.ib.bid
                if fill_price is None:
                    fill_price = self._last_mark_price()
            else:
                fill_price = self._last_mark_price()
            if fill_price is None or not np.isfinite(fill_price) or fill_price <= 0:
                messagebox.showerror("Trade", "No price reference for simulated fill.")
                return
            # Mark timestamp = the current bar's start so the resulting state_df row
            # overwrites this bar's row (one row per bar) instead of scattering off-grid
            # rows on every mid-bar click.
            mark_ts = self.bar_start or datetime.now()
            # exec_backend.execute synthesizes the Fill and funnels it through apply_fill
            # — the SOLE mutator of sim_position/P&L — stamping the current forecast
            # context so the ledger remembers WHY the trade happened (our extra fields).
            self.exec_backend.execute(fill_side, float(qty), fill_price,
                                      mark_ts, ctx=self._last_forecast_view)
            # Re-mark the book and refresh panel + plots immediately, so position / P&L
            # move on the click itself (not only at the next bar close).
            self._record_state_row(mark_ts, self._last_mark_price())
            self.update_portfolio_display()
            self._refresh_recommendation()
            self.redraw_chart()
            return

        # ── LIVE PATH (unchanged) ──────────────────────────────────────────────
        # Build the order via the shared library builders. The PAXOS IOC/tick-snap
        # rules and the eTradeOnly/firmQuoteOnly hasattr guard now live INSIDE those
        # builders (crypto_marketable_limit_order / market_order), so they are not
        # duplicated here. The CLOSE leg routes through the SAME builders as OPEN.
        if is_crypto:
            # PAXOS rejects MKT — it needs a marketable IOC limit. The builder crosses
            # the spread and snaps to tick; we just supply the reference price: the ask
            # for a BUY / bid for a SELL, falling back to last_price. The None-check
            # stays here in the GUI (the builder assumes a valid, non-None ref).
            ref = self.ib.ask if action == "BUY" else self.ib.bid
            if ref is None:
                ref = self.ib.last_price
            if ref is None:
                messagebox.showerror("Trade", "No price reference for crypto LMT order.")
                return
            order = crypto_marketable_limit_order(action, qty, ref)
        else:
            # Equities / FX: plain market order.
            order = market_order(action, qty)

        # Send through OrderApp.place_order: it reserves the next order id UNDER A LOCK
        # (reserve_order_id) and calls placeOrder, so two concurrent dispatches (a
        # manual click racing the auto-trade signal) can't grab the same id — this
        # replaces the old un-locked `self.ib.next_order_id += 1`. placeOrder is async:
        # TWS replies via orderStatus/execDetails callbacks (not handled here), so we
        # update our local position optimistically without waiting for fills.
        self.order_app.place_order(contract_obj, order)

        # Optimistic local bookkeeping. If the order partially fills or is rejected,
        # this number will drift from reality — IBApp.position callbacks (populated by
        # reqPositions) are the authoritative source and are read on connect.
        # Cast qty to float for the running total so we don't accidentally mix
        # Decimal (crypto) with int (equity) in self.position arithmetic.
        delta_q = float(qty)
        self.position = 0 if side == 0 else (self.position + (delta_q if side == 1 else -delta_q))
        self.update_portfolio_display()

    def update_portfolio_display(self):
        # In sim mode the authoritative position is the accounting engine's
        # sim_position (filled units); in live mode it's the optimistic self.position.
        # Show whichever matches the active mode so the top widget never lies.
        shown_pos = self.sim_position if self.sim_mode_var.get() else self.position
        # Trim trailing zeros on fractional (crypto) sizes for readability.
        self.pos_lbl.config(text=f"{shown_pos:g}")
        # Portfolio value: live NetLiquidation if connected, else our sim cash base
        # plus realised + unrealised P&L (so the figure moves in pure replay too).
        if self.ib.account_value is not None and not self.sim_mode_var.get():
            val = self.ib.account_value
        else:
            unreal = self._sim_unrealised(self._last_mark_price())
            val = self.cash + self.sim_realised + unreal
        self.port_lbl.config(text=f"{val:,.2f}")

    # ========================================================================
    # RISK / RETURN ENGINE (plan §7.1, PRIORITY 1)
    # ========================================================================
    def _last_mark_price(self):
        # Best available "current price" for marking the book: the live last tick if
        # we have one, else the forming bar's close, else μ. Used by P&L marking and
        # the recommendation's r_hat denominator.
        if self.ib.last_price is not None and np.isfinite(self.ib.last_price):
            return float(self.ib.last_price)
        if self.current_bar is not None:
            return float(self.current_bar[4])
        if self.ohlc_bars:
            return float(self.ohlc_bars[-1]['c'])
        return float(self.mu) if self.mu is not None else None

    def _forecast_quantiles(self, h, qs):
        # Gaussian closed-form quantile PATHS over steps 1..h (plan §7.1, 1d option 1).
        # For step k the OU conditional std is σ·sqrt(1 − φ^(2k)); the q-quantile price
        # is central_k + z(q)·std_k, with central_k from KalmanOU.forecast and z(q) the
        # standard-normal inverse-CDF (statistics.NormalDist — no scipy dependency).
        # Returns {q: [price_1..price_h]} so the chart can fill_between band paths and
        # _compute_recommendation can read the final-step value via [-1]. Designed so the
        # bootstrap / Monte-Carlo variants (1d options 2/3) are drop-in replacements.
        if self.kalman is None or self.sigma is None:
            return {}
        central = self.kalman.forecast(h)
        phi = self.kalman.phi
        out = {}
        for q in qs:
            z = statistics.NormalDist().inv_cdf(q)
            path = []
            for k in range(1, h + 1):
                std_k = self.sigma * math.sqrt(max(1.0 - phi ** (2 * k), 0.0))
                path.append(central[k - 1] + z * std_k)
            out[q] = path
        return out

    def _compute_recommendation(self):
        # Build a ForecastView from the CURRENT model state (plan §7.1). Returns None
        # if the model isn't calibrated or there's no price to measure against.
        if self.kalman is None or self.sigma is None or self.mu is None:
            return None
        p_now = self._last_mark_price()
        if p_now is None or not np.isfinite(p_now) or p_now <= 0:
            return None

        # Horizon H reuses the existing forecast-horizon control (panel ↔ chart agree).
        try:
            H = max(1, int(self.forecast_horizon_var.get()))
        except (TypeError, ValueError):
            H = 5

        # Central H-step projection -> expected simple return (§1c).
        central = self.kalman.forecast(H)[-1]
        r_hat_h = central / p_now - 1.0
        # Worst/best-case returns from the H-step quantile prices (final step).
        qbands = self._forecast_quantiles(H, (Q_LOW_P, Q_HIGH_P))
        if qbands:
            q_low_ret = qbands[Q_LOW_P][-1] / p_now - 1.0
            q_high_ret = qbands[Q_HIGH_P][-1] / p_now - 1.0
        else:
            q_low_ret = q_high_ret = r_hat_h

        # ---- risk-aware weight cap (§3a v1) ----
        # If the worst case is worse than the hard floor, no trade is allowed at all.
        if q_low_ret < REC_QLOW_FLOOR:
            w_cap = 0.0
            binding_cap = "idio_floor"
        else:
            # Otherwise scale the cap down linearly as the worst case worsens toward the
            # floor: benign downside -> full REC_W_CAP; near the floor -> ~0.
            frac = (q_low_ret - REC_QLOW_FLOOR) / (0.0 - REC_QLOW_FLOOR)
            w_cap = REC_W_CAP * max(0.0, min(1.0, frac))
            binding_cap = "none"

        # Map expected return -> target weight (piecewise linear), then clip to ±cap.
        w_unclipped = REC_ALPHA * r_hat_h
        w_target = max(-w_cap, min(w_cap, w_unclipped))
        # Flag if the cap (not the raw signal) is what limited the size.
        if binding_cap == "none" and abs(w_unclipped) > w_cap and w_cap > 0:
            binding_cap = "idio_floor"

        # ---- translate weight into a concrete order (§3c) ----
        # Account value: live NetLiq when trading live, else the sim cash + P&L base.
        if self.ib.account_value is not None and not self.sim_mode_var.get():
            account_value = float(self.ib.account_value)
        else:
            account_value = self.cash + self.sim_realised + self._sim_unrealised(p_now)
        if account_value <= 0:
            account_value = self.cash
        cur_pos = self.sim_position if self.sim_mode_var.get() else self.position
        value_current = cur_pos * p_now
        w_current = value_current / account_value if account_value else 0.0
        value_target = w_target * account_value
        value_delta = value_target - value_current
        w_delta = w_target - w_current
        qty = value_delta / p_now if p_now else 0.0
        direction = "long" if qty > 1e-12 else ("short" if qty < -1e-12 else "flat")

        return ForecastView(
            r_hat_h=r_hat_h, q_low=q_low_ret, q_high=q_high_ret,
            central_price=central, mu_now=float(self.mu), p_now=p_now,
            direction=direction, qty=qty, w_current=w_current, w_target=w_target,
            w_delta=w_delta, value_current=value_current, value_target=value_target,
            value_delta=value_delta, binding_cap=binding_cap,
        )

    def _intensity_hex(self, base_rgb, magnitude, scale):
        # Interpolate a colour from dim grey toward `base_rgb` as |magnitude| grows
        # (capped at `scale`). Gives the panel its "green brighter = more upside,
        # red brighter = more downside" feel (§3b) without external colour libs.
        t = max(0.0, min(1.0, abs(magnitude) / scale)) if scale else 0.0
        dim = (0.4, 0.4, 0.4)
        r = dim[0] + (base_rgb[0] - dim[0]) * t
        g = dim[1] + (base_rgb[1] - dim[1]) * t
        b = dim[2] + (base_rgb[2] - dim[2]) * t
        return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'

    def _update_recommendation_panel(self, fv):
        # Render a ForecastView into the panel labels (plan §7.1). Tk-thread only.
        if fv is None:
            for lbl in (self.rec_ret_lbl, self.rec_worst_lbl, self.rec_best_lbl,
                        self.rec_trade_lbl):
                lbl.config(text="—")
            self.rec_cap_lbl.config(text="—")
            return
        # Expected return — neutral.
        self.rec_ret_lbl.config(text=f"{fv.r_hat_h*100:+.2f}%")
        # Worst case — red, intensity by downside magnitude (scaled vs a 10% reference).
        self.rec_worst_lbl.config(
            text=f"{fv.q_low*100:+.2f}%",
            fg=self._intensity_hex((0.97, 0.32, 0.29), fv.q_low, 0.10))
        # Best case — green, intensity by upside magnitude.
        self.rec_best_lbl.config(
            text=f"{fv.q_high*100:+.2f}%",
            fg=self._intensity_hex((0.25, 0.73, 0.31), fv.q_high, 0.10))
        # Recommended order line: direction + |qty| + $ value delta.
        if fv.direction == "flat":
            self.rec_trade_lbl.config(text="flat (no trade)")
        else:
            self.rec_trade_lbl.config(
                text=f"{fv.direction.upper()} {abs(fv.qty):.4g} (${fv.value_delta:+,.0f})")
        # Binding cap explainer.
        self.rec_cap_lbl.config(
            text=f"{fv.binding_cap}  w {fv.w_current*100:.1f}%→{fv.w_target*100:.1f}%")

    def _refresh_recommendation(self):
        # Compute + render the recommendation, caching the view so redraw_chart's
        # quantile bands can reuse it. Safe to call on the Tk main thread only.
        fv = self._compute_recommendation()
        self._last_forecast_view = fv
        self._update_recommendation_panel(fv)

    def _on_bar_close(self, ts, price):
        # Tk-thread bar-close hook for the sim/replay stack (dispatched from on_tick).
        # Refresh the recommendation FIRST so the freshly-computed forecast context is
        # what gets stamped into the new state_df row, then mark the book at this bar's
        # close. Recompute order: panel -> ledger row -> (chart redrawn separately by
        # the existing _deferred_chart_update on the same bar close).
        self._refresh_recommendation()
        self._record_state_row(ts, price)

    # ========================================================================
    # ACCOUNTING ENGINE (plan §7.3 / §4b) — the SOLE mutator of sim position/P&L.
    # ========================================================================
    def apply_fill(self, fill):
        # Apply one Fill to the running book per the §4b rules. `fill.side` is +1 BUY /
        # -1 SELL; `fill.qty` is the absolute size. Updates sim_position, sim_avg_entry
        # and sim_realised in place and accumulates closed units for the current bar.
        signed_qty = fill.side * fill.qty          # +long add / -short add
        pos = self.sim_position
        avg = self.sim_avg_entry
        price = fill.price

        if pos == 0 or (pos > 0 and signed_qty > 0) or (pos < 0 and signed_qty < 0):
            # SAME DIRECTION (or opening from flat): position grows. New avg entry is the
            # volume-weighted average of the old open units and the new fill.
            new_pos = pos + signed_qty
            if pos == 0:
                avg = price
            else:
                avg = (avg * abs(pos) + price * abs(signed_qty)) / abs(new_pos)
            self.sim_position = new_pos
            self.sim_avg_entry = avg
        else:
            # OPPOSITE DIRECTION: the fill reduces (and maybe flips) the position.
            closing = min(abs(signed_qty), abs(pos))   # units actually closed here
            # Realised P&L on the closed units: sign(pos)·(price − avg)·units.
            self.sim_realised += np.sign(pos) * (price - avg) * closing
            self._sim_closed_this_bar += closing
            if abs(signed_qty) <= abs(pos):
                # Pure reduction (no flip): avg entry of the surviving units is unchanged.
                self.sim_position = pos + signed_qty
                if self.sim_position == 0:
                    self.sim_avg_entry = 0.0
            else:
                # FLIP: close all old units (done above) then OPEN the remainder on the
                # opposite side at the fill price (new leg, fresh avg entry).
                remainder = abs(signed_qty) - abs(pos)
                self.sim_position = np.sign(signed_qty) * remainder
                self.sim_avg_entry = price

    def _sim_unrealised(self, price):
        # Mark-to-market P&L on currently-open units at `price`. Zero when flat or when
        # no price is available. (last_price − avg_entry)·position carries the sign
        # correctly for both long and short books.
        if price is None or not np.isfinite(price) or self.sim_position == 0:
            return 0.0
        return (price - self.sim_avg_entry) * self.sim_position

    def _record_state_row(self, ts, price):
        # Append/refresh the state_df row for bar timestamp `ts`, marked at `price`
        # (plan §4b — state_df is the single source of truth for all replay plots). Each
        # bar close calls this once; the top widgets and every analytics axis read it.
        if price is None or not np.isfinite(price):
            price = self.sim_avg_entry or 0.0
        unreal = self._sim_unrealised(price)
        fv = self._last_forecast_view
        row = {
            "position": self.sim_position,
            "avg_entry_price": self.sim_avg_entry,
            "entry_cost": self.sim_avg_entry * self.sim_position,
            "mark_value": price * self.sim_position,
            "unrealised_pnl": unreal,
            "realised_pnl": self.sim_realised,
            "total_pnl": self.sim_realised + unreal,
            "last_price": price,
            # In sim, intended == filled (immediate fills); unfilled is the residual,
            # always ~0 here but wired so the live path (partial fills) reuses the plot.
            "intended": self.sim_position,
            "filled": self.sim_position,
            "unfilled": 0.0,
            "closed_units": self._sim_closed_this_bar,
            "r_hat": fv.r_hat_h if fv is not None else np.nan,
            "q_low": fv.q_low if fv is not None else np.nan,
            "q_high": fv.q_high if fv is not None else np.nan,
        }
        # One row per bar timestamp: overwrite if the bar is re-marked, else append.
        self.state_df.loc[ts] = row
        # Reset the per-bar closure accumulator now that it's been recorded.
        self._sim_closed_this_bar = 0.0

    def _reset_accounting(self):
        # Wipe all sim accounting + the state_df. Called from clear_chart so a fresh
        # symbol/run doesn't inherit a stale ledger.
        self.sim_position = 0.0
        self.sim_avg_entry = 0.0
        self.sim_realised = 0.0
        self._sim_closed_this_bar = 0.0
        self.state_df = self.state_df.iloc[0:0]
        self.exec_backend = SimExecutionBackend(self)
        self._last_forecast_view = None

    def redraw_chart(self):
        # ORCHESTRATOR (plan §7.2). Clears every axis, then delegates to one _draw_*
        # helper per pane so each concern (price / P&L / position / entry) is isolated
        # and individually testable. The analytics axes are only touched when they
        # exist (analytics toggle on) and there is state_df data to plot.
        self.ax.clear()
        self._style_ax(self.ax)
        # Reattach hover annotation after ax.clear() (clear() removed it).
        if hasattr(self, '_hover_annot'):
            self._hover_annot = self.ax.annotate(
                "", xy=(0, 0), xytext=(12, 12), textcoords='offset points',
                bbox=dict(boxstyle='round,pad=0.4', fc='#161b22', ec='#58a6ff', alpha=0.95),
                color='#c9d1d9', fontsize=9, family='Consolas', zorder=10,
            )
            self._hover_annot.set_visible(False)
        # PRICE PANE — the original chart, now in its own helper.
        self._draw_price()
        # ANALYTICS PANES — only if the stacked layout is active. Each clears + restyles
        # its axis then draws from state_df (replay/sim source of truth).
        if self.ax_pnl is not None:
            for ax in (self.ax_pnl, self.ax_pos, self.ax_entry):
                ax.clear()
                self._style_ax(ax)
            self._draw_pnl()
            self._draw_positions()
            self._draw_entry_vs_market()
        self.canvas.draw_idle()

    def _draw_price(self):
        # PRICE PANE (extracted verbatim from the original redraw_chart, with the
        # ±1σ forecast cone swapped for explicit quantile bands per §7.2/§1d). Draws
        # candles, the close line, the Kalman history dots, the live mean / μ / bands,
        # and the OU forecast with worst/best-case quantile shading.
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

        # ---- OU FORECAST: central path + explicit WORST/BEST-CASE quantile bands ----
        # (plan §7.2 / §1d) — the ±1σ Gaussian cone is replaced by the same quantile
        # PATHS the recommendation panel reasons about (_forecast_quantiles), so the
        # shaded region the trader sees == the q_low/q_high that drive the trade. The
        # central path is still the OU mean projection (KalmanOU.forecast). Bands come
        # from Q_LOW_P / Q_HIGH_P; inner bands can be stacked later at other alphas.
        if self.show_forecast_bounds_var.get() and self.kalman is not None and self.sigma is not None:
            H = max(1, int(self.forecast_horizon_var.get()))
            mean_path = self.kalman.forecast(H)
            # Per-step quantile price paths (dict {q: [p_1..p_H]}).
            qbands = self._forecast_quantiles(H, (Q_LOW_P, Q_HIGH_P))
            fc_x = list(range(n_draw, n_draw + H))
            self.ax.plot(fc_x, mean_path, color='#a371f7', linewidth=1.6,
                         alpha=0.9, zorder=5, label='OU forecast')
            if qbands:
                lower_fc = qbands[Q_LOW_P]
                upper_fc = qbands[Q_HIGH_P]
                self.ax.fill_between(
                    fc_x, lower_fc, upper_fc, color='#a371f7', alpha=0.18,
                    zorder=4, linewidth=0,
                    label=f'OU P{int(Q_LOW_P*100)}–P{int(Q_HIGH_P*100)} band')
        elif self.forecast_prices:
            fc_x = list(range(n_draw, n_draw + len(self.forecast_prices)))
            self.ax.scatter(fc_x, self.forecast_prices, color='#a371f7', s=22, zorder=5, label='OU forecast')
        self.ax.set_ylabel("Price", color='#c9d1d9')
        # Only the price axis carries the x-label when analytics axes are stacked below
        # it (the bottom analytics axis re-labels it); standalone, it labels itself.
        if self.ax_pnl is None:
            self.ax.set_xlabel("Bar index", color='#8b949e')
        handles, labels = self.ax.get_legend_handles_labels()
        if handles:
            self.ax.legend(loc='upper left', fontsize=8)

    def _state_xy(self, column):
        # Helper: return (x_indices, y_values) for one state_df column, with x = the
        # row's integer position so it lines up bar-for-bar with the price candles
        # (which are also drawn at integer indices). Empty frame -> empty lists.
        if self.state_df.empty or column not in self.state_df.columns:
            return [], []
        y = self.state_df[column].to_list()
        return list(range(len(y))), y

    def _draw_pnl(self):
        # P&L DECOMPOSITION PANE (§4c plot 1). Three toggleable line series sourced
        # straight from state_df: realised (crystallised), unrealised (mark-to-market
        # on open units), and total (their sum). All from the SAME frame the top
        # widget summarises, so the number on screen and the curve never disagree.
        if self.state_df.empty:
            self.ax_pnl.set_ylabel("P&L", color='#c9d1d9', fontsize=8)
            return
        x, _ = self._state_xy("total_pnl")
        if self.show_realised_var.get():
            self.ax_pnl.plot(x, self.state_df["realised_pnl"].to_list(),
                             color='#3fb950', linewidth=1.2, label='realised')
        if self.show_unrealised_var.get():
            self.ax_pnl.plot(x, self.state_df["unrealised_pnl"].to_list(),
                             color='#58a6ff', linewidth=1.2, label='unrealised')
        if self.show_total_var.get():
            self.ax_pnl.plot(x, self.state_df["total_pnl"].to_list(),
                             color='#f0883e', linewidth=1.5, label='total')
        # Zero reference so sign of P&L reads at a glance.
        self.ax_pnl.axhline(0, color='#30363d', linewidth=0.8, zorder=1)
        self.ax_pnl.set_ylabel("P&L", color='#c9d1d9', fontsize=8)
        h, _l = self.ax_pnl.get_legend_handles_labels()
        if h:
            self.ax_pnl.legend(loc='upper left', fontsize=7, ncol=3)

    def _draw_positions(self):
        # POSITION BREAKDOWN PANE (§4c plot 2). filled (actually held), intended
        # (target the recommendation wanted), unfilled (= intended − filled), and a
        # scatter of unit closures over time. In pure sim every order fills instantly,
        # so intended == filled and unfilled ≈ 0 — but the series are wired so the live
        # path (partial fills) reuses the exact same plot.
        if self.state_df.empty:
            self.ax_pos.set_ylabel("Units", color='#c9d1d9', fontsize=8)
            return
        x, _ = self._state_xy("filled")
        if self.show_filled_var.get():
            self.ax_pos.plot(x, self.state_df["filled"].to_list(),
                             color='#7ee787', linewidth=1.4, label='filled')
        if self.show_intended_var.get():
            self.ax_pos.plot(x, self.state_df["intended"].to_list(),
                             color='#d29922', linewidth=1.0, linestyle='--', label='intended')
            self.ax_pos.plot(x, self.state_df["unfilled"].to_list(),
                             color='#f85149', linewidth=0.8, alpha=0.7, label='unfilled')
        # closed_units markers: only where a closure happened (non-zero), so the chart
        # isn't littered with zeros. Size scaled by magnitude for quick visual weight.
        closed = self.state_df["closed_units"].to_list()
        cx = [i for i, v in enumerate(closed) if v]
        cy = [closed[i] for i in cx]
        if cx:
            self.ax_pos.scatter(cx, cy, color='#a371f7', s=24, zorder=5, label='closed')
        self.ax_pos.axhline(0, color='#30363d', linewidth=0.8, zorder=1)
        self.ax_pos.set_ylabel("Units", color='#c9d1d9', fontsize=8)
        h, _l = self.ax_pos.get_legend_handles_labels()
        if h:
            self.ax_pos.legend(loc='upper left', fontsize=7, ncol=4)

    def _draw_entry_vs_market(self):
        # ENTRY-vs-MARKET PANE (§4c plot 3). avg_entry_price and last_price on one
        # axis, with the background shaded by position SIGN — blue while long, pink
        # while short, neutral when flat — by walking contiguous same-sign runs in
        # state_df and drawing one axvspan per run (matplotlib has no vectorised band).
        if self.state_df.empty:
            self.ax_entry.set_ylabel("Entry/mkt", color='#c9d1d9', fontsize=8)
            self.ax_entry.set_xlabel("Bar index", color='#8b949e')
            return
        x, _ = self._state_xy("last_price")
        if self.show_entry_var.get():
            # avg_entry_price is 0 when flat; mask those so the line doesn't dive to 0.
            entry = [e if p != 0 else np.nan
                     for e, p in zip(self.state_df["avg_entry_price"].to_list(),
                                     self.state_df["position"].to_list())]
            self.ax_entry.plot(x, entry, color='#d29922', linewidth=1.2, label='avg entry')
        self.ax_entry.plot(x, self.state_df["last_price"].to_list(),
                           color='#58a6ff', linewidth=1.0, alpha=0.8, label='last')
        # Background sign shading: iterate runs of constant sign(position).
        pos = self.state_df["position"].to_list()
        i = 0
        n = len(pos)
        while i < n:
            s = 0 if pos[i] == 0 else (1 if pos[i] > 0 else -1)
            j = i
            while j + 1 < n and (0 if pos[j + 1] == 0 else (1 if pos[j + 1] > 0 else -1)) == s:
                j += 1
            if s != 0:
                # span from i-0.5 to j+0.5 so candles sit inside their shaded run.
                color = '#1f6feb' if s > 0 else '#db61a2'
                self.ax_entry.axvspan(i - 0.5, j + 0.5, color=color, alpha=0.10, zorder=0)
            i = j + 1
        self.ax_entry.set_ylabel("Entry/mkt", color='#c9d1d9', fontsize=8)
        self.ax_entry.set_xlabel("Bar index", color='#8b949e')
        h, _l = self.ax_entry.get_legend_handles_labels()
        if h:
            self.ax_entry.legend(loc='upper left', fontsize=7, ncol=2)

    def refresh_timer(self):
        self.update_portfolio_display()
        self.root.after(2000, self.refresh_timer)


def main():
    root = tk.Tk()
    app = KalmanTradingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
