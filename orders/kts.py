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
import json
import pathlib
import math
import statistics
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
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
    qty: float              # signed units to trade to reach the RECOMMENDED weight (w_rec)
    w_current: float        # current portfolio weight (value_current / account_value)
    w_target: float         # RAW target weight α·r̂ BEFORE any cap or turnover threshold
    w_capped: float         # target AFTER risk caps but BEFORE the turnover gate (the move
                            # the turnover gate is tested against; = w_rec unless suppressed)
    w_rec: float            # RECOMMENDED weight AFTER risk caps + the turnover threshold gate
    w_delta: float          # w_rec - w_current (what the recommendation actually moves)
    value_current: float    # $ exposure now
    value_target: float     # $ exposure after the trade (sized at w_rec)
    value_delta: float      # $ to trade (value_target - value_current)
    binding_cap: str        # which RISK cap bound w_capped: "none"/"idio_floor"/"factor"/
                            # "portfolio". Turnover suppression is NOT reported here — it is
                            # surfaced separately via w_capped vs w_threshold (a zero trade
                            # next to binding_cap="none" reads as a turnover suppression).
    # --- enrichment for the order ledger + state_df (§7.5) ---
    # w_threshold is the min-turnover weight gate actually applied (REC_MIN_TURNOVER): if the
    # capped target would move the weight by less than this, the recommendation suppresses the
    # trade (w_rec = w_current) so we don't churn on noise (§3a/§3c). account_value is the
    # equity the weights were measured against, surfaced so _record_state_row can stamp
    # portfolio_value straight onto the bar without recomputing it. Both carry defaults, so
    # (dataclass rule) they MUST stay last — after every non-default field above.
    w_threshold: float = 0.0
    account_value: float = 0.0
    
    


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
    # which risk cap (if any) was active when this trade was recommended: "none"/"idio_floor"/"factor"/"portfolio"
    binding_cap: str | None = None


# -----------------------------------------------------------------------------
# ORDER — the trade ledger record that sits ABOVE Fill (plan §7.5 / GAP A).
# Where Fill is one atomic execution print, Order is the whole order's life: its
# metadata, the recommendation rationale that justified it, its current lifecycle
# state, and an append-only event history. It is shaped to serialize EXACTLY to the
# nested structure of orders/dummy_orders.json via to_dict(), so the same record
# format works for a simulated replay order and (later) a real IBKR order.
#
# In SIM/replay we assume a single fill that fills the whole order immediately, so
# `history` collapses to a Submitted→Filled pair at one timestamp and qty_filled ==
# total_qty. The lifecycle plumbing (an order that COULD sit unfilled / partial)
# still exists unchanged for the live path to populate from orderStatus/execDetails.
#
# The dataclass holds FLAT fields (cheap to build at the execute() choke point); the
# dummy_orders.json nesting (metadata{contract, rationale}, state, history) is
# assembled only on demand in to_dict() at serialization time.
# -----------------------------------------------------------------------------
@dataclass
class Order:
    # ---- metadata (identity + the contract + how the order was specified) ----
    order_id: int                       # our ledger key (the SimExecutionBackend counter in sim)
    submitted_at: datetime              # when the order was created/submitted (bar ts in replay)
    symbol: str                         # contract symbol, e.g. "BTC" / "SMH"
    sec_type: str                       # "CRYPTO" / "STK" / "CASH"
    exchange: str                       # routing venue, e.g. "PAXOS" / "SMART"
    currency: str                       # quote currency, e.g. "USD"
    action: str                         # "BUY" / "SELL" (human-facing; sign carried by the fill side)
    order_type: str                     # "MKT" / "LMT" — sim fills are immediate market-style
    total_qty: float                    # absolute units the order is for (magnitude)
    market_price_at_submit: float       # mark/quote at submit time — the price the sim fills at
    # rationale (the ForecastView context that justified the trade) — keyed EXACTLY as the
    # `rationale` block in dummy_orders.json so to_dict() round-trips to that schema.
    # in the syntax, 'field' is a function imported from dataclasses that defines a field
    #  with a default value (in this case, an empty dict as per default_factory = dict).
    rationale: dict = field(default_factory=dict)
    # optional IBKR-side identifiers — None in pure sim (no TWS to allocate them).
    perm_id: int | None = None          # IBKR permId (stable across the order's life) — None in sim
    client_id: int | None = None        # the API client id that placed it — None/our GUI id in sim
    parent_id: int | None = None        # bracket/parent linkage — None for flat sim orders
    con_id: int | None = None           # IBKR contract id — None/0 in sim
    limit_price: float | None = None    # limit price for LMT orders — None for market-style sim fills
    tif: str = "DAY"                    # time-in-force — DAY by default
    strategy_tag: str = "ou_mean_revert_v1"  # which strategy emitted this order (provenance)
    # ---- mutable lifecycle state (overwritten as the order progresses; one shot in sim) ----
    state: dict = field(default_factory=dict)
    # ---- append-only event log (Submitted → Filled in sim, richer for live) ----
    history: list = field(default_factory=list)

    def to_dict(self):
        # Assemble the dummy_orders.json nesting from the flat fields. Called only at
        # serialization time (export_orders_json), never on the hot path.
        return {
            "metadata": {
                "order_id": self.order_id,
                "perm_id": self.perm_id,
                "client_id": self.client_id,
                "parent_id": self.parent_id,
                # ISO timestamps so the JSON matches dummy_orders.json's string form.
                "submitted_at": _iso(self.submitted_at),
                "contract": {
                    "symbol": self.symbol,
                    "sec_type": self.sec_type,
                    "exchange": self.exchange,
                    "currency": self.currency,
                    "con_id": self.con_id,
                },
                "action": self.action,
                "order_type": self.order_type,
                "total_qty": self.total_qty,
                "limit_price": self.limit_price,
                "tif": self.tif,
                "market_price_at_submit": self.market_price_at_submit,
                "strategy_tag": self.strategy_tag,
                "rationale": self.rationale,
            },
            "state": self.state,
            "history": self.history,
        }


def _iso(ts):
    # Normalize a timestamp to an ISO-8601 string for JSON. Accepts datetime (most cases)
    # or anything already string-like; None passes through so optional fields stay null.
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.isoformat()
    # pandas Timestamp / numpy datetime / str all have a sane str() fallback.
    return str(ts)


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
        # Snapshot the book BEFORE the fill so we can classify the order (did it grow the
        # exposure → OPEN, or reduce/flip it → CLOSE) and measure the realised P&L this
        # single fill crystallised (sim_realised moves only inside apply_fill).
        pos_before = self.app.sim_position
        realised_before = self.app.sim_realised
        # Route through the app's accounting engine — the ONLY place position/P&L move.
        self.app.apply_fill(fill)
        # Realised P&L this fill booked = how far the cumulative realised moved.
        realised_delta = self.app.sim_realised - realised_before
        # OPEN if the trade pushed |exposure| in the same direction as (or from) flat;
        # CLOSE if it traded against an existing opposite position (reduced/flipped it).
        # pos_before == 0 → always opening; same sign as side → growing (OPEN); else CLOSE.
        if pos_before == 0 or (pos_before > 0) == (int(side) > 0):
            role = "OPEN"
        else:
            role = "CLOSE"
        # Build the ledger Order ABOVE the fill (§7.5) and register it. In sim the whole
        # order fills in one shot, so total_qty == qty_filled and history is Submitted→Filled
        # at the same timestamp. ctx (a ForecastView) supplies the rationale block.
        self.app._register_sim_order(oid, fill, ctx, role, realised_delta)
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
# REC_W_CAP and REC_MIN_TURNOVER are now the DEFAULTS for two user-editable entries in the
# Trading & Portfolio panel (self.max_weight_var / self.min_turnover_var, entered as %).
# _compute_recommendation reads those vars live via _max_weight_frac / _min_turnover_frac on
# every call, so the trader can retune the caps without a restart; these module constants
# seed the entry defaults and act as the parse-failure fallback.
REC_W_CAP = 0.03        # default max |single-position weight| when downside risk benign (3%)
REC_QLOW_FLOOR = -0.05  # if worst-case return < this (-5%), cap collapses to 0 (no trade)
# Min-turnover weight gate (§3a/§3c): smallest |recommended − current| weight move worth
# trading. If the capped target would move the weight by less than this, the recommendation
# suppresses the trade (recommended = current) so we don't churn the book on tiny/noisy
# signals. 0.0025 = 0.25% of equity — below that the trade costs more (spread/fees) than earns.
REC_MIN_TURNOVER = 0.0025

# -----------------------------------------------------------------------------
# REPLAY RENDERING CONSTANT (plan §7 build-step 7 — historical-replay scrubber).
# A precomputed replay can hold thousands of bars; drawing one matplotlib Rectangle
# per candle for all of them makes every scrub-drag redraw crawl. Above this many
# bars the price pane degrades from individual candles to a single close LINE (cheap
# to draw, still shows the whole price path) so dragging the scrubber stays smooth.
# -----------------------------------------------------------------------------
REPLAY_CANDLE_LIMIT = 300


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
        # Chart + model controls. bar_size_var feeds into the IB historical data request;
        # the rest are purely local and drive the OU/Kalman estimation and trading logic in self.on_tick.

        # The bar size (resolution):
        # First, goes through self._bar_size_to_sec_and_ib(), which first converts this to IBKR format
        # stored as self._bar_size_ib (e.g. "1 min") 
        # and also extracts the raw number of seconds per bar into self._bar_sec (e.g. 60).

        # Then, these converted attributes feed into two places:
        # A: the historical data request handled by our custom method get_historical_bars(), 
        # which we import from our custom ib_utils module, 
        # it takes as unput the self._bar_size_ib string and the number of bars we want,
        # and returns a list of historical bars in that resolution through
        # Iin turn suing IBKR's built-in reqHistoricalData under the hood. 
        # B: the live bars, handled by      
        # then self.on_tick within this same file, which aggregates 
        # incoming ticks into bars according to the same bar size, upon automatically receiving each
        # tick callback from IBKR which we initialised from self.toggle_stream
        #  This is a string like "1 min" or "5 mins" that IB's API expects. 
        # Changing this will affect how we aggregate ticks into bars and how we
        #  calibrate the OU model from those bars.
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
        # Optional hard cap on total |position| in UNITS (blank = None = uncapped).
        # This is a secondary safety guard — primary sizing is done by the weight-based
        # controls (min_turnover_var / max_weight_var) which drive _compute_recommendation.
        # When uncapped, the recommended qty from the model flows through unmodified.
        self.max_position_var = tk.StringVar(value="")
        # Optional hard cap on a single trade size in UNITS (blank = None = uncapped).
        # Same design: default None so the recommendation drives sizing; set a value only
        # if you need an absolute per-fire ceiling independent of the weight controls.
        self.max_trade_var = tk.StringVar(value="")
        # User-adjustable risk caps, entered as PERCENT in the Trading & Portfolio panel and
        # converted to fractions by _min_turnover_frac / _max_weight_frac (read live by
        # _compute_recommendation). Defaults track the REC_* fallbacks: 0.25% min-turnover
        # gate, 3% max single-position weight.
        self.min_turnover_var = tk.DoubleVar(value=REC_MIN_TURNOVER * 100)
        self.max_weight_var = tk.DoubleVar(value=REC_W_CAP * 100)
        # On/off switches for each cap (checkbox in the panel). Default ON = enforced.
        # Off => the gate/cap is disabled (see _min_turnover_frac / _max_weight_frac).
        self.min_turnover_on_var = tk.BooleanVar(value=True)
        self.max_weight_on_var = tk.BooleanVar(value=True)
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
        # ORDER LEDGER (§7.5 / GAP A) — the "separate dict keyed by order id" the plan
        # calls for, kept OUT of state_df to avoid column explosion. Each value is an
        # Order (dummy_orders.json shape). In sim every order == one immediate fill.
        # Serialized to replay_orders.json; cleared by _reset_accounting on a fresh run.
        self.orders = {}
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
                # ---- recommendation block at the bar (§7.5) — the WHOLE ForecastView, so
                # each row also answers "what was the recommended trade here?" not just
                # "where am I now?". Mirrors the order rationale fields one-for-one.
                "central_price", "mu_now", "reference_price", "direction", "signed_qty",
                "previous_weight", "target_weight", "recommended_weight", "weight_change",
                "weight_threshold",
                "previous_value", "target_value", "value_delta", "binding_cap",
                # ---- portfolio-level context (§7.5) — let the user eyeball position value
                # vs whole-book equity (are the weights sane?) at any scrubbed bar.
                "portfolio_value", "portfolio_pnl",
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
        # Split out of the coarser toggles so EVERY line series is independently
        # switchable (plan §B): unfilled used to ride on `intended`, `last` used to be
        # always-on inside the entry pane, and the `closed` scatter was unconditional.
        self.show_unfilled_var = tk.BooleanVar(value=True)
        self.show_closed_var = tk.BooleanVar(value=True)
        self.show_last_var = tk.BooleanVar(value=True)
        self.show_entry_var = tk.BooleanVar(value=True)
        # next_intended (Units pane): the TOTAL position the recommendation would hold AFTER
        # its recommended trade (target_value/reference_price). Distinct from `intended`
        # (the band-signal book) — the gap is what the recommendation wanted but the executed
        # strategy did not put on.
        self.show_next_intended_var = tk.BooleanVar(value=True)
        # ---- PRICE-PANE per-series toggles (besides Show bands / Forecast line, which have
        # their own controls in the trading-bands row). Each gates one artist in _draw_price.
        self.show_candles_var = tk.BooleanVar(value=True)   # historical OHLC candles
        self.show_close_var = tk.BooleanVar(value=True)
        self.show_kalman_hist_var = tk.BooleanVar(value=True)
        self.show_live_mean_var = tk.BooleanVar(value=True)
        self.show_longmean_var = tk.BooleanVar(value=True)
        # Trade markers on the price plot (▲ buy / ▼ sell at the fill price) + whether their
        # hover tooltip appends the full recommendation rationale (it can be tall).
        self.show_markers_var = tk.BooleanVar(value=True)
        self.show_marker_rationale_var = tk.BooleanVar(value=True)
        # Master switch for the lower analytics axes. Off by default so the app opens
        # looking exactly like before (price only); flip on to reveal P&L/position/entry.
        self.show_analytics_var = tk.BooleanVar(value=True)
        # Per-axis switches (plan §A): each analytics pane is independently shown/hidden so
        # the user can keep, e.g., just P&L + Units. Only consulted when the master is on;
        # _build_axes builds exactly the panes whose var is True, so the layout re-flows
        # (no empty crammed panes). Default all on = the previous 4-axis behaviour.
        self.show_pnl_axis_var = tk.BooleanVar(value=True)
        self.show_pos_axis_var = tk.BooleanVar(value=True)
        self.show_entry_axis_var = tk.BooleanVar(value=True)
        # Pop-out window handle (plan §C): None while the plots are docked in-app; set to the
        # Toplevel while they live in their own resizable window.
        self._popout = None
        # Paper/simulated trading switch. Default ON so the accounting engine + replay
        # plots work with NO IBKR connection (pure replay). Off = route orders live.
        self.sim_mode_var = tk.BooleanVar(value=True)

        # ForecastView most recently computed — cached so manual redraws can reuse it
        # for the quantile bands without recomputing the model. None until first bar.
        self._last_forecast_view = None

        # ---- HISTORICAL-REPLAY SCRUBBER STATE (plan §7 build-step 7) ----
        # The replay model is "compute once, scrub freely": _build_replay pulls a long
        # bar history, runs the WHOLE OU/Kalman/accounting pipeline bar-by-bar ONCE, and
        # snapshots enough per-bar state that dragging the slider to any index instantly
        # reconstructs "what the chart looked like as of that bar" — no recomputation on
        # drag, just slicing + a redraw. These hold the precomputed product:
        #   _replay_bars        — the full normalized OHLC list the replay ran over.
        #   _replay_kalman_prices — flat list of Kalman mean x, one per bar (orange dots).
        #   _replay_state_df    — the COMPLETE state_df (one row per bar) the run produced;
        #                         scrubbing to i shows iloc[:i+1] of this.
        #   _replay_frames      — per-bar model snapshot {phi, mu, sigma, x} so bands /
        #                         forecast cone / live-mean line reflect the model AS OF i.
        # _replay_active gates every scrub path; _replay_mark overrides _last_mark_price so
        # marking/recommendation use the REPLAYED bar's close, never a stale live tick.
        self._replay_active = False
        self._replay_mark = None
        self._replay_cursor = 0
        self._replay_bars = []
        self._replay_kalman_prices = []
        self._replay_state_df = None
        self._replay_frames = []
        # Drag throttle: ttk.Scale fires its command continuously while dragging; we cap
        # the redraw rate and flush the final position via root.after so the scrub feels
        # live without queueing a redraw per pixel.
        self._replay_cursor_pending = 0
        self._last_replay_draw = 0.0
        self._replay_draw_scheduled = False
        # How many bars _build_replay pulls for the replay window (user-editable).
        self.replay_bars_var = tk.StringVar(value="500")
        # Slider position (bar index). Bound to the scrub ttk.Scale built in setup_ui.
        self.replay_slider_var = tk.DoubleVar(value=0.0)
        # Jump-to-bar entry (1-based bar number the user types to scrub exactly there).
        # Default 120 so the user lands at a meaningful point after each build.
        self.replay_jump_var = tk.StringVar(value="120")

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
        # ============================================================================
        # SETUP_UI — build the entire Tk widget tree once at construction.
        # Layout is a single-column grid on `main`; each LabelFrame is one horizontal
        # band stacked top-to-bottom (rows 0..7). Only the chart row is given weight so
        # it absorbs all spare vertical space when the window is resized. Called once
        # from __init__ before the IB reader thread starts, so everything here runs on
        # the Tk main thread and no widget needs to be thread-guarded at build time.
        # ============================================================================
        # Root container with uniform padding; everything else is a child of `main`.
        main = ttk.Frame(self.root, padding=12)
        main.grid(row=0, column=0, sticky='nsew')
        # Make the single root cell elastic so `main` fills the whole window.
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        # One column, full width: every band stretches horizontally with the window.
        main.columnconfigure(0, weight=1)
        # Chart now lives on row 8 (a Recommendation frame is at row 6 and a Historical
        # Replay band at row 7), so it is row 8 that must stretch to fill vertical space.
        main.rowconfigure(8, weight=1)

        # ---- HEADER BAND (row 0) ----
        # Title on the left, live connection LED on the right. The LED is the only
        # widget here we keep a handle to (self.status_lbl) because connect/disconnect
        # recolour it; the title is static so it stays anonymous.
        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(header, text="Kalman Filter Trading System — OU Mean-Level", font=('Segoe UI', 14, 'bold')).pack(side='left')
        # Connection LED: red ● Disconnected by default; flipped to green by the
        # nextValidId / connection callbacks. tk.Label (not ttk) so we can set bg/fg
        # directly to the GitHub-dark palette.
        self.status_lbl = tk.Label(header, text="● Disconnected", font=('Segoe UI', 10, 'bold'), bg='#0d1117', fg='#f85149')
        self.status_lbl.pack(side='right')

        # ---- CONNECTION & SYMBOL BAND (row 1) ----
        # Two sub-rows: row0 = TWS/Gateway socket (host/port + connect buttons),
        # row1 = the contract definition (symbol/secType/exchange/market-data tier)
        # plus the stream/refresh actions. Split so the socket controls stay visually
        # separate from the instrument controls.
        ctrl = ttk.LabelFrame(main, text="Connection & Symbol", padding=8)
        ctrl.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        ctrl.columnconfigure(1, weight=1)
        row0 = ttk.Frame(ctrl)
        row0.grid(row=0, column=0, sticky='ew')
        # Host/Port feed connect_ib(): the TWS/IB-Gateway API socket address. Entry =
        # free-text box bound to a StringVar, so the user can type any host/port.
        ttk.Label(row0, text="Host").pack(side='left', padx=(0, 4))
        ttk.Entry(row0, textvariable=self.host_var, width=12).pack(side='left', padx=(0, 12))
        ttk.Label(row0, text="Port").pack(side='left', padx=(0, 4))
        ttk.Entry(row0, textvariable=self.port_var, width=6).pack(side='left', padx=(0, 12))
        # Connect button starts the API client + reader thread. Kept as an attribute
        # so the connection callbacks can disable it once connected.
        self.connect_btn = ttk.Button(row0, text="Connect", command=self.connect_ib)
        self.connect_btn.pack(side='left', padx=(0, 6))
        # Disconnect starts disabled (nothing to disconnect yet) and is enabled by the
        # connection callback. Accent style marks it as the "stop" action.
        self.disc_btn = ttk.Button(row0, text="Disconnect", command=self.disconnect_ib, state='disabled', style='Accent.TButton')
        self.disc_btn.pack(side='left')
        row1 = ttk.Frame(ctrl)
        row1.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        # Symbol Entry: the underlying ticker, read into Contract.symbol at stream time.
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

        # ---- OU MODEL & CALIBRATION BAND (row 2) ----
        # Controls that govern how the AR(1)/OU process is fit, plus a read-only
        # readout of the three fitted parameters. Inputs on the left drive
        # estimate_ar1; the φ/μ/σ labels on the right are written by the calibration
        # routine so the trader can see the live model state without opening the log.
        ou_frame = ttk.LabelFrame(main, text="OU Model & Calibration", padding=8)
        ou_frame.grid(row=2, column=0, sticky='ew', pady=(0, 4))
        ou_frame.columnconfigure(1, weight=1)
        row_ou = ttk.Frame(ou_frame)
        row_ou.grid(row=0, column=0, sticky='ew')
        # Bar size: the resampling cadence for OHLC bars and therefore the time step Δt
        # of the OU fit. readonly Combobox = pick-from-list dropdown, no free typing.
        ttk.Label(row_ou, text="Bar size").pack(side='left', padx=(0, 4))
        bar_combo = ttk.Combobox(row_ou, 
                                 # below, the textvariable argument of this Combobox is the value
                                 # that will be displayed in the Combobox
                                 # We assign self.bar_size_var to be its value, because this is 
                                 # an attribute of the KalmanTradingApp class [and of any instance of it]
                                 # stored as an attribute precisely because it allows us to readily
                                 # update it and /or read its current value from anywhere else in the class
                                 # Note that this is updated in the following key ways:
                                 # - when the user selects a different bar size from the dropdown, which is
                                 #  handled by the _on_bar_size_change method that is bound to the Combobox's
                                    #  <<ComboboxSelected>> callback event..
                                 textvariable=self.bar_size_var,
                                 values=("30 s", "1 m", "5 m", "15 m", "30 m", "60 m", "1 d"), 
                                 width=8, 
                                 state="readonly")
        bar_combo.pack(side='left', padx=(0, 12))
        # Calibration window: how many trailing bars estimate_ar1 fits over. Entry box
        # (free integer) so the user can trade off responsiveness vs. stability.
        ttk.Label(row_ou, text="Calib window (bars)").pack(side='left', padx=(0, 4))
        ttk.Entry(row_ou, textvariable=self.calib_window_var, width=6).pack(side='left', padx=(0, 12))
        # Online toggle: when checked, refit φ/μ/σ on every new bar close (rolling);
        # when unchecked, the parameters are frozen at the last manual Refresh.
        self.online_cb = ttk.Checkbutton(row_ou, text="Recalibrate OU each new bar", variable=self.online_params_var)
        self.online_cb.pack(side='left', padx=(0, 16))
        # φ — AR(1) persistence (mean-reversion speed; φ→1 = slow, φ→0 = fast). Static
        # tk.Label, text set to "—" until the first calibration writes the fitted value.
        ttk.Label(row_ou, text="φ (phi):").pack(side='left', padx=(0, 2))
        self.phi_lbl = tk.Label(row_ou, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.phi_lbl.pack(side='left', padx=(0, 8))
        # μ — the OU long-run mean level the bands are centred on (mu ± k·σ).
        ttk.Label(row_ou, text="μ (mean):").pack(side='left', padx=(0, 2))
        self.mu_lbl = tk.Label(row_ou, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.mu_lbl.pack(side='left', padx=(0, 8))
        # σ — stationary std of the OU process; sets band width and the forecast cone.
        ttk.Label(row_ou, text="σ:").pack(side='left', padx=(0, 2))
        self.sigma_lbl = tk.Label(row_ou, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.sigma_lbl.pack(side='left')

        # ---- KALMAN TRUST LEVER BAND (row 3) ----
        # A single slider that trades off measurement noise R vs. process noise: pulled
        # toward "Trust prices" the filter tracks raw ticks; toward "Trust OU" it leans
        # on the model and smooths through noise. _on_noise_lever maps 0..100 -> R and
        # rescales self.kalman.R live, so the effect is visible on the next redraw.
        noise_frame = ttk.LabelFrame(main, text="Kalman: Trust prices ↔ Trust OU model", padding=8)
        noise_frame.grid(row=3, column=0, sticky='ew', pady=(0, 4))
        noise_frame.columnconfigure(1, weight=1)
        row_noise = ttk.Frame(noise_frame)
        row_noise.grid(row=0, column=0, sticky='ew')
        # Left anchor label: slider min (0%) = fully trust the incoming prices.
        ttk.Label(row_noise, text="Trust prices").pack(side='left', padx=(0, 6))
        # The lever itself. ttk.Scale = a draggable horizontal slider bound to a
        # DoubleVar; command fires continuously while dragging so R updates live.
        self.noise_slider = ttk.Scale(row_noise, from_=0, to=100, variable=self.noise_lever_var, orient='horizontal', length=280, command=self._on_noise_lever)
        self.noise_slider.pack(side='left', padx=(0, 6))
        # Right anchor label: slider max (100%) = fully trust the OU model.
        ttk.Label(row_noise, text="Trust OU").pack(side='left', padx=(0, 8))
        # Numeric echo of the slider position, updated by _on_noise_lever so the user
        # sees the exact percentage rather than guessing from the handle position.
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

        # ---- TRADING & PORTFOLIO BAND (row 5) ----
        # Two sub-rows: row2 = manual order buttons + live position/portfolio readouts,
        # row_auto = the auto-trade controls (built below). Manual and auto share one
        # frame so the trader reads them as one control surface.
        trade = ttk.LabelFrame(main, text="Trading & Portfolio", padding=8)
        trade.grid(row=5, column=0, sticky='ew', pady=(0, 8))
        row2 = ttk.Frame(trade)
        row2.grid(row=0, column=0, sticky='ew')
        # Manual order buttons. The lambda passes a signed direction to place_trade:
        # +1 = go long, -1 = go short, 0 = flatten. Close is Accent-styled as the
        # "exit" action so it stands out from the entry buttons.
        ttk.Button(row2, text="Long", command=lambda: self.place_trade(1)).pack(side='left', padx=(0, 6))
        ttk.Button(row2, text="Short", command=lambda: self.place_trade(-1)).pack(side='left', padx=(0, 6))
        ttk.Button(row2, text="Close", command=lambda: self.place_trade(0), style='Accent.TButton').pack(side='left', padx=(0, 12))
        # Live position readout (signed units). Updated from fills; grey until non-zero.
        ttk.Label(row2, text="Position:").pack(side='left', padx=(0, 4))
        self.pos_lbl = tk.Label(row2, text="0", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#8b949e')
        self.pos_lbl.pack(side='left', padx=(0, 12))
        # Live portfolio value. Base is today's IBKR NetLiquidation when available
        # (_sim_base_equity), else the 100k sim cash base; sim/replay P&L accrues on top.
        ttk.Label(row2, text="Portfolio:").pack(side='left', padx=(0, 4))
        self.port_lbl = tk.Label(row2, text="100000.00", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#58a6ff')
        self.port_lbl.pack(side='left')

        # ---- POSITION DETAIL ROW ----
        # Second info row: avg entry price, position market value, and decomposed P&L
        # (realised + unrealised shown separately as absolute dollar figures). These
        # always reflect the active mode (sim or live) so the trader sees the full book.
        row_pos_detail = ttk.Frame(trade)
        row_pos_detail.grid(row=1, column=0, sticky='ew', pady=(2, 0))
        ttk.Label(row_pos_detail, text="@ avg entry:").pack(side='left', padx=(0, 2))
        self.avg_entry_lbl = tk.Label(row_pos_detail, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.avg_entry_lbl.pack(side='left', padx=(0, 14))
        ttk.Label(row_pos_detail, text="Mkt val:").pack(side='left', padx=(0, 2))
        self.mkt_val_lbl = tk.Label(row_pos_detail, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.mkt_val_lbl.pack(side='left', padx=(0, 14))
        ttk.Label(row_pos_detail, text="Total P&L:").pack(side='left', padx=(0, 2))
        self.total_pnl_lbl = tk.Label(row_pos_detail, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.total_pnl_lbl.pack(side='left', padx=(0, 4))
        ttk.Label(row_pos_detail, text="(R:").pack(side='left', padx=(0, 2))
        self.realised_pnl_lbl = tk.Label(row_pos_detail, text="—", font=('Consolas', 10), bg='#0d1117', fg='#2ea043')
        self.realised_pnl_lbl.pack(side='left', padx=(0, 4))
        ttk.Label(row_pos_detail, text="U:").pack(side='left', padx=(0, 2))
        self.unrealised_pnl_lbl = tk.Label(row_pos_detail, text="—", font=('Consolas', 10), bg='#0d1117', fg='#7ee787')
        self.unrealised_pnl_lbl.pack(side='left', padx=(0, 2))
        ttk.Label(row_pos_detail, text=")").pack(side='left')

        # ---- AUTO-TRADE ROW ----
        # Third row inside the same "Trading & Portfolio" frame (row 0 = manual buttons,
        # row 1 = position detail, row 2 = auto-trade controls).
        row_auto = ttk.Frame(trade)
        row_auto.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        # Master toggle. Unchecked = on_tick never dispatches signal-driven orders.
        ttk.Checkbutton(row_auto, text="Auto-trade (OU mean-reversion)",
                        variable=self.auto_trade_var).pack(side='left', padx=(0, 12))
        # Label + combobox for the evaluation cadence (every N bar closes).
        ttk.Label(row_auto, text="Eval every N bars:").pack(side='left', padx=(0, 4))
        ttk.Combobox(row_auto, textvariable=self.auto_eval_bars_var,
                     values=(1, 2, 3, 5, 10), width=4,
                     state="readonly").pack(side='left', padx=(0, 12))
        # Max total |position| in units — blank = None = uncapped (recommended). The
        # weight controls (min_turnover, max_weight) are the primary sizing mechanism; this
        # is a secondary safety ceiling for when a hard unit limit is also required.
        ttk.Label(row_auto, text="Max pos (units):").pack(side='left', padx=(0, 4))
        ttk.Entry(row_auto, textvariable=self.max_position_var,
                  width=7).pack(side='left', padx=(0, 12))
        # Max single-trade size in units — blank = None = uncapped (recommended). Limits
        # the per-fire qty so a huge recommendation doesn't dump in one bar.
        ttk.Label(row_auto, text="Max trade (units):").pack(side='left', padx=(0, 4))
        ttk.Entry(row_auto, textvariable=self.max_trade_var,
                  width=7).pack(side='left', padx=(0, 12))
        # Small status label so user can SEE that auto-trade fired (or was capped)
        # without having to grep the log file in real time.
        self.auto_status_lbl = tk.Label(row_auto, text="auto: idle",
                                        font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.auto_status_lbl.pack(side='left', padx=(8, 0))

        # ---- RISK-CAP ROW ----
        # Two user-editable number entries (PERCENT) feeding the recommendation engine: the
        # min-turnover weight gate and the max single-position weight. _compute_recommendation
        # reads them live (via _min_turnover_frac / _max_weight_frac), so editing a value
        # re-sizes the NEXT recommendation; committing it (<Return>/<FocusOut>) also repaints
        # the panel immediately so the trader sees the effect without waiting for a new bar.
        row_risk = ttk.Frame(trade)
        row_risk.grid(row=3, column=0, sticky='ew', pady=(6, 0))
        # Each cap = an ON/OFF checkbox + a percent entry. Unchecking disables the cap
        # entirely (gate -> 0 / weight cap -> unbounded) and greys out its entry.
        ttk.Checkbutton(row_risk, variable=self.min_turnover_on_var,
                        command=self._sync_risk_cap_entries).pack(side='left')
        ttk.Label(row_risk, text="Min turnover %:").pack(side='left', padx=(0, 4))
        self.mt_entry = ttk.Entry(row_risk, textvariable=self.min_turnover_var, width=6)
        self.mt_entry.pack(side='left', padx=(0, 12))
        ttk.Checkbutton(row_risk, variable=self.max_weight_on_var,
                        command=self._sync_risk_cap_entries).pack(side='left')
        ttk.Label(row_risk, text="Max weight %:").pack(side='left', padx=(0, 4))
        self.mw_entry = ttk.Entry(row_risk, textvariable=self.max_weight_var, width=6)
        self.mw_entry.pack(side='left', padx=(0, 12))
        # Repaint the recommendation the moment either cap value is committed.
        for _e in (self.mt_entry, self.mw_entry):
            _e.bind('<Return>', lambda _evt: self._on_risk_cap_change())
            _e.bind('<FocusOut>', lambda _evt: self._on_risk_cap_change())
        # Initial grey-out + first compute to match the default checkbox states.
        self._sync_risk_cap_entries()
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
        ttk.Label(rec_row, text="Worst:").pack(side='left', padx=(0, 2))
        self.rec_worst_lbl = tk.Label(rec_row, text="—", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#f85149')
        self.rec_worst_lbl.pack(side='left', padx=(0, 12))
        # Best-case return (high quantile) — green, intensity scales with upside size.
        ttk.Label(rec_row, text="Best:").pack(side='left', padx=(0, 2))
        self.rec_best_lbl = tk.Label(rec_row, text="—", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#3fb950')
        self.rec_best_lbl.pack(side='left', padx=(0, 16))
        # The concrete recommended order (direction + qty + value delta) — the §3c spec
        # collapsed to one readable line; this is exactly what auto-trade would send.
        ttk.Label(rec_row, text="Trade:").pack(side='left', padx=(0, 2))
        self.rec_trade_lbl = tk.Label(rec_row, text="—", font=('Consolas', 11, 'bold'), bg='#0d1117', fg='#58a6ff')
        self.rec_trade_lbl.pack(side='left', padx=(0, 16))
        # Which cap (if any) is binding the target weight — explains a flat/clipped call.
        ttk.Label(rec_row, text="Weight change:").pack(side='left', padx=(0, 2))
        self.rec_cap_lbl = tk.Label(rec_row, text="—", font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.rec_cap_lbl.pack(side='left')

        # ---- HISTORICAL REPLAY BAND (row 7, plan §7 build-step 7) ----
        # "Compute once, scrub freely." The user loads a long bar history, clicks Build
        # replay (runs the full pipeline ONCE), then DRAGS the slider to any point to see
        # the exact chart / P&L / position they would have had if the replay had run up to
        # that bar — no waiting, no play/pause, just scrub like a Plotly range handle.
        replay = ttk.LabelFrame(main, text="Historical Replay — build once, drag to any point in time", padding=8)
        replay.grid(row=7, column=0, sticky='ew', pady=(0, 8))
        replay.columnconfigure(1, weight=1)   # column 1 (the slider) absorbs the width
        replay_row = ttk.Frame(replay) 
        replay_row.grid(row=0, column=0, sticky='ew')
        replay.columnconfigure(0, weight=1)
        replay_row.columnconfigure(3, weight=1)  # the slider cell stretches
        # Bars-to-load entry: how deep a history _build_replay pulls (more bars = longer
        # scrubbable timeline, slower one-off build). Free integer so the user picks.
        ttk.Label(replay_row, text="Bars to load:").grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(replay_row, textvariable=self.replay_bars_var, width=7).grid(row=0, column=1, padx=(0, 12))
        # Build button: the single heavy action. Disabled-looking work happens inside;
        # afterwards the slider goes live. Needs a connection (it pulls historical bars).
        self.replay_build_btn = ttk.Button(replay_row, text="⟲ Build replay", command=self._build_replay)
        self.replay_build_btn.grid(row=0, column=2, padx=(0, 12))
        # THE SCRUBBER. ttk.Scale bound to replay_slider_var; command fires continuously
        # while dragging so the chart tracks the handle. Starts disabled (nothing built
        # yet) and is range-configured + enabled by _build_replay once frames exist.
        self.replay_slider = ttk.Scale(replay_row, from_=0, to=1, orient='horizontal',
                                       variable=self.replay_slider_var,
                                       command=self._on_replay_scrub, state='disabled')
        self.replay_slider.grid(row=0, column=3, sticky='ew', padx=(0, 12))
        # Cursor readout: which bar (index + timestamp) the handle currently sits on, so
        # the user knows exactly where in history they are scrubbed to.
        self.replay_pos_lbl = tk.Label(replay_row, text="— build a replay to scrub —",
                                       font=('Consolas', 10), bg='#0d1117', fg='#8b949e')
        self.replay_pos_lbl.grid(row=0, column=4, padx=(0, 12))
        # Export button (§7.5): dump the order ledger + per-bar state to JSON on demand
        # (replay_orders.json in dummy_orders.json shape, replay_state.json one row/bar).
        # _build_replay already auto-exports; this lets the user re-dump after manual sim
        # trades too. Pops a confirmation messagebox (quiet=False).
        ttk.Button(replay_row, text="⤓ Export JSON",
                   command=lambda: self._export_replay_json(quiet=False)
                   ).grid(row=0, column=5, padx=(0, 8))
        # Plotly export (§F): additive deep-zoom HTML view of the same replay state_df —
        # render-once, opens in the browser. Independent of the in-app matplotlib scrubber.
        ttk.Button(replay_row, text="⧉ Export Plotly",
                   command=self._export_replay_plotly
                   ).grid(row=0, column=6, padx=(0, 0))
        # Jump-to-bar: type a 1-based bar number + Enter/Go to scrub there exactly (the
        # slider is coarse for precise targets). Range-checked in _jump_to_bar.
        # ← / → step buttons move one bar back or forward from wherever the slider is.
        ttk.Label(replay_row, text="Go to bar:").grid(row=0, column=7, padx=(12, 4))
        jump_entry = ttk.Entry(replay_row, textvariable=self.replay_jump_var, width=6)
        jump_entry.grid(row=0, column=8, padx=(0, 4))
        jump_entry.bind('<Return>', lambda _evt: self._jump_to_bar())
        ttk.Button(replay_row, text="Go", width=4,
                   command=self._jump_to_bar).grid(row=0, column=9, padx=(0, 4))
        # ← / → step one bar in either direction without typing. Default start=120 so
        # these are immediately useful after a build (slider parked at end, but the user
        # can jump to 120 then step from there).
        ttk.Button(replay_row, text="←", width=3,
                   command=self._jump_prev_bar).grid(row=0, column=10, padx=(0, 2))
        ttk.Button(replay_row, text="→", width=3,
                   command=self._jump_next_bar).grid(row=0, column=11, padx=(0, 0))

        chart_frame = ttk.LabelFrame(main, text="OHLC | Orange = Kalman mean (history) | Solid line = live mean (updates every tick) | Purple = OU forecast", padding=6)
        chart_frame.grid(row=8, column=0, sticky='nsew')
        chart_frame.columnconfigure(0, weight=1)
        # Row 0 = plot-series toggles, row 1 = the canvas (which stretches).
        chart_frame.rowconfigure(1, weight=1)

        # ---- PLOT TOGGLE ROW (§7.2 series visibility, Tk checkbuttons) ----
        # Layout (plan §A/§B): a master "Analytics axes" switch + a "Pop out" button, then
        # one LabelFrame group PER analytics axis. Each group = an axis on/off checkbox plus
        # one checkbox per line series in that axis. Axis toggles rebuild the layout (the
        # pane appears/disappears, so the figure re-flows and nothing is crammed); series
        # toggles just redraw. When an axis (or the master) is off, that group's series
        # checkboxes are greyed out by _sync_toggle_states — you only pick series for the
        # axes you're actually showing.
        toggles = ttk.Frame(chart_frame)
        toggles.grid(row=0, column=0, sticky='ew', pady=(0, 4))
        # Master switch: off => single price axis (original app); on => show whichever of the
        # three per-axis groups below are ticked. Rebuilds the axes AND re-syncs grey-out.
        ttk.Checkbutton(toggles, text="Analytics axes", variable=self.show_analytics_var,
                        command=self._on_axis_toggle).pack(side='left', padx=(0, 12))
        # Pop-out button (plan §C): float the whole plot set into a resizable window. Lives at
        # the LEFT of the flow (after the master) so it doesn't fight the wrapping groups.
        self.popout_btn = ttk.Button(toggles, text="⤢ Pop out", width=10,
                                     command=self._toggle_popout)
        self.popout_btn.pack(side='left', padx=(0, 12))

        # Collected so _sync_toggle_states can enable/disable widgets as a unit:
        #   _axis_checkbuttons — the three per-axis on/off boxes (greyed when master off).
        #   _series_groups     — (axis_var, [series checkbuttons]) so a group's series boxes
        #                        grey out when the master OR that axis is off.
        self._axis_checkbuttons = []
        self._series_groups = []

        # Helper: build one axis group = a LabelFrame holding the axis checkbox + its series
        # checkboxes. `series` is a list of (label, var) drawn left-to-right inside the group.
        def _axis_group(title, axis_var, series):
            grp = ttk.LabelFrame(toggles, text=title, padding=(6, 0))
            grp.pack(side='left', padx=(0, 10))
            axis_cb = ttk.Checkbutton(grp, text="show", variable=axis_var,
                                      command=self._on_axis_toggle)
            axis_cb.pack(side='left', padx=(0, 8))
            self._axis_checkbuttons.append(axis_cb)
            cbs = []
            for label, var in series:
                cb = ttk.Checkbutton(grp, text=label, variable=var, command=self.redraw_chart)
                cb.pack(side='left', padx=(0, 6))
                cbs.append(cb)
            self._series_groups.append((axis_var, cbs))

        # Price pane (ALWAYS shown — no axis on/off): per-series visibility for everything
        # the main plot draws except the bands / forecast line, which keep their own controls
        # in the trading-bands row. "markers" = the ▲/▼ trade markers; "rationale" toggles
        # whether their hover tooltip appends the full recommendation block. These just redraw.
        price_grp = ttk.LabelFrame(toggles, text="Price", padding=(6, 0))
        price_grp.pack(side='left', padx=(0, 10))
        for label, var in (("candles", self.show_candles_var),
                           ("close", self.show_close_var),
                           ("kalman hist", self.show_kalman_hist_var),
                           ("live mean", self.show_live_mean_var),
                           ("μ", self.show_longmean_var),
                           ("markers", self.show_markers_var),
                           ("rationale", self.show_marker_rationale_var)):
            ttk.Checkbutton(price_grp, text=label, variable=var,
                            command=self.redraw_chart).pack(side='left', padx=(0, 6))

        # P&L pane: realised / unrealised / total.
        _axis_group("P&L", self.show_pnl_axis_var,
                    [("realised", self.show_realised_var),
                     ("unrealised", self.show_unrealised_var),
                     ("total", self.show_total_var)])
        # Units pane: filled / intended / next-intended / unfilled / closed.
        _axis_group("Units", self.show_pos_axis_var,
                    [("filled", self.show_filled_var),
                     ("intended", self.show_intended_var),
                     ("next int.", self.show_next_intended_var),
                     ("unfilled", self.show_unfilled_var),
                     ("closed", self.show_closed_var)])
        # Entry-vs-market pane: avg entry / last.
        _axis_group("Entry", self.show_entry_axis_var,
                    [("avg entry", self.show_entry_var),
                     ("last", self.show_last_var)])
        # Initial grey-out pass so series boxes match the current axis/master state.
        self._sync_toggle_states()

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
        # 1-BASED x display: data is plotted at 0-based integer positions, but the user
        # thinks/types in 1-based bar numbers (matching the scrubber "bar i+1/N" + the
        # jump-to-bar box). A FuncFormatter shifts only the TICK LABELS by +1, so every
        # axis reads bar 1..N while the underlying positions stay 0..N-1 (hover/markers/
        # slicing untouched). ax.clear() drops this, so it's reapplied on every restyle.
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{int(round(v)) + 1}"))

    def _make_hover_annot(self, ax):
        # Build one reusable hover tooltip Annotation on `ax` (plan §D). Factored out
        # because we now keep one per axis and recreate them after every ax.clear().
        # clip_on=False so the box can extend past its own axis (it's repositioned by
        # _on_hover to stay inside the FIGURE), and a high zorder so it floats above
        # every pane / overlapping artist.
        ann = ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords='offset points',
            bbox=dict(boxstyle='round,pad=0.4', fc='#161b22', ec='#58a6ff', alpha=0.95),
            color='#c9d1d9', fontsize=9, family='Consolas', zorder=20,
        )
        ann.set_clip_on(False)
        ann.set_visible(False)
        return ann

    def _build_axes(self):
        # (Re)build the axis layout on self.fig. Called by setup_chart and whenever any
        # axis toggle flips (_on_axis_toggle -> _rebuild_chart_axes). The layout is now
        # DYNAMIC (plan §A): the price axis always sits on top, then ONE short pane per
        # enabled analytics axis (P&L / Units / Entry), in that fixed order. Showing fewer
        # panes gives the survivors more room — no permanently-crammed [3,1,1,1] stack.
        # sharex=price so pan/zoom/scrub move every pane together and they line up
        # bar-for-bar. We clear the figure first so toggling doesn't leak orphaned axes.
        self.fig.clear()
        # Which analytics panes are live = master on AND that pane's own var on. Fixed order.
        panes = []
        if self.show_analytics_var.get():
            if self.show_pnl_axis_var.get():
                panes.append('pnl')
            if self.show_pos_axis_var.get():
                panes.append('pos')
            if self.show_entry_axis_var.get():
                panes.append('entry')
        # Reset every analytics handle; only the enabled ones are reassigned a real Axes.
        self.ax_pnl = self.ax_pos = self.ax_entry = None
        if panes:
            # height_ratios: price (3) dominates; each enabled analytics pane is a short 1.
            gs = self.fig.add_gridspec(1 + len(panes), 1,
                                       height_ratios=[3] + [1] * len(panes), hspace=0.08)
            self.ax = self.fig.add_subplot(gs[0])
            # Map pane name -> the attribute that holds its Axes, so _draw_* find them.
            attr = {'pnl': 'ax_pnl', 'pos': 'ax_pos', 'entry': 'ax_entry'}
            axes = [self.ax]
            for r, name in enumerate(panes, start=1):
                a = self.fig.add_subplot(gs[r], sharex=self.ax)
                setattr(self, attr[name], a)
                axes.append(a)
            for a in axes:
                self._style_ax(a)
            # Only the bottom-most axis shows x tick labels — they share x, so repeating
            # the labels on every pane is noise.
            for a in axes[:-1]:
                a.tick_params(labelbottom=False)
            # Remember the bottom axis so redraw_chart can put the single x-label there.
            self._bottom_ax = axes[-1]
        else:
            # Single-axis fallback: the analytics axes simply don't exist (original app).
            self.ax = self.fig.add_subplot(1, 1, 1)
            self._style_ax(self.ax)
            self._bottom_ax = self.ax

        # Hover tooltips (plan §D): one reusable Annotation per EXISTING axis, keyed by the
        # Axes object. Rebuilt here because fig.clear() destroyed any previous ones.
        self._hover_annots = {
            a: self._make_hover_annot(a)
            for a in (self.ax, self.ax_pnl, self.ax_pos, self.ax_entry) if a is not None
        }

    def _rebuild_chart_axes(self):
        # Toggle handler for any axis change: rebuild the layout then repaint. Kept
        # separate from redraw_chart because redraw assumes the axes already exist;
        # this is the only place that changes how many axes there ARE.
        self._build_axes()
        self.redraw_chart()

    def _on_axis_toggle(self):
        # Command for the master + the three per-axis checkboxes (plan §A): first grey out
        # the series checkboxes that no longer apply, then rebuild the (now different) set
        # of axes and repaint.
        self._sync_toggle_states()
        self._rebuild_chart_axes()

    def _sync_toggle_states(self):
        # Enable/disable checkboxes so you only pick series for axes you're actually showing
        # (plan §B). The three axis boxes are live only while the master is on; each group's
        # series boxes are live only while the master AND that group's axis are on.
        master = self.show_analytics_var.get()
        for cb in self._axis_checkbuttons:
            cb.config(state='normal' if master else 'disabled')
        for axis_var, cbs in self._series_groups:
            on = master and axis_var.get()
            for cb in cbs:
                cb.config(state='normal' if on else 'disabled')

    def setup_chart(self):
        plt.style.use('dark_background')
        # Create the figure ONCE. The CANVAS is (re)mounted by _mount_canvas under whatever
        # parent currently hosts the plots — the in-app container or the pop-out window —
        # because Tk widgets can't be reparented after creation.
        self.fig = plt.figure(figsize=(11, 6), facecolor='#0d1117')
        self._build_axes()
        self._last_log_time = 0.0
        self._mount_canvas(self.chart_container)

    def _mount_canvas(self, parent):
        # (Re)build the FigureCanvasTkAgg + navigation toolbar under `parent` (plan §C/§E).
        # Popping out / docking tears down the old canvas widgets and rebuilds them here
        # against the new parent; self.canvas is repointed so every existing caller
        # (canvas.draw_idle) keeps working unchanged. A holder frame stacks the zoom toolbar
        # (row 0) above the canvas (row 1) so the pair moves as a single unit, and the
        # toolbar gets its own frame to pack() into as Tk requires.
        self._canvas_holder = ttk.Frame(parent)
        self._canvas_holder.grid(row=0, column=0, sticky='nsew')
        self._canvas_holder.columnconfigure(0, weight=1)
        self._canvas_holder.rowconfigure(1, weight=1)
        toolbar_frame = ttk.Frame(self._canvas_holder)
        toolbar_frame.grid(row=0, column=0, sticky='ew')
        self.canvas = FigureCanvasTkAgg(self.fig, master=self._canvas_holder)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky='nsew')
        # Native matplotlib zoom: box-zoom / pan / home/reset, axes auto-rescale. Rebuilt on
        # every mount so it follows the plots into the pop-out window.
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()
        # Hover events bind to the live canvas — rebound here because the canvas is new.
        self.canvas.mpl_connect('motion_notify_event', self._on_hover)
        self.canvas.mpl_connect('axes_leave_event', self._on_hover_leave)
        self.canvas.draw_idle()

    def _toggle_popout(self):
        # Float the whole plot set into its own resizable window, or dock it back (plan §C).
        # One figure throughout — only the Tk canvas widgets are torn down and rebuilt.
        if self._popout is None:
            # ---- POP OUT ----
            # Drop the in-app canvas widgets (can't reparent), then mount onto a new window.
            self._canvas_holder.destroy()
            self._popout = tk.Toplevel(self.root)
            self._popout.title("Charts — popped out")
            self._popout.configure(bg='#0d1117')
            self._popout.geometry("1300x900")
            self._popout.columnconfigure(0, weight=1)
            self._popout.rowconfigure(0, weight=1)
            # Closing the window (X) docks back rather than killing the app.
            self._popout.protocol("WM_DELETE_WINDOW", self._toggle_popout)
            self._mount_canvas(self._popout)
            # In-app slot: a placeholder button that restores the plots on click.
            self._popout_placeholder = ttk.Button(
                self.chart_container, text="▣ Plots popped out — click to restore",
                command=self._toggle_popout)
            self._popout_placeholder.grid(row=0, column=0, sticky='nsew')
            self.popout_btn.config(text="⤢ Restore")
        else:
            # ---- DOCK BACK ----
            self._popout_placeholder.destroy()
            win = self._popout
            self._popout = None
            win.destroy()   # also destroys the canvas holder living inside it
            self._mount_canvas(self.chart_container)
            self.popout_btn.config(text="⤢ Pop out")
        # Axes + their content live on the figure (not the canvas), but redraw to refresh
        # the hover annotations and guarantee the new canvas shows the current state.
        self.redraw_chart()

    def _on_hover_leave(self, _evt):
        # Hide whichever per-axis tooltip is currently visible.
        any_vis = False
        for ann in self._hover_annots.values():
            if ann.get_visible():
                ann.set_visible(False)
                any_vis = True
        if any_vis:
            self.canvas.draw_idle()

    def _on_hover(self, event):
        # Per-axis hover tooltip (plan §D). Fires on the price axis AND every visible
        # analytics axis, showing ONLY the series enabled for that axis, with the box
        # flipped to whichever corner keeps it inside the figure. GUI-thread (mpl event).
        ax = event.inaxes
        annot = self._hover_annots.get(ax) if ax is not None else None
        # Cursor outside any tracked axis -> hide everything.
        if annot is None or event.xdata is None:
            self._on_hover_leave(event)
            return
        # Hide any OTHER axis's tooltip so only the hovered one shows.
        for other in self._hover_annots.values():
            if other is not annot:
                other.set_visible(False)
        # All series sit at integer x indices 0..n-1 (forming bar at n on the price axis).
        i = int(round(event.xdata))
        if ax is self.ax:
            # On the price axis, a ▲/▼ trade marker under the cursor wins over the OHLC box:
            # show the trade tooltip (colour/bold by side), else the normal OHLC readout.
            mk = self._trade_marker_at(event)
            if mk is not None:
                text, y, i = self._hover_text_trade(mk), mk["price"], mk["x"]
                annot.set_color('#3fb950' if mk["side"] == "BUY" else '#f85149')
                annot.set_fontweight('bold')
            else:
                # Then the per-element templates (forecast / bands / kalman-mean); else OHLC.
                special = self._price_hover_special(event)
                annot.set_color('#c9d1d9')
                annot.set_fontweight('normal')
                if special is not None:
                    text, (i, y) = special
                else:
                    text, y = self._hover_text_price(i)

        # otherwise, which occurs if ax is one of the analytics panes, show the analytics tooltip
        #  for that axis (only the enabled series, so the box matches what's drawn) or 
        # nothing if none apply at this bar.
        else:
            text, y = self._hover_text_analytics(ax, i)
            annot.set_color('#c9d1d9')
            annot.set_fontweight('normal')
        if text is None:
            annot.set_visible(False)
            self.canvas.draw_idle()
            return
        # ---- clamp: flip the offset quadrant so the box never leaves the figure ----
        # Use the cursor's fractional position inside the axis bbox: upper half -> draw the
        # box BELOW the point; right-ish -> draw it to the LEFT. clip_on=False (set at
        # creation) lets it overhang its own axis; this flip keeps it within the figure.
        bbox = ax.get_window_extent()
        fx = (event.x - bbox.x0) / bbox.width if bbox.width else 0.0
        fy = (event.y - bbox.y0) / bbox.height if bbox.height else 0.0
        dx, ha = (12, 'left') if fx < 0.6 else (-12, 'right')
        dy, va = (12, 'bottom') if fy < 0.6 else (-12, 'top')
        annot.set_ha(ha)
        annot.set_va(va)
        annot.set_position((dx, dy))   # xytext offset in points (textcoords='offset points')
        annot.xy = (i, y)
        annot.set_text(text)
        annot.set_visible(True)
        self.canvas.draw_idle()

    def _hover_text_price(self, i):
        # OHLC tooltip text for bar index i (forming bar = n). Returns (text, y) anchored at
        # the bar close, or (None, None) when i is off the chart.
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
            return None, None
        t = bar['t']
        t_str = t.strftime('%Y-%m-%d %H:%M:%S') if hasattr(t, 'strftime') else str(t)
        text = (f"bar {i + 1}{label_suffix}\n"
                f"t  {t_str}\n"
                f"O  {bar['o']:.5f}\n"
                f"H  {bar['h']:.5f}\n"
                f"L  {bar['l']:.5f}\n"
                f"C  {bar['c']:.5f}")
        return text, bar['c']

    def _hover_text_analytics(self, ax, i):
        # Tooltip for an analytics axis at bar index i. Reads state_df.iloc[i] and lists
        # ONLY the series whose toggle is on for that axis (so the box matches what's drawn).
        # Returns (text, y_anchor) — y is the first shown series' value — or (None, None).
        if self.state_df.empty or not (0 <= i < len(self.state_df)):
            return None, None
        row = self.state_df.iloc[i]
        lines = [f"bar {i + 1}"]
        anchor = {'y': 0.0, 'set': False}

        # Append "label value" for a series only when its toggle is on and the value is
        # finite; capture the first such value as the box's y-anchor.
        def add(label, value, var):
            if var.get() and value is not None and np.isfinite(value):
                lines.append(f"{label} {value:,.4g}")
                if not anchor['set']:
                    anchor['y'] = value
                    anchor['set'] = True

        if ax is self.ax_pnl:
            add("realised", row.get("realised_pnl"), self.show_realised_var)
            add("unrealised", row.get("unrealised_pnl"), self.show_unrealised_var)
            add("total", row.get("total_pnl"), self.show_total_var)
        elif ax is self.ax_pos:
            add("filled", row.get("filled"), self.show_filled_var)
            add("intended", row.get("intended"), self.show_intended_var)
            # next_intended = target_value / reference_price (total recommended position in
            # units). Not stored as its own column — compute on the fly from the same
            # source columns that _draw_positions uses, so hover and line always agree.
            try:
                tv_f = float(row.get("target_value"))
                rp_f = float(row.get("reference_price"))
                ni = tv_f / rp_f if (np.isfinite(tv_f) and np.isfinite(rp_f) and rp_f != 0) else None
            except (TypeError, ValueError):
                ni = None
            add("next int.", ni, self.show_next_intended_var)
            add("unfilled", row.get("unfilled"), self.show_unfilled_var)
            add("closed", row.get("closed_units"), self.show_closed_var)
        elif ax is self.ax_entry:
            add("avg entry", row.get("avg_entry_price"), self.show_entry_var)
            add("last", row.get("last_price"), self.show_last_var)
        # Nothing enabled at this bar -> no tooltip.
        if len(lines) == 1:
            return None, None
        return "\n".join(lines), anchor['y']

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
        # Leave any active historical replay so a stale _replay_mark can't hijack the
        # marking of a subsequent live stream, and the scrubber disarms.
        self._exit_replay()
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
            # First leave any active historical replay: live ticks must mark at the real
            # last price, not a frozen _replay_mark, so we drop the override + disarm the
            # scrubber before going live.
            self._exit_replay()
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
        # ============================================================================
        # BAR SIZE TRANSLATOR — UI label → (seconds, IB API string).
        # Called before every historical data request and before each replay, so
        # both the OU step Δt (bar_sec) and IB's barSizeSetting come from one place.
        #
        # The UI labels are intentionally short ("1 m", "15 m", …); the IB API
        # requires its own exact vocabulary ("1 min", "15 mins", "1 hour", "1 day").
        # Mapping is explicit rather than programmatic so the IB strings are obvious
        # and any future IB-API change is trivially located here.
        #
        # Return contract: (int seconds_per_bar, str ib_bar_size_setting)
        # ============================================================================
        bs = self.bar_size_var.get().strip()
        # 30-second bars — shortest resolution the UI exposes; IB uses "30 secs".
        if bs == "30 s":
            return 30, "30 secs"
        # 5-minute bars — IB pluralises minutes for values > 1.
        if bs == "5 m":
            return 300, "5 mins"
        # 15-minute bars — common intraday swing resolution.
        if bs == "15 m":
            return 900, "15 mins"
        # 30-minute bars — half-hour; IB still uses "mins" plural.
        if bs == "30 m":
            return 1800, "30 mins"
        # 60-minute / hourly bars — IB uses "1 hour", not "60 mins".
        if bs == "60 m":
            return 3600, "1 hour"
        # Daily bars — IB uses "1 day"; on_tick bar-close logic is irrelevant at
        # this resolution (no intraday streaming), but the value is kept consistent
        # so any duration-string calculation still works correctly.
        if bs == "1 d":
            return 86400, "1 day"
        # Default fallback: 1-minute bars — also the UI's initial value ("1 m").
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

        # 2) Translate "I want N bars of size X" into IB's durationStr. Three tiers:
        #    < 86400 s  → "X S"; 1–365 days → "X D"; > 365 days → "X Y".
        #    IB error 1780 fires if days > 365 and you send "X D" — must use years.
        total_sec = num_bars * bar_sec
        total_days = total_sec // 86400
        if total_sec < 86400:
            duration_str = f"{max(60, total_sec)} S"
        elif total_days <= 365:
            duration_str = f"{max(1, total_days)} D"
        else:
            duration_str = f"{max(1, (total_days + 364) // 365)} Y"

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
        #    a UNIQUE reqId from our custom counter within self.ib, which in turn is 
        #    an instance of our IB class and uses its own per-request Event,
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

    def _ou_signal_side(self, last_price, kalman, mu, sigma, k, H):
        # ============================================================================
        # OU MEAN-REVERSION SIGNAL (pure decision — no Tk, no orders, no side effects).
        # SINGLE SOURCE OF TRUTH for the trade direction, shared by the LIVE auto-trader
        # (_check_auto_signal, on the IB reader thread) and the HISTORICAL REPLAY builder
        # (_build_replay) so both fire on identical logic. Returns a 4-tuple
        # (side, upper, lower, fc_end): side is +1 long / -1 short / None no-trade; the
        # bands + H-step forecast are handed back too so the caller can LOG them without
        # recomputing the forecast.
        # ============================================================================
        # Compute upper trading band: mu + k * sigma (stationary std).
        upper = mu + k * sigma
        # Compute lower trading band: mu - k * sigma.
        lower = mu - k * sigma
        # Run the OU forecast: returns a list of H projected mean levels at steps
        # 1..H ahead. We take the LAST element (step H) — that is the level we
        # expect the process to be at after H bars if it evolves purely under OU.
        forecast_path = kalman.forecast(H)
        # Defensive: empty/short forecast list -> no signal.
        if not forecast_path:
            return None, upper, lower, None
        # `fc_end` = the H-step-ahead OU mean projection. This is the "where we
        # expect to be in H bars" number that the signal pivots on.
        fc_end = forecast_path[-1]
        # Reject non-finite forecasts (shouldn't happen with sane OU params).
        if not np.isfinite(fc_end):
            return None, upper, lower, fc_end
        # Core mean-reversion test: is the H-step forecast INSIDE the trading bands?
        # If yes, the OU model is telling us "we will be back inside [lower, upper]
        # within H steps" — i.e. any current band breach is expected to revert.
        # If the forecast itself sits outside the bands, the model is implying the
        # breach is persistent (not mean-reverting in our horizon). Do nothing.
        if not (lower <= fc_end <= upper):
            return None, upper, lower, fc_end
        # Decide trade direction based on which side of the bands the SPOT price is
        # currently on. The forecast-inside condition above already confirmed we
        # expect to revert back inward.
        # Spot price is ABOVE the upper band -> rich -> short, expect down-revert.
        if last_price > upper:
            return -1, upper, lower, fc_end
        # Spot price is BELOW the lower band -> cheap -> long, expect up-revert.
        if last_price < lower:
            return +1, upper, lower, fc_end
        # Spot price already inside the bands -> no breach, nothing to fade.
        return None, upper, lower, fc_end

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
        # Read the forecast horizon H (number of OU steps to project forward).
        # Reuses the same Horizon control already shown in the chart frame, so the
        # purple cone on the chart and the signal evaluation point are consistent.
        try:
            H = max(1, int(self.forecast_horizon_var.get()))
        except (TypeError, ValueError):
            H = 5

        # Delegate the band/forecast/direction decision to the shared helper (the SAME
        # logic the historical-replay builder uses, so live and replay never diverge).
        # It hands back the bands and the H-step forecast so the dlog lines below can
        # report them without recomputing the forecast.
        side, upper, lower, fc_end = self._ou_signal_side(
            last_price, self.kalman, self.mu, self.sigma, k, H)
        # No actionable signal (forecast outside bands, or spot already inside) -> stop.
        if side is None:
            return

        # ---- RECOMMENDATION-DRIVEN SIZING ----
        # The order quantity comes from _compute_recommendation (via _last_forecast_view),
        # NOT a fixed step_qty. This means min_turnover and max_weight drive the SIZE of
        # each auto-trade, exactly as they drive the Recommendation panel; the OU band
        # signal above is purely the direction / gate (only fire at band extremes).
        # _last_forecast_view is the recommendation from the PREVIOUS bar close (set by
        # _on_bar_close → _refresh_recommendation on the main thread) — one bar stale, but
        # OU parameters change slowly so the lag is negligible for sizing purposes.
        fv = self._last_forecast_view
        if fv is None:
            # Model computed no recommendation yet (pre-calibration) — abort.
            return
        # If the turnover gate suppressed the trade (fv.qty == 0), the recommendation
        # engine said "not worth it" — honour that and skip the fire even if the band
        # breach is genuine. The bands and the weight controls must agree.
        if fv.qty == 0.0:
            dlog("auto_skip", reason="turnover_gate", last=last_price, fc_end=fc_end)
            self.root.after(0, lambda: self.auto_status_lbl.config(
                text="auto: turnover gate", fg='#8b949e'))
            return
        # The recommendation carries a signed qty (+= buy, -= sell). Sanity-check that the
        # sign agrees with the OU band direction (they should always agree; this guards
        # against edge cases like a reversed position that flips w_current sign).
        fill_side = 1 if fv.qty > 0 else -1
        if fill_side != side:
            # Direction conflict — the OU signal and the recommendation disagree.
            # Trust the OU signal as the gate and skip rather than flipping direction.
            dlog("auto_skip", reason="direction_conflict", side=side, fv_qty=fv.qty)
            return
        trade_qty = abs(fv.qty)
        # Optional hard cap on single-trade size (default uncapped — weight controls
        # already handle this via max_weight_frac). Clip WITHOUT adjusting direction.
        max_trade = self._max_trade_units()
        if max_trade is not None:
            trade_qty = min(trade_qty, max_trade)
        # Optional hard cap on total |position| in units. Clip the qty so the projected
        # position would not exceed the cap, rather than outright refusing the trade (a
        # smaller fill is better than none when the user wants some exposure).
        cur_pos = self.sim_position if self.sim_mode_var.get() else self.position
        max_pos = self._max_pos_units()
        if max_pos is not None:
            remaining = max(0.0, max_pos - abs(cur_pos))
            if remaining <= 0:
                dlog("auto_skip", reason="pos_cap", pos=cur_pos, cap=max_pos)
                self.root.after(0, lambda _c=max_pos: self.auto_status_lbl.config(
                    text=f"auto: pos cap @{_c:g}", fg='#d29922'))
                return
            trade_qty = min(trade_qty, remaining)
        if trade_qty <= 0:
            return

        # All gates passed. Log the fire event with everything a post-mortem needs.
        dlog("auto_fire", side=side, last=last_price, fc_end=fc_end,
             upper=upper, lower=lower, pos=cur_pos, qty=trade_qty, H=H)
        direction_str = "LONG" if side == 1 else "SHORT"
        self.root.after(0, lambda s=direction_str, p=last_price, q=trade_qty:
                        self.auto_status_lbl.config(
                            text=f"auto: {s} {q:.4g} @ {p:.4g}", fg='#7ee787'))
        # Dispatch the trade on the Tk main thread. place_trade touches Tk widgets
        # (status labels, position counters) AND calls ibapi.placeOrder — we keep
        # all of that off the IB reader thread by going through root.after.
        # Pass qty explicitly so the auto-trade uses the recommendation-sized amount
        # instead of place_trade's own fixed default (10 STK / 0.001 CRYPTO).
        self.root.after(0, lambda s=side, q=trade_qty: self.place_trade(s, qty=q))

    def place_trade(self, side, qty=None):
        # ============================================================================
        # Order entry. Invoked two ways, BOTH on the Tk MAIN thread:
        #   (a) the three Tkinter buttons wired up in setup_ui:
        #         "Long"  -> place_trade( 1)   (BUY  open)
        #         "Short" -> place_trade(-1)   (SELL open)
        #         "Close" -> place_trade( 0)   (flatten current position)
        #       via lambdas, e.g. ttk.Button(..., command=lambda: self.place_trade(1));
        #   (b) the auto-trade signal: _check_auto_signal (on the IB reader thread)
        #       dispatches self.root.after(0, lambda s=side, q=trade_qty:
        #           self.place_trade(s, qty=q)) so the actual order placement still
        #       lands on the Tk main thread with the recommendation-derived qty.
        # `qty` is the OPEN size in units. When None (manual buttons), the default below
        # applies (10 STK / 0.001 CRYPTO). When passed explicitly (auto-trade), it is
        # the recommendation-sized amount already validated + capped by _check_auto_signal.
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

        # Default open size when called from the manual buttons (qty=None):
        #   - STK/CASH: 10 units (shares / base-currency units).
        #   - CRYPTO: 0.001 BTC (~$70 at $70k BTC). A 100-BTC default would be
        #     a multi-million-dollar order on PAXOS and instantly rejected.
        # Auto-trade passes qty explicitly (recommendation-sized), bypassing these
        # defaults. Must be Decimal for ibapi >=10.19 for fractional CRYPTO precision.
        if qty is None:
            if is_crypto:
                qty = Decimal("0.001")
            else:
                qty = 10
        elif is_crypto:
            # Auto-trade passed a float qty; wrap in Decimal for PAXOS precision.
            qty = Decimal(str(round(float(qty), 8)))

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
        # Portfolio value: live NetLiquidation in live mode; in sim/replay it's the IBKR
        # NetLiq base (today's real portfolio value, via _sim_base_equity) plus the accrued
        # sim realised + unrealised P&L, so the figure starts at the real account and moves
        # with the simulated book.
        if self.ib.account_value is not None and not self.sim_mode_var.get():
            val = self.ib.account_value
        else:
            unreal = self._sim_unrealised(self._last_mark_price())
            val = self._sim_base_equity() + self.sim_realised + unreal
        self.port_lbl.config(text=f"{val:,.2f}")

        # ---- POSITION DETAIL ROW — avg entry, market value, decomposed P&L ----
        # Mark price for computing unrealised; falls back gracefully to None / 0 before
        # any ticks arrive.
        mark = self._last_mark_price()
        # Avg entry: meaningful only when a position is open (sim_avg_entry is 0 when flat).
        if self.sim_mode_var.get():
            avg_e = self.sim_avg_entry
            real_pnl = self.sim_realised
            unreal_pnl = self._sim_unrealised(mark) if mark else 0.0
        else:
            # Live mode: avg_entry comes from IBKR position callback (self.avg_entry),
            # P&L from the same source; fall back to 0 if not yet populated.
            avg_e = getattr(self, 'avg_entry', 0.0) or 0.0
            real_pnl = 0.0   # IBKR P&L requires a separate subscription — placeholder
            unreal_pnl = 0.0
        total_pnl = real_pnl + unreal_pnl
        # Market value = current open units × mark price (signed: positive = long).
        if mark and np.isfinite(mark) and mark > 0:
            mkt_val = shown_pos * mark
        else:
            mkt_val = None
        # Avg-entry label: show the price, or "—" when flat (avg_entry is 0 and meaningless).
        if shown_pos != 0 and avg_e and np.isfinite(avg_e) and avg_e > 0:
            self.avg_entry_lbl.config(text=f"{avg_e:.5g}")
        else:
            self.avg_entry_lbl.config(text="—")
        # Market value in dollars.
        if mkt_val is not None:
            self.mkt_val_lbl.config(text=f"${mkt_val:+,.2f}")
        else:
            self.mkt_val_lbl.config(text="—")
        # P&L decomposition: total (bright), realised (green), unrealised (lighter green).
        self.total_pnl_lbl.config(
            text=f"${total_pnl:+,.2f}",
            fg='#3fb950' if total_pnl >= 0 else '#f85149')
        self.realised_pnl_lbl.config(text=f"${real_pnl:+,.2f}")
        self.unrealised_pnl_lbl.config(text=f"${unreal_pnl:+,.2f}")

    def _sim_base_equity(self):
        # Starting portfolio value for the sim/replay book. Prefer today's IBKR
        # NetLiquidation (the user wants the real portfolio value as the base, and the
        # historical replay uses today's IBKR value as its starting equity); fall back to the
        # sim cash base only when no broker value is available (e.g. offline).
        av = self.ib.account_value
        if av is not None:
            try:
                return float(av)
            except (TypeError, ValueError):
                pass
        return self.cash

    def _min_turnover_frac(self):
        # Min-turnover weight gate as a FRACTION (the UI entry is in percent). Returns 0.0
        # when the gate checkbox is OFF (never suppress — any non-zero move trades). Falls
        # back to the REC_MIN_TURNOVER default on a blank/garbage entry so sizing never breaks.
        if not self.min_turnover_on_var.get():
            return 0.0
        try:
            return max(0.0, float(self.min_turnover_var.get()) / 100.0)
        except (TypeError, ValueError, tk.TclError):
            return REC_MIN_TURNOVER

    def _max_weight_frac(self):
        # Max single-position weight as a FRACTION (the UI entry is in percent). Returns an
        # effectively-unbounded ceiling (1000%) when the cap checkbox is OFF, so the clamp is
        # a no-op for any realistic target. Falls back to REC_W_CAP on a blank/garbage entry.
        # This is the BENIGN-downside ceiling; the q_low risk scaling in
        # _compute_recommendation can still pull the effective cap below it.
        if not self.max_weight_on_var.get():
            return 10.0
        try:
            return max(0.0, float(self.max_weight_var.get()) / 100.0)
        except (TypeError, ValueError, tk.TclError):
            return REC_W_CAP

    def _max_pos_units(self):
        # Hard cap on total |position| in units — None means uncapped.
        # Empty entry = None (primary sizing driven by weight controls).
        # Returns a positive float when set, or None when uncapped.
        try:
            v = self.max_position_var.get().strip()
            return float(v) if v else None
        except (TypeError, ValueError, tk.TclError):
            return None

    def _max_trade_units(self):
        # Hard cap on a single trade size in units — None means uncapped.
        # Same design as _max_pos_units: blank entry = None = let the recommendation
        # decide the qty (already capped by max_weight and the turnover gate).
        try:
            v = self.max_trade_var.get().strip()
            return float(v) if v else None
        except (TypeError, ValueError, tk.TclError):
            return None

    def _sync_risk_cap_entries(self):
        # Grey out a cap's entry when its checkbox is OFF (the value is irrelevant while the
        # cap is disabled), then recompute. Called by the two on/off checkboxes + at setup.
        self.mt_entry.config(state='normal' if self.min_turnover_on_var.get() else 'disabled')
        self.mw_entry.config(state='normal' if self.max_weight_on_var.get() else 'disabled')
        self._on_risk_cap_change()

    def _on_risk_cap_change(self):
        # Recompute + repaint the recommendation when a risk-cap entry/checkbox changes, so
        # the panel reflects the new min-turnover / max-weight immediately. Guarded:
        # _refresh_recommendation no-ops cleanly before the model is calibrated.
        try:
            self._refresh_recommendation()
        except Exception:
            pass

    # ========================================================================
    # RISK / RETURN ENGINE (plan §7.1, PRIORITY 1)
    # ========================================================================
    def _last_mark_price(self):
        # Best available "current price" for marking the book: the live last tick if
        # we have one, else the forming bar's close, else μ. Used by P&L marking and
        # the recommendation's r_hat denominator.
        # REPLAY OVERRIDE: while a historical replay is built/scrubbed, the mark is the
        # REPLAYED bar's close (_replay_mark), NOT any live tick that may linger from a
        # prior session — so P&L and the recommendation are evaluated at the point in
        # history we are scrubbed to, deterministically and independent of TWS state.
        if self._replay_mark is not None:
            return float(self._replay_mark)
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
        # First, find what the OU model forecasts the price to be H steps from now
        central = self.kalman.forecast(H)[-1]
        # Then, calculate what kind of return this wuld imply
        r_hat_h = central / p_now - 1.0
        # Worst/best-case returns from the H-step quantile prices (final step).
        # Note that by default:
        # Q_LOW_P = 0.05 -> the 5th percentile price path (pessimistic) -> q_low_ret is 
        # the return if we hit that low price.
        # Q_HIGH_P = 0.95 -> the 95th percentile price path (optimistic) -> q_high_ret is
        #  the return if we hit that high price.
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
            # floor: benign downside -> the full user max weight; near the floor -> ~0. The
            # ceiling is the user-editable Max weight % (default 3%), read live each call.
            frac = (q_low_ret - REC_QLOW_FLOOR) / (0.0 - REC_QLOW_FLOOR)
            w_cap = self._max_weight_frac() * max(0.0, min(1.0, frac))
            binding_cap = "none"

        # Map expected return -> RAW target weight (piecewise linear), BEFORE any cap or
        # turnover threshold. This is the unconstrained "where the signal points" weight.
        w_target = REC_ALPHA * r_hat_h
        # Apply the risk cap: clamp the raw target to ±w_cap. (w_rec gets the turnover gate
        # applied below, once we know w_current.)
        w_capped = max(-w_cap, min(w_cap, w_target))
        # Flag if the cap (not the raw signal) is what limited the size.
        if binding_cap == "none" and abs(w_target) > w_cap and w_cap > 0:
            binding_cap = "idio_floor"

        # ---- translate weight into a concrete order (§3c) ----
        # Account value: live NetLiq when trading live, else the sim cash + P&L base.
        # Live path: trust the broker's NetLiquidation, but only when we are NOT in
        # sim mode — in sim we must ignore the real account and use our own book.
        if self.ib.account_value is not None and not self.sim_mode_var.get():
            account_value = float(self.ib.account_value)
        else:
            # Sim (or no live value yet): reconstruct equity ourselves as the starting base
            # (today's IBKR NetLiq via _sim_base_equity, else sim cash) + realised P&L booked
            # so far + open-position mark-to-market at p_now.
            account_value = self._sim_base_equity() + self.sim_realised + self._sim_unrealised(p_now)
        # Guard: a zero/negative equity would blow up the weight divisions below;
        # fall back to raw cash so sizing stays finite and sane.
        if account_value <= 0:
            account_value = self.cash
        # Pick the position that matches the mode we are pricing against: the sim
        # book in sim mode, the real held position otherwise.
        cur_pos = self.sim_position if self.sim_mode_var.get() else self.position
        # Current exposure in money = shares held * current price.
        value_current = cur_pos * p_now
        # Current weight = exposure / equity. Guard the divide; flat book -> 0 weight.
        w_current = value_current / account_value if account_value else 0.0
        # ---- min-turnover threshold gate (§3a/§3c) ----
        # The RECOMMENDED weight is the capped target UNLESS the move it implies is too small
        # to be worth trading. If |w_capped - w_current| < threshold, suppress the trade
        # (recommend staying put, w_rec = w_current → zero trade) so noise doesn't churn the
        # book. We deliberately do NOT retag binding_cap here: that field reports RISK caps
        # only, and the turnover suppression is read off w_capped vs w_threshold instead (the
        # panel shows "Δ < min" with binding_cap still "none"). Keeping the two orthogonal is
        # what lets a trader tell a cap-bound zero from a turnover-suppressed zero at a glance.
        # The gate magnitude is the user-editable Min turnover % (default 0.25%), read live.
        w_threshold = self._min_turnover_frac()
        if abs(w_capped - w_current) < w_threshold:
            w_rec = w_current
        else:
            w_rec = w_capped
        # Target exposure in money = the RECOMMENDED weight * equity.
        value_target = w_rec * account_value
        # Dollar trade needed to move from where we are to the recommended exposure.
        value_delta = value_target - value_current
        # Same delta expressed as a weight move (for display / the ForecastView).
        w_delta = w_rec - w_current
        # Convert the dollar trade into a signed share quantity at the current price;
        # guard against a zero price. Sign carries through: + buy, - sell.
        qty = value_delta / p_now if p_now else 0.0
        # Label the trade direction, with a tiny epsilon so float dust reads as "flat".
        direction = "long" if qty > 1e-12 else ("short" if qty < -1e-12 else "flat")

        return ForecastView(
            r_hat_h=r_hat_h, q_low=q_low_ret, q_high=q_high_ret,
            central_price=central, mu_now=float(self.mu), p_now=p_now,
            direction=direction, qty=qty, w_current=w_current, w_target=w_target,
            w_capped=w_capped, w_rec=w_rec, w_delta=w_delta, value_current=value_current,
            value_target=value_target, value_delta=value_delta, binding_cap=binding_cap,
            # Enrichment (§7.5): carry the turnover threshold applied and the equity used for
            # sizing so the order rationale + state row can be stamped without recomputing.
            w_threshold=w_threshold, account_value=account_value,
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
        # ---- weight transition (two arrows) + cap + turnover verdict ----------------
        # 1) TWO-ARROW transition FIRST: current weight -> RAW target (w_target, α·r̂ BEFORE
        #    any cap/gate) -> the actual RECOMMENDED weight (w_rec, AFTER the risk caps AND
        #    the turnover gate). Showing the raw target as the middle stop makes the trimming
        #    visible: target and rec diverge exactly when a cap and/or the turnover gate cut
        #    the move down (a suppressed bar reads "…→target%→current%", so the wanted move is
        #    still on screen even though the recommended action is to stay put).
        trans = (f"w {fv.w_current*100:.2f}%→{fv.w_target*100:.2f}%→{fv.w_rec*100:.2f}%")
        # 2) Binding RISK cap NEXT: "none" unless idio_floor/factor/portfolio bound the size.
        #    Turnover is NOT reported here — it's the Δ-vs-min clause below.
        cap = f"cap {fv.binding_cap}"
        # 3) Weight delta vs the min-turnover gate LAST: the intended move the gate actually
        #    tested (w_capped − w_current) with the operator it used — "≥ min" means the move
        #    cleared the gate (trade fires), "< min" means it was suppressed (w_rec =
        #    w_current → zero trade).
        intended = fv.w_capped - fv.w_current
        op = "≥" if abs(intended) >= fv.w_threshold else "<"
        chg = f"Δ {intended*100:+.2f}% {op} min {fv.w_threshold*100:.2f}%"
        self.rec_cap_lbl.config(text=f"{trans}   {cap}   {chg}")

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
        signed_qty = fill.side * fill.qty          # +long add or short reduce / -short add or long reduce
        pos = self.sim_position
        avg = self.sim_avg_entry
        price = fill.price

        if pos == 0 or (pos > 0 and signed_qty > 0) or (pos < 0 and signed_qty < 0):
            # SAME DIRECTION (or opening from flat): position grows. New avg entry is the
            # volume-weighted average of the old open units and the new fill.
            new_pos = pos + signed_qty
            # if no previous position, then the vwap is just the fill price; 
            if pos == 0:
                avg = price
            # if the fill is in the same direction as the existing position, 
            # we calculate a new average entry price by taking a weighted average of the 
            # existing position and the new fill. The weights are based on the absolute 
            # number of units of the existing position and the new fill quantity. 
            # This ensures that the average entry price accurately reflects the 
            # combined position after the fill is applied.
            else:
                avg = (avg * abs(pos) + price * abs(signed_qty)) / abs(new_pos)
            self.sim_position = new_pos
            self.sim_avg_entry = avg
        else:
            # OPPOSITE DIRECTION: the fill reduces (and maybe flips) the position.
            closing = min(abs(signed_qty), abs(pos))   # can't close more than existing position
            # Realised P&L on the closed units: sign(pos)·(price − avg)·units.
            self.sim_realised += np.sign(pos) * (price - avg) * closing
            # Accumulate the closed units for this bar so the state row can report it, then reset at the next bar close.
            self._sim_closed_this_bar += closing
            if abs(signed_qty) <= abs(pos):
                # Pure reduction (no flip): avg entry price of the surviving units is unchanged.
                # since signed_qty is negative, adding it to pos reduces the position size
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
            # ---- recommendation block (§7.5): the rest of the ForecastView at this bar.
            # All NaN/"" when no recommendation has been computed yet (warm-up bars).
            "central_price": fv.central_price if fv is not None else np.nan,
            "mu_now": fv.mu_now if fv is not None else np.nan,
            # reference_price = the spot the weights/return were measured against (p_now).
            "reference_price": fv.p_now if fv is not None else np.nan,
            "direction": fv.direction if fv is not None else "",
            # signed recommended qty to reach the target weight (+buy / −sell).
            "signed_qty": fv.qty if fv is not None else np.nan,
            # previous/target/capped/recommended weights: target = RAW pre-cap (w_target),
            # capped = post-cap but PRE-turnover (w_capped), recommended = post-cap +
            # post-turnover (w_rec) — same split as the order rationale. weight_threshold =
            # the min-turnover gate applied at this bar (recommended stays = previous when the
            # capped move falls below it).
            "previous_weight": fv.w_current if fv is not None else np.nan,
            "target_weight": fv.w_target if fv is not None else np.nan,
            "capped_weight": fv.w_capped if fv is not None else np.nan,
            "recommended_weight": fv.w_rec if fv is not None else np.nan,
            "weight_change": fv.w_delta if fv is not None else np.nan,
            "weight_threshold": fv.w_threshold if fv is not None else np.nan,
            "previous_value": fv.value_current if fv is not None else np.nan,
            "target_value": fv.value_target if fv is not None else np.nan,
            "value_delta": fv.value_delta if fv is not None else np.nan,
            "binding_cap": fv.binding_cap if fv is not None else "",
            # ---- portfolio-level (§7.5): equity the weights were sized against, and the
            # whole-book P&L (realised + unrealised) — for the position-vs-portfolio check.
            # portfolio_value falls back to reconstructed equity (IBKR base + sim P&L) when
            # no fv exists yet.
            "portfolio_value": (fv.account_value if fv is not None
                                else self._sim_base_equity() + self.sim_realised + unreal),
            "portfolio_pnl": self.sim_realised + unreal,
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
        # Drop the order ledger too (§7.5) so a fresh run starts with no stale orders.
        self.orders = {}

    def _register_sim_order(self, oid, fill, ctx, role, realised_delta):
        # Build the dummy_orders.json-shaped Order for a SINGLE-FILL sim trade (§7.5) and
        # store it in self.orders. Called by SimExecutionBackend.execute right AFTER the
        # fill is booked, so the post-fill book (sim_position/realised/unrealised) is final.
        #   oid            — the order/ledger id (also the fill's order_id).
        #   fill           — the Fill just applied (side/qty/price/ts + forecast extras).
        #   ctx            — the ForecastView that justified the trade (None for manual clicks
        #                    with no live recommendation); supplies the rationale block.
        #   role           — "OPEN" or "CLOSE", classified by execute() from the pre-fill book.
        #   realised_delta — realised P&L this fill crystallised (0 for a pure open).
        # Read the current contract once so the order records WHAT was traded and WHERE.
        c = self.contract()
        # Human-facing action string; the signed side lives on the fill.
        action = "BUY" if fill.side > 0 else "SELL"
        # Rationale block — keyed EXACTLY as dummy_orders.json. NaN-safe: if no ForecastView
        # drove the trade (e.g. a manual click pre-calibration) leave the fields null.
        if ctx is not None:
            rationale = {
                "expected_return_h": ctx.r_hat_h,
                "q_low_h": ctx.q_low,
                "q_high_h": ctx.q_high,
                "previous_weight": ctx.w_current,
                # target = RAW pre-cap α·r̂; capped = after risk caps but BEFORE the turnover
                # gate; recommended = the weight actually aimed at AFTER caps AND the turnover
                # threshold; min_turnover_weight = the gate applied (recommended stays =
                # previous when |capped − previous| falls below it).
                "target_weight": ctx.w_target,
                "capped_weight": ctx.w_capped,
                "recommended_weight": ctx.w_rec,
                "weight_change": ctx.w_delta,
                "min_turnover_weight": ctx.w_threshold,
                "previous_value": ctx.value_current,
                "target_value": ctx.value_target,
                "reference_price": ctx.p_now,
                "signed_qty": ctx.qty,
            }
        else:
            rationale = {}
        # The single fill dict embedded in the Filled history event (dummy_orders.json shape).
        # commission is 0 in sim (no broker); closes[] is omitted because apply_fill does not
        # track lot-level closes — realised_total carries the P&L this fill crystallised.
        fill_rec = {
            "exec_id": f"sim-{oid}",
            "ts": _iso(fill.ts),
            "qty": fill.qty,
            "price": fill.price,
            "commission": 0.0,
            "role": role,
            "realised_total": realised_delta,
        }
        # Mark-to-fill unrealised on the post-fill book (what the order leaves open).
        unreal = self._sim_unrealised(fill.price)
        # Assemble + store the Order. 100% immediate fill: total_qty == qty_filled,
        # qty_outstanding == 0, history is Submitted→Filled at the same timestamp.
        self.orders[oid] = Order(
            order_id=oid,
            submitted_at=fill.ts,
            symbol=c.symbol, sec_type=c.secType, exchange=c.exchange,
            currency=c.currency, con_id=(getattr(c, "conId", 0) or None),
            action=action, order_type="MKT",
            total_qty=fill.qty, market_price_at_submit=fill.price,
            limit_price=None, tif="DAY", strategy_tag="ou_mean_revert_v1",
            rationale=rationale,
            state={
                "as_of": _iso(fill.ts),
                "status": "Filled",
                "qty_filled": fill.qty,
                "qty_outstanding": 0.0,
                # SIM_FILLED distinguishes a synthesized replay fill from a live lifecycle.
                "lifecycle": "SIM_FILLED",
                "realised_cum": self.sim_realised,
                "unrealised": unreal,
            },
            history=[
                # Submitted: the order entered the book (no fill yet).
                {
                    "ts": _iso(fill.ts), "event": "ORDER_UPDATE", "status": "Submitted",
                    "qty_filled": 0.0, "qty_outstanding": fill.qty,
                    "avg_fill_price": None, "fill": None,
                    "pnl": {"realised_cum": self.sim_realised - realised_delta,
                            "unrealised": 0.0, "mark_price": None},
                },
                # Filled: the single fill completes the order in one shot.
                {
                    "ts": _iso(fill.ts), "event": "ORDER_UPDATE", "status": "Filled",
                    "qty_filled": fill.qty, "qty_outstanding": 0.0,
                    "avg_fill_price": fill.price, "fill": fill_rec,
                    "pnl": {"realised_cum": self.sim_realised,
                            "unrealised": unreal, "mark_price": fill.price},
                },
            ],
        )

    def export_orders_json(self, path=None):
        # Dump the order ledger to JSON in the dummy_orders.json shape: {str(order_id): {...}}.
        # Default target sits next to kts.py so a replay run is inspectable / exportable for
        # the static web view (§6). Returns the path written (or None if there is nothing).
        if not self.orders:
            return None
        path = path or (pathlib.Path(__file__).parent / "replay_orders.json")
        payload = {str(oid): o.to_dict() for oid, o in self.orders.items()}
        with open(path, "w", encoding="utf-8") as f:
            # default=str so any stray datetime/Decimal serializes cleanly.
            json.dump(payload, f, indent=2, default=str)
        return path

    def export_state_json(self, path=None):
        # Dump the per-bar state_df to JSON, one record per bar (the position/P&L +
        # recommendation + portfolio columns of §7.5). Complements the orders ledger:
        # orders = trades-over-time, this = position/portfolio-over-time. Returns the path.
        if self.state_df.empty:
            return None
        path = path or (pathlib.Path(__file__).parent / "replay_state.json")
        # reset_index() promotes the bar-timestamp index into a "ts" column so each record
        # is self-describing; orient="records" gives a flat list the web view can read.
        out = self.state_df.reset_index().rename(columns={"index": "ts"})
        out.to_json(path, orient="records", date_format="iso", indent=2)
        return path

    def _export_replay_json(self, quiet=False):
        # Convenience used by the Export JSON button (quiet=False) + _build_replay
        # (quiet=True): write both files and report where they landed. The button pops a
        # messagebox; the auto-export at build end only logs (no popup per build).
        op = self.export_orders_json()
        sp = self.export_state_json()
        if op is None and sp is None:
            if not quiet:
                messagebox.showinfo("Export", "Nothing to export — build a replay or place a sim trade first.")
            return
        # Show just the filenames (full paths are long); both sit in orders/.
        names = ", ".join(p.name for p in (op, sp) if p is not None)
        dlog("replay_export", orders=str(op), state=str(sp))
        if not quiet:
            messagebox.showinfo("Export", f"Exported {names} → orders/")

    def _export_replay_plotly(self):
        # ============================================================================
        # PLOTLY REPLAY EXPORT (plan §F / plan.md item 11, step 1). Render-once.
        # ADDITIVE — does NOT touch the in-app matplotlib replay or the scrubber. Renders
        # the per-bar state_df ONCE to a self-contained interactive HTML (price + P&L +
        # Units + Entry, shared x, bottom range slider) and opens it in the browser. Native
        # Plotly gives zoom / pan / legend-toggle / unified hover / WebGL-sharp traces for
        # free — the deep-zoom companion to the live Tk chart. Tk-thread (button command).
        # ============================================================================
        # Lazy import so the app still runs without plotly installed; guide the user if not.
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
        except ImportError:
            messagebox.showerror(
                "Plotly export",
                "plotly is not installed.\nInstall it with:  pip install plotly")
            return
        import webbrowser
        # Prefer the full precomputed replay frame; fall back to whatever sim state exists.
        df = (self._replay_state_df
              if (self._replay_state_df is not None and not self._replay_state_df.empty)
              else self.state_df)
        if df is None or df.empty:
            messagebox.showinfo(
                "Plotly export",
                "Nothing to export — build a replay or place a sim trade first.")
            return
        # x-axis = bar timestamps (df index) -> a real time axis + range slider in Plotly.
        x = list(df.index)

        # Four stacked rows, shared x, price dominant — mirrors the in-app pane order.
        fig = make_subplots(
            rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03,
            row_heights=[0.4, 0.2, 0.2, 0.2],
            subplot_titles=("Price", "P&L", "Units", "Entry vs market"))

        # ---- Row 1: price. OHLC candlesticks when the replay bar source lines up with the
        # frame count, else the always-present last_price line. ----
        bars = (self._replay_bars
                if (self._replay_bars and len(self._replay_bars) == len(df)) else None)
        if bars:
            fig.add_trace(go.Candlestick(
                x=x, open=[b['o'] for b in bars], high=[b['h'] for b in bars],
                low=[b['l'] for b in bars], close=[b['c'] for b in bars], name="OHLC"),
                row=1, col=1)
        else:
            fig.add_trace(go.Scattergl(x=x, y=df["last_price"].to_list(),
                                       mode="lines", name="last"), row=1, col=1)

        # ---- Row 2: P&L decomposition (Scattergl = WebGL, stays sharp on deep zoom). ----
        fig.add_trace(go.Scattergl(x=x, y=df["realised_pnl"].to_list(),
                                   mode="lines", name="realised"), row=2, col=1)
        fig.add_trace(go.Scattergl(x=x, y=df["unrealised_pnl"].to_list(),
                                   mode="lines", name="unrealised"), row=2, col=1)
        fig.add_trace(go.Scattergl(x=x, y=df["total_pnl"].to_list(),
                                   mode="lines", name="total"), row=2, col=1)

        # ---- Row 3: position breakdown + closure markers. ----
        fig.add_trace(go.Scattergl(x=x, y=df["filled"].to_list(),
                                   mode="lines", name="filled"), row=3, col=1)
        fig.add_trace(go.Scattergl(x=x, y=df["intended"].to_list(),
                                   mode="lines", name="intended"), row=3, col=1)
        fig.add_trace(go.Scattergl(x=x, y=df["unfilled"].to_list(),
                                   mode="lines", name="unfilled"), row=3, col=1)
        # Only the non-zero closures, so the marker row isn't littered with zeros.
        closed = df["closed_units"].to_list()
        cx = [x[i] for i, v in enumerate(closed) if v]
        cy = [v for v in closed if v]
        if cx:
            fig.add_trace(go.Scattergl(x=cx, y=cy, mode="markers", name="closed"),
                          row=3, col=1)

        # ---- Row 4: avg entry (masked to None when flat) + last. ----
        entry = [e if p != 0 else None
                 for e, p in zip(df["avg_entry_price"].to_list(),
                                 df["position"].to_list())]
        fig.add_trace(go.Scattergl(x=x, y=entry, mode="lines", name="avg entry"),
                      row=4, col=1)
        fig.add_trace(go.Scattergl(x=x, y=df["last_price"].to_list(),
                                   mode="lines", name="last (entry)"), row=4, col=1)

        # Dark theme to match the app; single range slider on the bottom axis (xaxis4).
        # Candlestick auto-adds its own slider on xaxis — disable it so only the shared
        # bottom one shows. Unified hover = one box listing every series at that x.
        fig.update_layout(template="plotly_dark", height=900,
                          title="Replay — interactive (zoom / legend-toggle / hover)",
                          hovermode="x unified",
                          xaxis4=dict(rangeslider=dict(visible=True)))
        if bars:
            fig.update_layout(xaxis=dict(rangeslider=dict(visible=False)))

        # Write next to kts.py (like the JSON exports) and open in the default browser.
        path = pathlib.Path(__file__).parent / "replay_chart.html"
        fig.write_html(str(path), include_plotlyjs="cdn")
        webbrowser.open(path.as_uri())
        dlog("plotly_export", path=str(path))
        messagebox.showinfo(
            "Plotly export",
            f"Wrote {path.name} → orders/ and opened it in your browser.")

    # ========================================================================
    # HISTORICAL REPLAY SCRUBBER (plan §7 build-step 7).
    # "Compute once, scrub freely." _build_replay runs the entire OU/Kalman/accounting
    # pipeline over a long bar history ONE time and snapshots per-bar state; the slider
    # then reconstructs any point in O(slice) with no model recomputation. Three methods:
    #   _fetch_replay_bars — pull + normalize the historical OHLC series from IBKR.
    #   _build_replay      — the one-off heavy pass: calibrate, march bar-by-bar, snapshot.
    #   _replay_to         — display the precomputed state AS OF a chosen bar index.
    # plus the scrub throttle (_on_replay_scrub/_flush_replay_scrub) and _exit_replay.
    # ========================================================================
    def _fetch_replay_bars(self, num_bars):
        # Pull `num_bars` of historical OHLC for the current contract and normalize them
        # into the {t,o,h,l,c} dicts the rest of kts speaks. Mirrors refresh_30m's pull
        # path (same duration math, whatToShow/useRTH-by-secType rules, and timestamp
        # parsing) but returns the list instead of calibrating — the replay builder owns
        # the calibration. Returns [] on any failure (caller surfaces the message).
        # A connection is required: historical data comes from TWS just like the live path.
        if not self.connected:
            messagebox.showerror("Replay", "Connect to TWS first — replay pulls historical bars.")
            return []
        # Bar size drives both the OU step Δt and IB's bar_size string; also set the
        # bar-close cadence so any later live stream stays consistent with the replay.
        bar_sec, bar_size_setting = self._bar_size_to_sec_and_ib()
        self._bar_sec = bar_sec
        # Translate "N bars of size X" into IB's durationStr. Three tiers:
        #   < 86400 s  → seconds form  ("X S")   — intraday windows
        #   1–365 days → day form      ("X D")   — multi-day windows
        #   > 365 days → year form     ("X Y")   — IB error 1780 if you send "X D"
        #                                          for spans > 365 days; must use years.
        total_sec = num_bars * bar_sec
        total_days = total_sec // 86400
        if total_sec < 86400:
            duration_str = f"{max(60, total_sec)} S"
        elif total_days <= 365:
            duration_str = f"{max(1, total_days)} D"
        else:
            # Round up to whole years so IB doesn't reject a fractional value;
            # ceil division: (days + 364) // 365 avoids importing math.ceil.
            duration_str = f"{max(1, (total_days + 364) // 365)} Y"
        # Opt into the selected market-data tier (harmless for historical; keeps a later
        # live stream in lockstep, and lets no-live-sub symbols like BTC still pull bars).
        self.ib.reqMarketDataType(self._mkt_data_type_code())
        # whatToShow/useRTH by secType: TRADES+RTH for equities; MIDPOINT+24h for FX/crypto
        # (which have no "trade" prints, so TRADES would return zero bars).
        sectype = self.sectype_var.get().strip().upper()
        if sectype in ("CASH", "CRYPTO"):
            what_to_show, use_rth = "MIDPOINT", 0
        else:
            what_to_show, use_rth = "TRADES", 1
        # Blocking pull via the shared library helper (allocates its own reqId + Event,
        # waits for historicalDataEnd or a timeout). Longer timeout than calibration
        # because a replay window can be far larger than a calib window.
        bars = get_historical_bars(
            self.ib, self.contract(),
            duration=duration_str, bar_size=bar_size_setting,
            what_to_show=what_to_show, use_rth=use_rth, timeout=30,
        )
        if not bars:
            messagebox.showwarning("Replay", "No historical bars received. Check symbol / window / market hours.")
            return []
        # Normalize: parse the IB timestamp ("YYYYMMDD HH:MM:SS [tz]") and coerce OHLC to
        # float, dropping any garbage rows (same parsing refresh_30m uses).
        bar_list = []
        for b in bars:
            try:
                parts = b['datetime'].split()
                t = datetime.strptime(f"{parts[0]} {parts[1]}", '%Y%m%d %H:%M:%S')
            except Exception:
                # Skip unparseable rows entirely in replay (a wrong now()-stamp would
                # corrupt the time axis of a long history, unlike a single live bar).
                continue
            try:
                c = float(b['close'])
            except (TypeError, ValueError):
                continue
            if not np.isfinite(c) or c <= 0:
                continue
            bar_list.append({'t': t, 'o': float(b['open']), 'h': float(b['high']),
                             'l': float(b['low']), 'c': c})
        return bar_list

    def _build_replay(self):
        # ============================================================================
        # THE ONE-OFF HEAVY PASS. Pull a long history, calibrate on the first W bars,
        # then march bar-by-bar feeding each close through the SAME model + signal +
        # accounting the live path uses — recording one state_df row and one model
        # snapshot per bar. After this returns, every scrub is pure slicing. Runs on the
        # Tk main thread (blocks while pulling bars) — acceptable for a manual button.
        # ============================================================================
        # 0) Refuse to build while a live stream is running: the build loop sets
        #    _replay_mark every bar, and a concurrent on_tick (reader thread) marking the
        #    book would interleave with it. Ask the user to stop the stream first so the
        #    replay is computed against a quiet, deterministic book.
        if self.streaming:
            messagebox.showinfo("Replay", "Stop the live stream before building a replay.")
            return

        # 1) How many bars to pull (free-text entry; floor at 20 so AR(1) + a warm-up
        #    window have something to chew on).
        try:
            num = max(20, int(self.replay_bars_var.get()))
        except (TypeError, ValueError):
            num = 500
        bar_list = self._fetch_replay_bars(num)
        N = len(bar_list)
        if N < 20:
            messagebox.showwarning("Replay", "Need at least 20 valid bars to build a replay.")
            return

        # 2) Warm-up / calibration window W = the calib-window control, clamped below N so
        #    there is at least one tradable bar after it. estimate_ar1 needs >=5.
        W = min(self._get_calib_window(), N - 1)
        if W < 5:
            W = 5
        # Observation-noise scale from the trust lever (same mapping the live filter uses).
        scale = noise_lever_to_scale(self.noise_lever_var.get())
        # Calibrate the initial OU/Kalman on the first W bars. _recalibrate_from_bars also
        # writes self.phi/mu/sigma and returns the filter replayed over those W bars, so
        # flat_kprices already holds the orange Kalman-mean dot for each warm-up bar.
        init = self._recalibrate_from_bars(bar_list[:W], scale)
        if init is None:
            messagebox.showwarning("Replay", "Could not calibrate AR(1) on the warm-up window.")
            return
        self.phi, self.mu, self.sigma = init[0], init[1], init[2]
        self.kalman = init[3]
        flat_kprices = list(init[4])          # one Kalman mean per warm-up bar (len W)

        # 3) Fresh ledger for the run (wipes sim position/P&L + state_df + order counter).
        self._reset_accounting()
        # Mark replay active up front so _last_mark_price routes to _replay_mark for every
        # _record_state_row / _compute_recommendation below (never to a stale live tick).
        self._replay_active = True

        # Decision inputs read ONCE (constant across the run): band multiplier k and
        # horizon H (same values the live signal and the on-screen cone use). Sizing now
        # comes from _compute_recommendation (via fv.qty) rather than a fixed step_qty, so
        # the replay respects min_turnover, max_weight, and the optional unit caps exactly
        # as the live auto-trade does — no divergence between replay and live behaviour.
        k = float(self.band_mult_var.get())
        try:
            H = max(1, int(self.forecast_horizon_var.get()))
        except (TypeError, ValueError):
            H = 5
        # Optional hard unit caps (may be None = uncapped). Read once for the whole run.
        replay_max_pos = self._max_pos_units()
        replay_max_trade = self._max_trade_units()
        # Whether trades are simulated during the build. Auto-trade toggle gates it exactly
        # like the live path — off = a pure "what did the model say" replay with no fills.
        trade_on = self.auto_trade_var.get()

        # Per-bar model snapshots {phi,mu,sigma,x}; one entry per bar so a scrub to index i
        # restores the exact bands/forecast/live-mean that existed at bar i.
        frames = []

        # 4a) WARM-UP BARS (0..W-1): no trading (model only just calibrated). We still
        #     record a flat state row + a frame so the slider can scrub into the warm-up
        #     region and show price history with a zero book.
        for i in range(W):
            b = bar_list[i]
            self._replay_mark = b['c']           # mark at this bar's close
            self._last_forecast_view = None      # no recommendation context pre-trade
            self._record_state_row(b['t'], b['c'])
            frames.append({'phi': self.phi, 'mu': self.mu, 'sigma': self.sigma,
                           'x': flat_kprices[i]})

        # 4b) TRADABLE BARS (W..N-1): advance the filter, (optionally) recalibrate, compute
        #     the recommendation, fire the band signal through the sim backend, then mark.
        for i in range(W, N):
            b = bar_list[i]
            close = b['c']
            ts = b['t']
            self._replay_mark = close            # everything below marks at this close

            # Advance the OU model one bar. Online mode rebuilds the filter from scratch
            # over bars 0..i (mirrors on_tick CASE A, which also REPLACES kalman_prices);
            # frozen mode just .update()s and appends one mean. flat_kprices ends length N.
            if self.online_params_var.get():
                res = self._recalibrate_from_bars(bar_list[:i + 1], scale)
                if res is not None:
                    self.phi, self.mu, self.sigma = res[0], res[1], res[2]
                    self.kalman = res[3]
                    flat_kprices = list(res[4])
                else:
                    self.kalman.update(close)
                    flat_kprices.append(self.kalman.x)
            else:
                self.kalman.update(close)
                flat_kprices.append(self.kalman.x)

            # Recommendation context at this bar (pre-trade) — stamped into the fill and
            # the state row so the ledger remembers WHY each trade happened.
            self._last_forecast_view = self._compute_recommendation()

            # Band-signal decision via the SHARED helper (identical to live auto-trade).
            # Sizing comes from _compute_recommendation (fv.qty), NOT a fixed step_qty,
            # so the replay respects all weight controls just like the live path.
            if trade_on:
                side, _u, _l, _fc = self._ou_signal_side(
                    close, self.kalman, self.mu, self.sigma, k, H)
                fv = self._last_forecast_view
                if side is not None and fv is not None and fv.qty != 0.0:
                    # fv.qty is the recommendation-sized signed qty; we take the magnitude
                    # and check sign consistency with the OU band direction.
                    fill_side = 1 if fv.qty > 0 else -1
                    if fill_side == side:   # direction must agree with band signal
                        trade_qty = abs(fv.qty)
                        # Optional per-fire cap (secondary guard; weight controls primary).
                        if replay_max_trade is not None:
                            trade_qty = min(trade_qty, replay_max_trade)
                        # Optional total-position cap: clip to remaining room rather than skip.
                        if replay_max_pos is not None:
                            remaining = max(0.0, replay_max_pos - abs(self.sim_position))
                            trade_qty = min(trade_qty, remaining)
                        if trade_qty > 0:
                            # Synthesize the fill at this bar's close and book it through
                            # apply_fill (the sole mutator) — no network, pure replay.
                            self.exec_backend.execute(side, trade_qty, close, ts,
                                                      ctx=fv)

            # Mark the book at this bar's close (post-trade position) -> one state_df row.
            self._record_state_row(ts, close)
            # Snapshot the model as it stands at bar i (x is the live Kalman mean now).
            frames.append({'phi': self.phi, 'mu': self.mu, 'sigma': self.sigma,
                           'x': self.kalman.x})

        # 5) Stash the precomputed product. From here, scrubbing only SLICES these.
        self._replay_bars = bar_list
        self._replay_kalman_prices = flat_kprices
        self._replay_state_df = self.state_df.copy()
        self._replay_frames = frames

        # 6) Arm the slider over [0, N-1], park it at the end, and render that final state.
        self.replay_slider.config(state='normal', from_=0, to=N - 1)
        self.replay_slider_var.set(N - 1)
        self._replay_to(N - 1)
        # Persist the run (§7.5): orders ledger → replay_orders.json, per-bar state →
        # replay_state.json. Quiet (log only) so a build doesn't spawn a popup.
        self._export_replay_json(quiet=True)
        dlog("replay_built", bars=N, warmup=W, trades_on=trade_on,
             online=self.online_params_var.get())

    def _on_replay_scrub(self, value):
        # ttk.Scale command — fires continuously while the user drags. We THROTTLE: redraw
        # immediately if enough time has elapsed since the last draw, otherwise remember
        # the target index and flush it via root.after so a fast drag doesn't queue one
        # full (candle) redraw per pixel. The handle keeps moving smoothly either way.
        if not self._replay_active:
            return
        # ttk passes the value as a string float; snap to the nearest bar index.
        self._replay_cursor_pending = int(round(float(value)))
        now = time.time()
        if now - self._last_replay_draw >= 0.04:
            # Far enough from the last draw -> render this position right now.
            self._last_replay_draw = now
            self._replay_to(self._replay_cursor_pending)
        elif not self._replay_draw_scheduled:
            # Too soon -> schedule a single trailing flush ~40ms out (coalesces a burst
            # of drag events into one redraw at the final resting position).
            self._replay_draw_scheduled = True
            self.root.after(40, self._flush_replay_scrub)

    def _flush_replay_scrub(self):
        # Trailing-edge redraw for the throttle above: render whatever index the drag last
        # left pending, then clear the scheduled flag so the next burst can re-arm.
        self._replay_draw_scheduled = False
        self._last_replay_draw = time.time()
        if self._replay_active:
            self._replay_to(self._replay_cursor_pending)

    def _jump_to_bar(self):
        # Scrub to an EXACT 1-based bar number typed in the Go-to-bar box (the slider is
        # coarse for precise targets). No-op unless a replay is active; out-of-range / garbage
        # input is clamped to [1, N] so a typo can't crash the scrub.
        if not self._replay_active or not self._replay_frames:
            return
        N = len(self._replay_frames)
        try:
            one_based = int(float(self.replay_jump_var.get()))
        except (TypeError, ValueError, tk.TclError):
            return
        # User types 1-based; the internal cursor + slider var are 0-based.
        i = max(0, min(N - 1, one_based - 1))
        self.replay_slider_var.set(i)   # keep the slider handle in sync
        self._replay_to(i)

    def _jump_prev_bar(self):
        # Step back one bar from the current scrub position. Updates the jump entry box
        # so the displayed bar number always matches the slider. No-op when at bar 1.
        if not self._replay_active or not self._replay_frames:
            return
        N = len(self._replay_frames)
        # _replay_cursor tracks the current 0-based index; step back by 1.
        cur = getattr(self, '_replay_cursor', N - 1)
        i = max(0, cur - 1)
        self.replay_jump_var.set(str(i + 1))   # keep jump box in sync (1-based)
        self.replay_slider_var.set(i)
        self._replay_to(i)

    def _jump_next_bar(self):
        # Step forward one bar from the current scrub position. Updates the jump entry box
        # so the displayed bar number always matches the slider. No-op when at final bar.
        if not self._replay_active or not self._replay_frames:
            return
        N = len(self._replay_frames)
        cur = getattr(self, '_replay_cursor', 0)
        i = min(N - 1, cur + 1)
        self.replay_jump_var.set(str(i + 1))   # keep jump box in sync (1-based)
        self.replay_slider_var.set(i)
        self._replay_to(i)

    def _replay_to(self, index):
        # ============================================================================
        # RECONSTRUCT + RENDER the chart AS OF bar `index` from the precomputed replay.
        # Pure slicing + a redraw — NO model recomputation. Restores: the bar history,
        # the Kalman-mean dots, the model snapshot (phi/mu/sigma/x) for bands + forecast,
        # the sim ledger (position/avg/realised) for the widgets + recommendation, and the
        # state_df slice the analytics plots read. Safe to call only after _build_replay.
        # ============================================================================
        if not self._replay_active or not self._replay_frames:
            return
        N = len(self._replay_frames)
        # Clamp the requested index into range (defensive against slider edge values).
        i = max(0, min(N - 1, int(index)))
        self._replay_cursor = i
        fr = self._replay_frames[i]

        # 1) Restore the model snapshot for this bar. phi/mu/sigma drive the bands + the
        #    quantile cone; we rebuild a KalmanOU and pin its mean x so forecast()/the
        #    green live-mean line read exactly the value they had at bar i. (Q/R don't
        #    affect any drawing, so the trust-lever scale here is cosmetic.)
        self.phi, self.mu, self.sigma = fr['phi'], fr['mu'], fr['sigma']
        scale = noise_lever_to_scale(self.noise_lever_var.get())
        self.kalman = KalmanOU(self.phi, self.mu, self.sigma, obs_noise_scale=scale)
        self.kalman.x = fr['x']

        # 2) Bar history + Kalman dots up to i. ohlc_bars becomes an UNBOUNDED deque so a
        #    long replay isn't truncated by the live maxlen; current_bar None means no
        #    half-formed candle is drawn (the bar at i is closed).
        self.ohlc_bars = deque(self._replay_bars[:i + 1])
        self.current_bar = None
        self.bar_start = None
        self.kalman_prices = self._replay_kalman_prices[:i + 1]
        self.forecast_prices = self.kalman.forecast(5)
        # 3) Mark at this bar's close so _compute_recommendation / portfolio value evaluate
        #    at the scrubbed point in history.
        self._replay_mark = self._replay_bars[i]['c']

        # 4) Restore the sim ledger from the recorded row so the top widgets and the
        #    recommendation reflect the book we held at bar i. Guard the row lookup in case
        #    duplicate timestamps collapsed a few rows during recording.
        df = self._replay_state_df
        if df is not None and len(df) > 0:
            ri = min(i, len(df) - 1)
            row = df.iloc[ri]
            self.sim_position = float(row['position'])
            self.sim_avg_entry = float(row['avg_entry_price'])
            self.sim_realised = float(row['realised_pnl'])
            # The analytics panes plot rows 0..i (P&L / position / entry history so far).
            self.state_df = df.iloc[:ri + 1].copy()

        # 5) Recompute the recommendation panel for this point and repaint everything.
        self._last_forecast_view = self._compute_recommendation()
        self._update_recommendation_panel(self._last_forecast_view)
        self._update_ou_labels()
        self.update_portfolio_display()
        # Cursor readout: 1-based bar number + its timestamp, plus the price label.
        ts = self._replay_bars[i]['t']
        self.replay_pos_lbl.config(
            text=f"bar {i + 1}/{N}  {ts:%Y-%m-%d %H:%M}", fg='#58a6ff')
        self.price_lbl.config(text=f"Last: {self._replay_bars[i]['c']:.6g}")
        self.redraw_chart()

    def _exit_replay(self):
        # Leave replay mode: drop the mark override, disarm the slider, and forget we were
        # scrubbing. Called before going live (toggle_stream) or clearing the chart so a
        # stale _replay_mark can never hijack live marking. The precomputed arrays are left
        # in place (cheap) so a user can re-arm without rebuilding — they're overwritten on
        # the next _build_replay anyway.
        self._replay_active = False
        self._replay_mark = None
        if hasattr(self, 'replay_slider'):
            self.replay_slider.config(state='disabled')
        if hasattr(self, 'replay_pos_lbl'):
            self.replay_pos_lbl.config(text="— build a replay to scrub —", fg='#8b949e')

    def redraw_chart(self):
        # ORCHESTRATOR (plan §7.2). Clears every existing axis, then delegates to one
        # _draw_* helper per pane so each concern (price / P&L / position / entry) is
        # isolated and individually testable. Each analytics axis is touched only when it
        # exists (its toggle is on) and there is state_df data to plot.
        self.ax.clear()
        self._style_ax(self.ax)
        # Clear + restyle whichever analytics axes currently exist (any subset, per §A).
        for ax in (self.ax_pnl, self.ax_pos, self.ax_entry):
            if ax is not None:
                ax.clear()
                self._style_ax(ax)
        # Reattach hover tooltips — ax.clear() removed them — one per existing axis (§D).
        self._hover_annots = {
            a: self._make_hover_annot(a)
            for a in (self.ax, self.ax_pnl, self.ax_pos, self.ax_entry) if a is not None
        }
        # PRICE PANE — the original chart, now in its own helper.
        self._draw_price()
        # ANALYTICS PANES — draw each only when its axis is live.
        if self.ax_pnl is not None:
            self._draw_pnl()
        if self.ax_pos is not None:
            self._draw_positions()
        if self.ax_entry is not None:
            self._draw_entry_vs_market()
        # Single x-label on the bottom-most axis (the rest hide their tick labels, §A).
        if getattr(self, '_bottom_ax', None) is not None:
            self._bottom_ax.set_xlabel("Bar index", color='#8b949e')
        self.canvas.draw_idle()

    def _draw_price(self):
        # PRICE PANE (extracted verbatim from the original redraw_chart, with the
        # ±1σ forecast cone swapped for explicit quantile bands per §7.2/§1d). Draws
        # candles, the close line, the Kalman history dots, the live mean / μ / bands,
        # and the OU forecast with worst/best-case quantile shading.
        has_current = self.current_bar is not None and (self.ohlc_bars or self.streaming)
        n = len(self.ohlc_bars)
        # Reset price-axis hover overlays each draw; populated as the elements below are
        # plotted so _on_hover can offer per-element templates (bands / forecast / kalman).
        self._price_levels = {}        # horizontal refs: mu / upper / lower / live_mean
        self._kalman_hist_xy = []      # [(x, value)] for the orange Kalman-mean dots
        self._forecast_overlay = None  # {'x0','central','low','high'} when forecast drawn
        # Throttled snapshot log: at most once per 500ms.
        now_t = time.time()
        if DEBUG_LOG and (now_t - getattr(self, '_last_log_time', 0.0)) >= 0.5:
            self._last_log_time = now_t
            last_b = self.ohlc_bars[-1] if self.ohlc_bars else None
            kx = self.kalman.x if self.kalman is not None else None
            dlog("redraw", n=n, last_bar=last_b, cur_bar=self.current_bar,
                 kx=kx, mu=self.mu, sigma=self.sigma)
        if not self.ohlc_bars and not has_current:
            # x-label is applied centrally by redraw_chart on the bottom-most axis (§A).
            self.ax.set_ylabel("Price", color='#c9d1d9')
            return
        if self.ohlc_bars:
            t = [b['t'] for b in self.ohlc_bars]
            o = [b['o'] for b in self.ohlc_bars]
            h = [b['h'] for b in self.ohlc_bars]
            l = [b['l'] for b in self.ohlc_bars]
            c = [b['c'] for b in self.ohlc_bars]
            # PERFORMANCE GUARD (replay scrubber, plan §7 step 7): one Rectangle per
            # candle is fine for a live chart (~120 bars) but crawls when a precomputed
            # replay holds thousands. Above REPLAY_CANDLE_LIMIT we skip the per-candle
            # bodies/wicks and let the close LINE below carry the whole price path, so
            # dragging the scrubber over a long history stays smooth. Also user-toggleable
            # via the "candles" Price box (off => the close line carries the price path).
            draw_candles = self.show_candles_var.get() and len(t) <= REPLAY_CANDLE_LIMIT
            if draw_candles:
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
            # Close line: faint companion to the candles when they're drawn, but the
            # PRIMARY price trace (brighter/thicker) when candles are suppressed for speed.
            # Toggleable via the Price group; candles themselves stay on.
            if self.show_close_var.get():
                close_alpha, close_lw = (0.5, 0.8) if draw_candles else (0.95, 1.2)
                # White (not blue) so the close line never reads as a band/forecast trace.
                self.ax.plot(range(len(t)), c, color='#f0f6fc', alpha=close_alpha, linewidth=close_lw, label='Close')
        if has_current and self.show_candles_var.get():
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
        if self.show_kalman_hist_var.get() and self.kalman_prices and n > 0:
            k_len = min(len(self.kalman_prices), n)
            idx = list(range(n - k_len, n))
            vals = self.kalman_prices[-k_len:]
            self.ax.scatter(idx, vals, color='#f0883e', s=18, zorder=5, label='Kalman mean (history)')
            self._kalman_hist_xy = list(zip(idx, vals))   # for the kalman/mean hover template

        # ---- GREEN HORIZONTAL LINE: the LIVE Kalman mean (self.kalman.x). ----
        # This is what the user came for: the current mean-level estimate as of
        # the latest tick. on_tick mutates self.kalman.x via kalman.update(price);
        # the deferred redraw then reads it here. Spans the full x-axis as a
        # horizontal because "the OU level right now" has no x-coordinate — it's
        # a scalar that we're claiming is fair value at this instant.
        if self.show_live_mean_var.get() and self.kalman is not None and np.isfinite(self.kalman.x):
            self.ax.axhline(y=self.kalman.x, color='#7ee787', linestyle='-', linewidth=2, alpha=0.95, zorder=4, label='Live mean level (Kalman x)')
            self._price_levels['live_mean'] = float(self.kalman.x)

        # ---- GREY DASHED LINE: long-run OU mean μ from the calibration window. ----
        # Frozen unless online recalibration is enabled. Useful visual reference
        # for how far the live KF mean has drifted from the calibration anchor.
        if self.show_longmean_var.get() and self.mu is not None and np.isfinite(self.mu):
            self.ax.axhline(y=self.mu, color='#8b949e', linestyle='--', linewidth=1, alpha=0.7, zorder=3, label='Long-run mean (μ)')
            self._price_levels['mu'] = float(self.mu)

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
            self._price_levels['upper'] = float(upper)
            self._price_levels['lower'] = float(lower)

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
            # in the below plot, fc_x is the x-axis for the forecast points
            #  (starts at n_draw, the next bar after the history).
            # and continues for H steps, so the last forecast point is at n_draw + H - 1.
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
                # Stash for the forecast hover template (per-step central + P5/P95).
                self._forecast_overlay = {'x0': fc_x[0], 'central': list(mean_path),
                                          'low': list(lower_fc), 'high': list(upper_fc)}
        elif self.forecast_prices:
            fc_x = list(range(n_draw, n_draw + len(self.forecast_prices)))
            self.ax.scatter(fc_x, self.forecast_prices, color='#a371f7', s=22, zorder=5, label='OU forecast')
        # ---- TRADE MARKERS: ▲ buy / ▼ sell at each fill, with hover rationale. ----
        self._draw_trade_markers(n)
        self.ax.set_ylabel("Price", color='#c9d1d9')
        # x-label is applied centrally by redraw_chart on the bottom-most axis (§A), so the
        # price pane only labels its y-axis here.
        handles, labels = self.ax.get_legend_handles_labels()
        if handles:
            self.ax.legend(loc='upper left', fontsize=8)

    def _draw_trade_markers(self, n):
        # Plot one marker per executed order on the price axis: ▲ (BUY) / ▼ (SELL) at the
        # fill price, positioned at the order's bar index. Solid blue = filled (a hollow
        # marker is reserved for a future unfilled/partial state). Also caches a hit-list
        # (self._trade_markers) so _on_hover can show a per-trade tooltip. Honors the
        # "markers" Price-pane toggle and only draws markers within the currently displayed
        # bar range — during replay state_df is sliced to 0..cursor, so future trades hide.
        self._trade_markers = []
        if not self.show_markers_var.get() or not self.orders or self.state_df.empty:
            return
        # Map each displayed bar timestamp -> its 0-based x position, so an order's
        # submitted_at lands on the same integer axis the candles use.
        pos_of_ts = {ts: i for i, ts in enumerate(self.state_df.index)}
        buy_x, buy_y, sell_x, sell_y = [], [], [], []
        for o in self.orders.values():
            x = pos_of_ts.get(o.submitted_at)
            if x is None or not (0 <= x < n):
                continue
            price = o.market_price_at_submit
            if price is None or not np.isfinite(price):
                continue
            is_buy = (o.action == "BUY")
            (buy_x if is_buy else sell_x).append(x)
            (buy_y if is_buy else sell_y).append(price)
            # Cache enough to render the trade header + the rationale block on hover.
            self._trade_markers.append({
                "x": x, "price": price, "side": o.action,
                "qty": o.total_qty, "rationale": o.rationale,
            })
        # One scatter per side, solid blue, drawn above the price line (zorder 7).
        if buy_x:
            self.ax.scatter(buy_x, buy_y, marker='^', s=70, facecolor='#1f6feb',
                            edgecolor='#58a6ff', linewidth=0.8, zorder=7, label='Buy')
        if sell_x:
            self.ax.scatter(sell_x, sell_y, marker='v', s=70, facecolor='#1f6feb',
                            edgecolor='#58a6ff', linewidth=0.8, zorder=7, label='Sell')

    def _trade_marker_at(self, event, px_tol=12.0):
        # Return the cached trade marker whose plotted point is within px_tol PIXELS of the
        # cursor (nearest wins), or None. Lets the price-axis hover show a trade tooltip when
        # the cursor sits on a ▲/▼ marker, falling back to the OHLC box otherwise.
        markers = getattr(self, "_trade_markers", None)
        if not markers or event.x is None or event.y is None:
            return None
        best, best_d2 = None, px_tol * px_tol
        for mk in markers:
            try:
                px, py = self.ax.transData.transform((mk["x"], mk["price"]))
            except Exception:
                continue
            d2 = (px - event.x) ** 2 + (py - event.y) ** 2
            if d2 <= best_d2:
                best, best_d2 = mk, d2
        return best

    def _price_hover_special(self, event, px_tol=8.0):
        # Per-element price-axis hover, checked in priority order. Returns (text, (x, y)) for
        # the OU-forecast band, the μ/upper/lower band cluster, or the kalman-mean cluster, or
        # None to fall back to the OHLC box. Driven by the overlays cached in _draw_price.
        if event.xdata is None or event.x is None:
            return None
        # 1) FORECAST: cursor inside the projected x-region -> central + P5/P95 at that step.
        # note that self._forecast_overlay is a dict object which holds x, central, high and low forecast estimates
        # only populated when the forecast is drawn, so this check implicitly
        fo = getattr(self, '_forecast_overlay', None)
        if fo is not None:
            # first, we check if the index lies among the indices for which we should have a forecast
            k = int(round(event.xdata)) - fo['x0']
            if 0 <= k < len(fo['central']):
                txt = (f"OU forecast\ncentral {fo['central'][k]:.6g}\n"
                       f"P{int(Q_HIGH_P*100)} {fo['high'][k]:.6g}\n"
                       f"P{int(Q_LOW_P*100)} {fo['low'][k]:.6g}")
                return txt, (fo['x0'] + k, fo['central'][k])
        lv = getattr(self, '_price_levels', {})
        # y-pixel of a price level (x is irrelevant for a horizontal line on a linear axis).
        def _py(yval):
            return self.ax.transData.transform((event.xdata, yval))[1]
        # 2) BAND CLUSTER: μ / upper / lower — show all three when near any of them.
        band_keys = [kk for kk in ('mu', 'upper', 'lower') if kk in lv]
        if band_keys:
            near = min(band_keys, key=lambda kk: abs(_py(lv[kk]) - event.y))
            if abs(_py(lv[near]) - event.y) <= px_tol:
                def g(kk):
                    return f"{lv[kk]:.6g}" if kk in lv else "—"
                txt = f"bands\nμ {g('mu')}\nupper {g('upper')}\nlower {g('lower')}"
                return txt, (int(round(event.xdata)), lv[near])
        # 3) KALMAN CLUSTER: the live-mean line OR a history dot -> kalman hist + live mean.
        cand, cand_d = None, px_tol
        if 'live_mean' in lv:
            d = abs(_py(lv['live_mean']) - event.y)
            if d <= cand_d:
                cand, cand_d = ('live_mean', None, lv['live_mean']), d
        for (kx, kv) in getattr(self, '_kalman_hist_xy', []):
            px, py = self.ax.transData.transform((kx, kv))
            d = ((px - event.x) ** 2 + (py - event.y) ** 2) ** 0.5
            if d <= cand_d:
                cand, cand_d = ('dot', kx, kv), d
        if cand is not None:
            khist = f"{cand[2]:.6g}" if cand[0] == 'dot' else "—"
            live = f"{lv['live_mean']:.6g}" if 'live_mean' in lv else "—"
            anchor = (cand[1] if cand[0] == 'dot' else int(round(event.xdata)), cand[2])
            return f"kalman mean\nhistory {khist}\nlive mean {live}", anchor
        return None

    def _hover_text_trade(self, mk):
        # Build the trade-marker tooltip: a header (BOUGHT/SOLD), the size @ fill price, then
        # — when the "rationale" toggle is on — the same recommendation fields the panel shows
        # (the box is colour/bold-styled green/red by side in _on_hover). Rationale values are
        # stored as fractions in the order ledger, shown here as percentages.
        head = "BOUGHT" if mk["side"] == "BUY" else "SOLD"
        lines = [head, f"{abs(mk['qty']):.4g} @ {mk['price']:.6g}"]
        r = mk.get("rationale") or {}
        if self.show_marker_rationale_var.get() and r:
            def pct(key):
                v = r.get(key)
                return (f"{v*100:+.2f}%"
                        if isinstance(v, (int, float)) and np.isfinite(v) else "—")
            lines.append(f"E[r] {pct('expected_return_h')}  "
                         f"lo {pct('q_low_h')}  hi {pct('q_high_h')}")
            lines.append(f"w {pct('previous_weight')}→{pct('target_weight')}"
                         f"→{pct('recommended_weight')}")
            lines.append(f"Δ {pct('weight_change')} vs min {pct('min_turnover_weight')}")
        return "\n".join(lines)

    def _state_xy(self, column):
        # Helper: return (x_indices, y_values) for one state_df column, with x = the
        # row's integer position so it lines up bar-for-bar with the price candles
        # (which are also drawn at integer indices). Empty frame -> empty lists.
        if self.state_df.empty or column not in self.state_df.columns:
            return [], []
        y = self.state_df[column].to_list()
        return list(range(len(y))), y

    def _draw_pnl(self):
        # P&L DECOMPOSITION PANE (§4c plot 1), drawn as a STACKED area so total reads as the
        # sum of its parts: realised filled off zero (green base = crystallised), unrealised
        # stacked ON TOP of realised (lighter green = not yet realised, so its band height is
        # the open mark-to-market), and total as a thick semi-transparent WHITE line riding
        # the stack top (realised+unrealised) — the tug-of-war reference. Same frame the top
        # widget summarises, so the number on screen and the curve never disagree.
        if self.state_df.empty:
            self.ax_pnl.set_ylabel("P&L", color='#c9d1d9', fontsize=8)
            return
        x, _ = self._state_xy("total_pnl")
        realised = self.state_df["realised_pnl"].to_list()
        total = self.state_df["total_pnl"].to_list()
        # realised: the crystallised base of the stack (filled from zero).
        if self.show_realised_var.get():
            self.ax_pnl.fill_between(x, 0, realised, color="#0eca30", alpha=0.55,
                                     linewidth=0.4, label='realised')
        # unrealised: stacked ON realised (base=realised, top=total) so the band height equals
        # the open-position P&L; lighter green signals "not yet realised".
        if self.show_unrealised_var.get():
            self.ax_pnl.fill_between(x, realised, total, color="#f3a005", alpha=0.40,
                                     linewidth=0.4, label='unrealised')
        # total: thick, semi-transparent white line on the stack top.
        if self.show_total_var.get():
            self.ax_pnl.plot(x, total, color='#f0f6fc', linewidth=2.2, alpha=0.7, label='total')
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
        # next_intended: the TOTAL position the recommendation would hold AFTER its
        # recommended trade = target_value / reference_price (recommended $ exposure ÷ the
        # price it was sized at). Distinct from `intended` (the band-signal book) — the gap
        # is what the recommendation wanted but the executed strategy didn't put on. NaN where
        # there's no price/target (warm-up bars) so the line just breaks there.
        if (self.show_next_intended_var.get()
                and "target_value" in self.state_df.columns
                and "reference_price" in self.state_df.columns):
            tv = self.state_df["target_value"].to_list()
            rp = self.state_df["reference_price"].to_list()
            next_int = [(t / p) if (p and np.isfinite(p) and np.isfinite(t)) else np.nan
                        for t, p in zip(tv, rp)]
            self.ax_pos.plot(x, next_int, color='#a371f7', linewidth=1.0,
                             linestyle=':', label='next int.')
        # unfilled is now its OWN toggle (plan §B): it used to ride on `intended`, but the
        # user wants every series independently switchable.
        if self.show_unfilled_var.get():
            self.ax_pos.plot(x, self.state_df["unfilled"].to_list(),
                             color='#f85149', linewidth=0.8, alpha=0.7, label='unfilled')
        # closed_units markers: only where a closure happened (non-zero), so the chart
        # isn't littered with zeros. Size scaled by magnitude for quick visual weight.
        # Gated by its own toggle (plan §B); previously always drawn.
        if self.show_closed_var.get():
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
            # x-label is applied centrally by redraw_chart on the bottom-most axis (§A).
            self.ax_entry.set_ylabel("Entry/mkt", color='#c9d1d9', fontsize=8)
            return
        x, _ = self._state_xy("last_price")
        if self.show_entry_var.get():
            # avg_entry_price is 0 when flat; mask those so the line doesn't dive to 0.
            entry = [e if p != 0 else np.nan
                     for e, p in zip(self.state_df["avg_entry_price"].to_list(),
                                     self.state_df["position"].to_list())]
            self.ax_entry.plot(x, entry, color='#d29922', linewidth=1.2, label='avg entry')
        # `last` is now its own toggle (plan §B); previously always drawn.
        if self.show_last_var.get():
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
        # x-label is applied centrally by redraw_chart on the bottom-most axis (§A).
        self.ax_entry.set_ylabel("Entry/mkt", color='#c9d1d9', fontsize=8)
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
