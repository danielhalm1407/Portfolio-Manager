"""
OPTION RULES — protective put, put spread and rolling collar as simulator rules.

WHAT THIS MODULE IS
-------------------
12-01's three costed hedge structures, carried across by 16-03 with their economics verbatim:
European pricing with no clamp (``pricing.py``), a bar-counted 63-bar roll (``schedule.py``),
strike-dependent IV from the v1 synthetic surface, floor 0.90 / spread 0.80 / cap 1.28, and the
collar's settle -> redeploy / fund branch from ``bse.py:4196-4218``.

HOW A RULE MEETS THE SIMULATOR
------------------------------
The rules are UNIT-NATIVE (CONTEXT.md friction 1): there is no weight form of "long one 0.90x
put", so ``propose`` returns signed contract units directly and never goes near
``weights_to_units``. Each bar the simulator:

1. calls ``propose`` — on a roll bar the rule closes the expiring legs and strikes new ones off
   the current spot, so the new legs' symbols come into existence HERE;
2. calls ``synthetic_marks`` — the rule prices every leg it holds, is closing or is opening, so
   the simulator can fill and value instruments the price panel has no column for;
3. fills, then marks the Book.

SIZING — one option per unit of underlying (decided 2026-09-19)
--------------------------------------------------------------
Each leg is sized at ``hedge_ratio`` (default 1.0) contracts per unit of the underlying the Book
holds, with no 100x multiplier. Values therefore stay in the underlying's price units and read
directly against the probe. 12-01's notional / 150%-overhedge question stays open for Phase 13.

WHAT IS NOT HERE YET
--------------------
Option Fills carry no rationale: 02-02's rule -> ctx -> Order channel has not landed. Every rule
keeps its own ``events`` list (bar, ts, action, symbol, qty, price, reason) instead, which is what
the probe figure reads; projecting it into ``Order.rationale`` is a later plan's job.

v1 SURFACE, SO EVERY PAYOFF IS A FLOOR
--------------------------------------
``base`` is frozen at 0.16 and each leg's moneyness reference is the spot it was struck at. Vol
never rises into a selloff, so a long put's crash value is understated — worst on the deep-OTM
strikes (probe finding 6). Conservative for the v0.4 claim, never generous.
"""

import numpy as np

from portutils.portfolio.rules import RebalanceRule

from ..instruments.options import OptionLeg
from ..instruments.vol import synthetic_iv_surface
from ..schedule import RollCalendar


class OptionOverlayRule(RebalanceRule):
    """Shared roll / mark / log machinery. Subclasses only declare their legs."""

    def __init__(self, underlying="SPY", reset_bars=63, rate=0.02, div_yield=0.0,
                 base_vol=0.16, vol_fn=None, hedge_ratio=1.0):
        # The ticker the legs are written on AND the Book position they hedge. 12-01 leaves
        # "hedge one symbol, hold another" (crash.py's basis-risk setup) open; here they are one.
        self.underlying = underlying
        # Bar-counted cadence. The roll fires reset_bars after the period started, never on a date.
        self.calendar = RollCalendar(reset_bars)
        # Carried onto every OptionLeg so each leg prices itself with the same carry.
        self.rate = float(rate)
        self.div_yield = float(div_yield)
        # v1 ATM vol level. Only used by the default vol_fn below.
        self.base_vol = float(base_vol)
        # vol_fn(strike, tau, spot_ref) -> implied vol AT THAT STRIKE. 12-01's constraint: vol is
        # a function of strike, never a scalar per bar — a flat vol would erase the put spread's
        # entire rationale (selling the dearer lower strike into the skew).
        self.vol_fn = vol_fn if vol_fn is not None else self._v1_vol
        # Contracts per unit of underlying held (1.0 = one option per share-unit).
        self.hedge_ratio = float(hedge_ratio)
        self.reset()

    # ---- state -----------------------------------------------------------------

    def reset(self):
        # Bar counter: the simulator hands us bars one at a time and never an index, so the rule
        # counts them itself. -1 so the first propose() lands on bar 0.
        self._bar = -1
        # Open legs: dicts of {leg, qty (signed), spot_ref, premium}. spot_ref is the spot the leg
        # was struck at — the v1 moneyness reference, fixed for the leg's whole life.
        self._legs = []
        # Legs being closed / opened on THIS bar, with their fill prices, so synthetic_marks can
        # price them without re-deciding anything. Cleared at the top of every propose().
        self._closing = []
        self._opening = []
        # Bar and spot the current period started on. None until the first legs are opened.
        self._period_start_bar = None
        self._period_start_spot = None
        # The rationale record, until 02-02's channel exists. One dict per leg action.
        self.events = []
        # Per-bar value of the option position, for figures: {bar: {...}}. Keyed by bar so a
        # repeated synthetic_marks call on the same bar overwrites rather than duplicates.
        self.history = {}

    # ---- pricing ---------------------------------------------------------------

    def _v1_vol(self, strike, tau, spot_ref):
        # v1 surface exactly as the probe uses it: frozen base, moneyness against the strike-time
        # spot. Never pass the CURRENT spot as spot_ref here — that is term B, and term B alone
        # makes the hedge look cheaper in a selloff (the 2026-08-29 decision).
        return float(synthetic_iv_surface(strike, tau, spot_ref, base=self.base_vol))

    def _price(self, entry, spot):
        # Mark one open leg at this bar's spot. On (or after) its expiry bar the leg is worth its
        # exercise value exactly — the pricer's degenerate branch gives the same number, but
        # stating it keeps settlement explicit rather than an accident of tau hitting zero.
        leg = entry["leg"]
        if leg.is_expired(self._bar):
            return leg.intrinsic(spot)
        tau = leg.tau(self._bar)
        return float(leg.price(spot, self.vol_fn(leg.strike, tau, entry["spot_ref"]), tau))

    # ---- structure-specific ----------------------------------------------------

    def _leg_spec(self):
        # [(right, strike_multiple_of_spot, sign)] — sign +1 long, -1 short. Per subclass.
        raise NotImplementedError

    def _on_roll(self, spot, closed_value, opened_premium, units, book, deltas):
        # Hook between closing the old legs and opening the new ones. Only the collar uses it.
        pass

    # ---- the simulator interface -----------------------------------------------

    def _held_units(self, book):
        # Units of the underlying currently held. Read without book.position(), which would
        # auto-create an empty Position (and a spurious ledger column) for a never-traded symbol.
        return book.position(self.underlying).qty if self.underlying in book.symbols else 0.0

    def _log(self, ts, action, symbol, qty, price, reason, **extra):
        # One flat record per action — the shape the probe figure's hover text reads.
        self.events.append({"bar": self._bar, "ts": ts, "action": action, "symbol": symbol,
                            "qty": qty, "price": price, "reason": reason, **extra})

    def propose(self, ts, prices, book):
        # Every bar, even ones where nothing trades — the bar count must never skip.
        self._bar += 1
        # Remembered so hooks called from inside propose (the collar's _on_roll) can stamp events.
        self._ts = ts
        self._closing, self._opening = [], []
        spot = prices.get(self.underlying)
        # Gate 1: no spot, no decision. Never strike or settle off a missing mark.
        if spot is None or not np.isfinite(spot) or spot <= 0:
            return {}
        held = self._held_units(book)

        # Gate 2: is a roll due? The FIRST open happens on the first bar the Book holds the
        # underlying (bar 1 when a BuyAndHoldRule buys on bar 0 — the same bar would size off
        # zero units). Every roll after that is the calendar's call.
        if self._period_start_bar is None:
            if held <= 0:
                return {}
        elif not self.calendar.is_roll_day(self._bar, self._period_start_bar):
            return {}

        deltas = {}
        # --- 1. Close the expiring legs at their settlement value. ------------------------
        closed_value, units_closed = 0.0, 0.0
        for entry in self._legs:
            px = self._price(entry, spot)
            leg, qty = entry["leg"], entry["qty"]
            # Buy back a short, sell a long: the closing delta is the negative of the position.
            deltas[leg.symbol] = deltas.get(leg.symbol, 0.0) - qty
            self._closing.append((leg, px, qty))
            # Net settlement per unit hedged: long legs add, short legs subtract.
            closed_value += np.sign(qty) * px
            units_closed = max(units_closed, abs(qty))
            self._log(ts, "close", leg.symbol, -qty, px, "roll", strike=leg.strike,
                      premium=entry["premium"], pnl_per_unit=np.sign(qty) * (px - entry["premium"]))
        self._legs = []

        # --- 2. Strike the replacement legs off the CURRENT spot. ---------------------------
        # Sized off the units held BEFORE any collar redeploy this bar — the redeploy is a
        # consequence of the settlement, and sizing the hedge off it would make the two circular.
        units = self.hedge_ratio * held
        new_legs, opened_premium = [], 0.0
        for right, mult, sign in self._leg_spec():
            leg = OptionLeg(self.underlying, right, mult * spot, open_bar=self._bar,
                            expiry_bar=self.calendar.next_roll(self._bar),
                            rate=self.rate, div_yield=self.div_yield)
            entry = {"leg": leg, "qty": sign * units, "spot_ref": spot, "premium": 0.0}
            entry["premium"] = self._price(entry, spot)
            opened_premium += sign * entry["premium"]
            new_legs.append(entry)

        # --- 3. Structure-specific settlement (collar redeploy / funding). ----------------
        if units_closed > 0:
            self._on_roll(spot, closed_value, opened_premium, units_closed, book, deltas)

        # --- 4. Open. ----------------------------------------------------------------------
        for entry in new_legs:
            leg = entry["leg"]
            deltas[leg.symbol] = deltas.get(leg.symbol, 0.0) + entry["qty"]
            self._opening.append((leg, entry["premium"]))
            self._log(ts, "open", leg.symbol, entry["qty"], entry["premium"], "roll",
                      strike=leg.strike, premium=entry["premium"], pnl_per_unit=0.0)
        self._legs = new_legs
        self._period_start_bar, self._period_start_spot = self._bar, spot
        return deltas

    def synthetic_marks(self, ts, prices, book):
        # Price everything this rule touches on this bar: the legs just closed (at their
        # settlement value), the legs just opened (at their premium) and — on the 62 bars of a
        # period where nothing trades — every open leg at its model value. An open leg with no
        # mark would be valued at ZERO unrealised by the Book, silently, so none is ever skipped.
        spot = prices.get(self.underlying)
        if spot is None:
            return {}
        marks = {leg.symbol: px for leg, px, _ in self._closing}
        marks.update({leg.symbol: px for leg, px in self._opening})
        for entry in self._legs:
            marks.setdefault(entry["leg"].symbol, self._price(entry, spot))

        # Record the position's value for the figures. Everything is PER UNIT HEDGED and NET of
        # long/short: a put spread's value is the long put minus the short put.
        if self._legs:
            self.history[self._bar] = {
                "bar": self._bar,
                "ts": self._ts,
                "period_start_bar": self._period_start_bar,
                # What the current legs are worth now, and what they cost when struck.
                "net_value": sum(np.sign(e["qty"]) * marks[e["leg"].symbol] for e in self._legs),
                "net_premium": sum(np.sign(e["qty"]) * e["premium"] for e in self._legs),
                # On a roll bar only: what the EXPIRING legs settled at, so the figure can show the
                # period's closing value before the next period restarts at 1.0.
                "settle_value": (sum(np.sign(q) * px for _, px, q in self._closing)
                                 if self._closing else None),
            }
        return marks

    # ---- read-out helpers for figures ------------------------------------------

    def events_frame(self):
        # The events log as a DataFrame — imported lazily so the rule stays pandas-free.
        import pandas as pd
        return pd.DataFrame(self.events)


class ProtectivePutRule(OptionOverlayRule):
    """Long one put at ``floor`` x spot per unit held. Maximum cost, no upside given away."""

    def __init__(self, floor=0.90, **kw):
        # 0.90 = 10% OTM, matching HEDGE_FLOOR_LOSS = 0.10 at bse.py:3940.
        self.floor = float(floor)
        # ------------------------------------------------------------------
        # Hand the SHARED parameters up to OptionOverlayRule.__init__ (`**kw` is
        # whatever the caller passed of underlying / reset_bars / rate / div_yield /
        # base_vol / vol_fn / hedge_ratio). That base constructor is what builds the
        # RollCalendar, installs the default v1 vol_fn and calls reset() to create
        # the bar counter, the empty leg list and the events log — so without this
        # line the rule would have no schedule, no vol and no state, and propose()
        # would fail on the first bar.
        #
        # Called LAST, after this subclass's strike parameters are set, because
        # reset() runs inside it: every attribute the rule needs must already exist
        # by then. The strike fields are subclass-only (the base never reads them);
        # they reach the base solely through _leg_spec(), which it calls on a roll.
        # ------------------------------------------------------------------
        super().__init__(**kw)

    def _leg_spec(self):
        return [("P", self.floor, +1)]


class PutSpreadRule(OptionOverlayRule):
    """Long the ``floor`` put, short the ``spread_width`` put below it to cheapen the floor."""

    def __init__(self, floor=0.90, spread_width=0.80, **kw):
        # The written strike must sit BELOW the bought one. Raise rather than swap: a silent swap
        # would turn a debit spread into a credit spread — a different, short-vol position.
        if not spread_width < floor:
            raise ValueError(f"spread_width {spread_width} must be below floor {floor}")
        self.floor, self.spread_width = float(floor), float(spread_width)
        # ------------------------------------------------------------------
        # Hand the SHARED parameters up to OptionOverlayRule.__init__ (`**kw` is
        # whatever the caller passed of underlying / reset_bars / rate / div_yield /
        # base_vol / vol_fn / hedge_ratio). That base constructor is what builds the
        # RollCalendar, installs the default v1 vol_fn and calls reset() to create
        # the bar counter, the empty leg list and the events log — so without this
        # line the rule would have no schedule, no vol and no state, and propose()
        # would fail on the first bar.
        #
        # Called LAST, after this subclass's strike parameters are set, because
        # reset() runs inside it: every attribute the rule needs must already exist
        # by then. The strike fields are subclass-only (the base never reads them);
        # they reach the base solely through _leg_spec(), which it calls on a roll.
        # ------------------------------------------------------------------
        super().__init__(**kw)

    def _leg_spec(self):
        return [("P", self.floor, +1), ("P", self.spread_width, -1)]


class RollingCollarRule(OptionOverlayRule):
    """Long the ``floor`` put, short the ``cap`` call, and redeploy / fund at every roll."""

    def __init__(self, floor=0.90, cap=1.28, redeploy_trigger=-0.05, redeploy_rate=1.0, **kw):
        # Cap 1.28 = give up everything above +28% per period (HEDGE_UPSIDE_CAP = 0.28,
        # bse.py:3941). Deliberately wide: a +5% cap is a different, short-vol structure.
        if not cap > floor:
            raise ValueError(f"cap {cap} must be above floor {floor}")
        self.floor, self.cap = float(floor), float(cap)
        # Redeploy positive hedge cash only after a period at or below this return (-5%).
        self.redeploy_trigger = float(redeploy_trigger)
        # Fraction of that cash converted into more underlying (1.0 = all of it).
        self.redeploy_rate = float(redeploy_rate)
        # ------------------------------------------------------------------
        # Hand the SHARED parameters up to OptionOverlayRule.__init__ (`**kw` is
        # whatever the caller passed of underlying / reset_bars / rate / div_yield /
        # base_vol / vol_fn / hedge_ratio). That base constructor is what builds the
        # RollCalendar, installs the default v1 vol_fn and calls reset() to create
        # the bar counter, the empty leg list and the events log — so without this
        # line the rule would have no schedule, no vol and no state, and propose()
        # would fail on the first bar.
        #
        # Called LAST, after this subclass's strike parameters are set, because
        # reset() runs inside it: every attribute the rule needs must already exist
        # by then. The strike fields are subclass-only (the base never reads them);
        # they reach the base solely through _leg_spec(), which it calls on a roll.
        # ------------------------------------------------------------------
        super().__init__(**kw)

    def _leg_spec(self):
        return [("P", self.floor, +1), ("C", self.cap, -1)]

    def _on_roll(self, spot, closed_value, opened_premium, units, book, deltas):
        # ============================================================================
        # SETTLE -> REDEPLOY / FUND — bse.py:4196-4218, the mechanic v0.4 exists to test.
        # Runs AFTER the expiring legs are closed and BEFORE the new pair opens.
        # Deviation from source, noted as 12-01 asks: bse.py marks legs at pure
        # intrinsic and settles at zero carry, so its net_cash_flow never includes
        # premium. Here the legs are Black-Scholes priced, so it does.
        # ============================================================================

        # Net settlement cash of the EXPIRING pair: put payout minus call payout, per unit, times
        # units. Positive = the floor paid out; negative = the cap was breached.
        #
        # DEVIATION FROM 12-01's TASK TEXT, logged in 16-03-SUMMARY: the task defines net cash as
        # settlement MINUS the replacement pair's premium, but its own AC-6 requires a +3% period
        # to CARRY. Those conflict — a 0.90 put costs more than a 1.28 call earns, so with the
        # premium subtracted every quiet up-quarter would read negative and trigger "funding",
        # selling underlying each roll just to pay for the hedge. The AC is the testable contract,
        # so it wins: the new pair's premium is paid through its own opening fill in the Book
        # (which is where it belongs), and only the settlement drives redeploy / funding.
        net = closed_value * units
        # The period's return, measured from the spot the expiring pair was struck at.
        period_return = spot / self._period_start_spot - 1.0
        held = self._held_units(book)

        # Branch 1: a bad period and positive cash -> buy MORE underlying at the depressed price.
        # This, not the hedge payoff itself, is where the edge is supposed to come from.
        if net > 0 and period_return <= self.redeploy_trigger:
            buy = self.redeploy_rate * net / spot
            deltas[self.underlying] = deltas.get(self.underlying, 0.0) + buy
            self._log(self._ts, "redeploy", self.underlying, buy, spot, "collar redeploy",
                      net_cash=net, period_return=period_return,
                      new_pair_premium=opened_premium * units)
        # Branch 2: the structure owes money -> sell underlying to fund it, capped at 99.9% of
        # the position (bse.py:4210) so a breached cap can never drive the book to zero units.
        elif net < 0:
            sell = min(-net / spot, 0.999 * held)
            deltas[self.underlying] = deltas.get(self.underlying, 0.0) - sell
            self._log(self._ts, "funding", self.underlying, -sell, spot, "collar funding",
                      net_cash=net, period_return=period_return,
                      new_pair_premium=opened_premium * units)
        # Branch 3: good period, positive cash -> carry it. The Book has no cash ledger: closing
        # the legs has already booked this as realised P&L inside equity, so "carried" means it
        # simply is NOT converted into underlying units. It is re-tested at the next roll only in
        # the sense that the next settlement is judged on its own period.
        else:
            self._log(self._ts, "carry", self.underlying, 0.0, spot, "collar carry",
                      net_cash=net, period_return=period_return,
                      new_pair_premium=opened_premium * units)
