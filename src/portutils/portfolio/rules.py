"""
REBALANCE RULES — the policies that decide what to trade at each bar of a scenario.

A rule is the offline analogue of what ``_compute_recommendation`` does inside kts.py:
given the current bar and the current book, say what to trade. Rules deliberately return
SIGNED DELTA UNITS (not target weights, not target units), because that is exactly what an
execution backend consumes — every rule does its own target→delta arithmetic internally,
where it has the context to do it correctly.

Two rules ship here, and they compose in one simulator run:
  * ``TradeListRule``       — a hand-specified list of dated trades ("do this, then this").
  * ``DrawdownRotationRule``— the policy behind the motivating question: hold a hedge
    sleeve, cut it into a drawdown, rotate the proceeds into a high-beta basket to ride
    the recovery, then rotate back.

Rules are stateful across bars (a drawdown policy must remember the equity peak), so each
one implements ``reset()`` and the simulator calls it before a run.
"""

import numpy as np


def _price(prices, symbol):
    """Look up one symbol's mark for ONE bar.

    ``prices`` : ``dict[str, float]`` — a SINGLE bar's cross-section, not a time series.
        The simulator builds it per row (``{sym: float(px) for sym, px in row.items()}``,
        see ``simulator.Simulator.run``), so it is a plain dict of length ≤ n_symbols:
        symbols with no quote on this bar are ABSENT (NaNs are dropped there, not here).
        A ``pd.Series`` indexed by symbol would also work — ``.get`` behaves the same — but
        that is never what the simulator hands in.
    ``symbol`` : ``str``.

    Returns ``float`` or ``None``; None for a missing or non-finite quote so callers can
    skip the leg rather than trade on a bad number.
    """
    px = prices.get(symbol)
    if px is None:
        return None
    px = float(px)
    return px if np.isfinite(px) and px > 0 else None


def weights_to_units(weights, prices, capital):
    """Convert {symbol: weight} into {symbol: units} at ONE bar's prices.

    ``weights`` : ``dict[str, float]`` — one entry per symbol in the basket, so its length
        is n_symbols and NOT n_bars. It is a static allocation target (set once in a rule's
        ``__init__``), re-applied at whatever bar the rule fires; there is no time axis in
        it. Weights are fractions of ``capital``, so they need not sum to 1 — a sleeve sized
        at 0.4 of equity simply leaves the rest uninvested.
    ``prices``  : ``dict[str, float]`` for THIS bar only — see ``_price``.
    ``capital`` : ``float`` — the money to size against (starting capital, live equity, or
        the cash proceeds of a sale, depending on the caller).

    Returns ``dict[str, float]`` of units, keyed by symbol, with unpriced or zero-weight
    legs omitted — so it can be shorter than ``weights``.
    """
    units = {}
    for sym, w in weights.items():
        px = _price(prices, sym)
        if px is None or w == 0:
            continue
        units[sym] = (float(w) * float(capital)) / px
    return units


class RebalanceRule:
    """Base class. Subclasses implement ``propose`` and, if stateful, ``reset``."""
    # note that the word stateful means "the rule has memory across bars" 
    # rather than "the rule has mutable (i.e., non-constant) attributes" 

    def reset(self):
        # Clear any cross-bar state. Called once by the simulator before the first bar so
        # a rule instance can be reused across runs without leaking the previous run's
        # peak equity / rotation flags.
        pass

    def propose(self, ts, prices, book):
        # Return {symbol: signed delta units to trade at this bar}. Empty dict = do nothing.
        # `prices` is a {symbol: price} mapping for THIS bar; `book` is the live Book, so a
        # rule can read current units and mark-to-market equity before deciding.
        raise NotImplementedError


class TradeListRule(RebalanceRule):
    """Replay a hand-specified trade list — the fully manual half of the harness.

    ``trades`` is anything with ``ts``, ``symbol``, ``side`` and ``qty`` columns (a
    DataFrame or a list of dicts). ``side`` is +1/-1 or "BUY"/"SELL"; ``qty`` is absolute.
    Trades are matched to bars by exact timestamp, so the timestamps must come from the
    same index as the price frame.
    """

    def __init__(self, trades):
        # Bucket by timestamp once at construction; propose() is then a dict lookup per bar
        # rather than a scan of the whole list.
        self._by_ts = {}
        rows = trades.to_dict("records") if hasattr(trades, "to_dict") else list(trades)
        for r in rows:
            side = r["side"]
            # Accept human-facing strings as well as ±1 so a hand-written CSV is usable
            # without a conversion step.
            if isinstance(side, str):
                side = 1 if side.strip().upper() in ("BUY", "B", "+1") else -1
            self._by_ts.setdefault(r["ts"], []).append((r["symbol"], int(side) * abs(float(r["qty"]))))

    def propose(self, ts, prices, book):
        out = {}
        for sym, signed in self._by_ts.get(ts, []):
            # Accumulate rather than overwrite: two entries for the same symbol on the same
            # bar are two trades, and their net is what actually hits the book.
            out[sym] = out.get(sym, 0.0) + signed
        return out


class BuyAndHoldRule(RebalanceRule):
    """Open a fixed weighted basket on the first bar and never trade again — the baseline
    every rotation policy has to beat. Without this comparison a scenario's P&L is a number
    with nothing to mean anything against."""

    def __init__(self, weights, capital=None):
        self.weights = dict(weights)
        # Capital to size against. None → the book's base_equity at the first bar, which is
        # the usual case (the sim is told its starting capital once, at book construction).
        self.capital = capital
        self._done = False

    def reset(self):
        self._done = False

    def propose(self, ts, prices, book):
        if self._done:
            return {}
        self._done = True
        capital = self.capital if self.capital is not None else book.base_equity
        # Fresh book → target units ARE the deltas.
        return weights_to_units(self.weights, prices, capital)


class ConstantMixRule(RebalanceRule):
    """Rebalance back to fixed target weights — the policy that generates realised P&L
    continuously, with no threshold and no discretion.

    This is what `PanelBuilder.attribution` implicitly assumes whenever it is handed a
    CONSTANT weight vector: `contrib = weights * rets` holds the weights fixed on every
    bar, which is only true if you trade back to them on every bar. That returns-based
    model cannot express the consequence — it has no units and no cost basis — so the
    realised P&L and the turnover it implies are invisible there and visible here.

    Why it is mechanically contrarian: targets are sized off **current equity**, so a leg
    that rallied is now above its target weight and its delta comes out NEGATIVE (sell the
    winner), while a leg that fell is below target and its delta is positive (buy the
    loser). Applied to a hedge sleeve against a beta sleeve, that means the hedge is sold
    into its rally — crystallising gains — and the proceeds buy the beta leg at its lows.
    No drawdown trigger required; the arithmetic does it every bar.
    """

    def __init__(self, weights, every=1, tolerance=0.0, min_trade_frac=1e-6, capital=None):
        self.weights = dict(weights)
        # Rebalance cadence in BARS. 1 = every bar (the daily constant-mix this study runs,
        # and the cadence `attribution` implicitly assumes). Higher values give calendar-ish
        # rebalancing without needing a different rule.
        self.every = max(1, int(every))
        # Optional drift band: skip a leg whose weight is already within `tolerance` of its
        # target (0.02 = "don't trade until 2 percentage points off"). 0.0 = always trade,
        # which is what reproduces the constant-weight assumption exactly.
        self.tolerance = float(tolerance)
        # Dust filter as a fraction of equity. Float arithmetic on target-vs-current units
        # leaves 1e-15 residuals every bar; without this the blotter fills with meaningless
        # trades and the turnover figure becomes noise.
        self.min_trade_frac = float(min_trade_frac)
        # Capital to size the FIRST allocation against. None → the book's base_equity.
        # After that, sizing always follows live equity — that is the "mix" in constant-mix.
        self.capital = capital
        self.reset()

    def reset(self):
        # Bar counter driving the `every` cadence. Starts at -1 so the first bar seen
        # increments it to 0 and rebalances immediately (0 % every == 0), establishing the
        # opening position rather than waiting a full period to get invested.
        self._bar = -1

    def propose(self, ts, prices, book):
        self._bar += 1
        # Off-cadence bars do nothing at all — no fills, no turnover, no ledger churn.
        if self._bar % self.every != 0:
            return {}

        # Size against CURRENT equity (base capital + realised + unrealised), not the
        # starting capital. This is the whole mechanism: as the book grows the targets grow
        # with it, and as one leg outruns another the rebalance trades the gap away.
        equity = book.equity(prices)
        if self._bar == 0 and self.capital is not None:
            # First bar only: an explicit capital override, for a study that wants to deploy
            # a different amount than the book's base equity.
            equity = float(self.capital)
        if equity <= 0:
            # A wiped-out book has no target to rebalance to; trading here would be
            # nonsense (and would divide a negative equity across the weights).
            return {}

        deltas = {}
        for sym, w in self.weights.items():
            px = _price(prices, sym)
            if px is None:
                # No mark for this leg today — skip it rather than invent a fill price.
                continue
            current = book.position(sym).qty
            target = (float(w) * equity) / px
            delta = target - current

            # Drift band: how far this leg's ACTUAL weight sits from its target, in weight
            # terms. Checked before the dust filter because it is the economically
            # meaningful gate; the dust filter below is only about float noise.
            if self.tolerance > 0:
                actual_w = (current * px) / equity
                if abs(actual_w - float(w)) < self.tolerance:
                    continue
            # Dust filter: ignore trades worth less than min_trade_frac of the book.
            if abs(delta * px) < self.min_trade_frac * equity:
                continue
            deltas[sym] = delta
        return deltas


class DrawdownRotationRule(RebalanceRule):
    """Cut the hedge sleeve into a drawdown, rotate into high beta, rotate back on recovery.

    The policy, in the order the checks fire each bar:

      1. **Bar 0 — establish the sleeve.** Buy ``sleeve_weights`` sized off starting equity.
      2. **Drawdown trigger.** Track peak equity. When the book's drawdown from that peak
         first exceeds ``dd_trigger``, sell ``cut_frac`` of the CURRENT sleeve units and buy
         the proceeds into ``growth_weights`` (renormalised so the proceeds are fully spent).
         The sale is what CRYSTALLISES P&L — this is the moment realised jumps and
         unrealised shrinks, which is the whole point of the study.
      3. **Recovery trigger.** Once rotated, track the trough. When the book has recovered
         ``recovery_frac`` of the peak-to-trough loss, sell the growth leg and buy the sleeve
         back with the proceeds. Rotating back crystallises the high-beta leg's P&L in turn.

    Both triggers can fire at most ``max_rotations`` times, so a choppy tape does not
    generate an endless churn of round trips.
    """

    def __init__(self, sleeve_weights, growth_weights, dd_trigger=0.10, cut_frac=0.5,
                 recovery_frac=0.5, max_rotations=1, capital=None):
        self.sleeve_weights = dict(sleeve_weights)
        self.growth_weights = dict(growth_weights)
        # Drawdown from the running equity peak that triggers the cut, as a POSITIVE
        # fraction (0.10 = a 10% drawdown).
        self.dd_trigger = float(dd_trigger)
        # Fraction of the sleeve's current units to sell when the trigger fires.
        self.cut_frac = float(cut_frac)
        # Fraction of the peak-to-trough loss that must be recovered before rotating back.
        self.recovery_frac = float(recovery_frac)
        self.max_rotations = int(max_rotations)
        self.capital = capital
        self.reset()

    def reset(self):
        self._opened = False        # has the initial sleeve been established?
        self._rotated = False       # are we currently sitting in the growth basket?
        self._peak = None           # running equity high-water mark
        self._trough = None         # lowest equity seen since rotating
        self._rotations = 0         # completed cut→rotate-back round trips
        # Audit trail: (ts, event, equity, drawdown) for every trigger that fired. The
        # scenario report reads this to annotate the equity chart — a P&L path without the
        # decision points marked on it is very hard to argue with.
        self.events = []

    # -- helpers ---------------------------------------------------------------
    def _sell_all_units(self, symbols, book, prices):
        # Deltas that flatten every named leg at this bar, plus the cash those sales raise.
        deltas, proceeds = {}, 0.0
        for sym in symbols:
            qty = book.position(sym).qty
            px = _price(prices, sym)
            if qty == 0 or px is None:
                continue
            deltas[sym] = -qty
            proceeds += qty * px
        return deltas, proceeds

    def _buy_with(self, weights, proceeds, prices):
        # Spend `proceeds` across `weights`, renormalised so the full amount is deployed.
        # Renormalising matters: the growth basket's weights describe RELATIVE sizing within
        # the basket, not a fraction of total equity, so 0.6/0.4 must spend 100% of the cash.
        total_w = sum(abs(w) for w in weights.values())
        if total_w <= 0 or proceeds <= 0:
            return {}
        return weights_to_units({s: w / total_w for s, w in weights.items()}, prices, proceeds)

    @staticmethod
    def _merge(a, b):
        # Combine two delta dicts (sell leg + buy leg) into the single set of trades this
        # bar will execute.
        out = dict(a)
        for k, v in b.items():
            out[k] = out.get(k, 0.0) + v
        return out

    # -- the policy ------------------------------------------------------------
    def propose(self, ts, prices, book):
        equity = book.equity(prices)

        # ---- step 1: establish the sleeve on the first bar we see. ----
        if not self._opened:
            self._opened = True
            self._peak = equity
            capital = self.capital if self.capital is not None else book.base_equity
            self.events.append((ts, "OPEN_SLEEVE", equity, 0.0))
            return weights_to_units(self.sleeve_weights, prices, capital)

        # Peak tracking drives the drawdown measure; it only ever ratchets up.
        self._peak = max(self._peak, equity)
        drawdown = 0.0 if self._peak <= 0 else (self._peak - equity) / self._peak

        # ---- step 2: drawdown deep enough, and we still hold the sleeve → rotate out. ----
        if (not self._rotated and drawdown >= self.dd_trigger
                and self._rotations < self.max_rotations):
            deltas, proceeds = {}, 0.0
            for sym in self.sleeve_weights:
                qty = book.position(sym).qty
                px = _price(prices, sym)
                if qty == 0 or px is None:
                    continue
                # Sell a FRACTION of the current units — a partial cut, not a liquidation,
                # so the sleeve keeps working on whatever it still holds.
                sell = qty * self.cut_frac
                deltas[sym] = -sell
                proceeds += sell * px
            buys = self._buy_with(self.growth_weights, proceeds, prices)
            if not deltas or not buys:
                # Nothing sellable or nothing buyable at this bar — do not half-execute a
                # rotation, or the book ends up in cash by accident.
                return {}
            self._rotated = True
            self._trough = equity
            self.events.append((ts, "ROTATE_TO_GROWTH", equity, drawdown))
            return self._merge(deltas, buys)

        # ---- step 3: rotated and recovering → rotate back into the sleeve. ----
        if self._rotated:
            self._trough = min(self._trough, equity)
            loss = self._peak - self._trough
            # Recovery measured as the fraction of the peak-to-trough loss clawed back;
            # a zero-loss denominator means we never really fell, so treat it as recovered.
            recovered = 1.0 if loss <= 0 else (equity - self._trough) / loss
            if recovered >= self.recovery_frac:
                sells, proceeds = self._sell_all_units(self.growth_weights, book, prices)
                buys = self._buy_with(self.sleeve_weights, proceeds, prices)
                if not sells or not buys:
                    return {}
                self._rotated = False
                self._rotations += 1
                self.events.append((ts, "ROTATE_TO_SLEEVE", equity, drawdown))
                return self._merge(sells, buys)

        return {}
