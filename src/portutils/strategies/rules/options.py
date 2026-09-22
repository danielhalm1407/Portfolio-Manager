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

v2 PRICING (13-01) — OPT-IN, AND BOTH TERMS TOGETHER OR NEITHER
---------------------------------------------------------------
``vol_mode="v2"`` replaces BOTH of v1's assumptions at once, with an ``iv_series`` of real
measured implied vol (11-01 cached IBKR's OPTION_IMPLIED_VOLATILITY back to 2006-01-09):

  term A, the vol LEVEL     — ``base`` comes bar by bar off ``iv_series`` instead of 0.16
  term B, the moneyness ref — ``spot_ref`` becomes the CURRENT bar's spot, not the strike-time one

There is deliberately NO setting that enables one without the other (the 2026-08-29 decision).
Term B alone slides a fixed strike DOWN the smirk as spot falls, which makes a crashing market
look like CHEAPER insurance — the opposite of what happens, and a bias that flatters the hedge.
Term A alone leaves the moneyness reference stale. Only the pair is a coherent market.

The default is still ``"v1"``, so every existing caller prices exactly as it did before.
"""

import numpy as np

from portutils.portfolio.rules import RebalanceRule

from ..instruments.options import OptionLeg
from ..instruments.vol import synthetic_iv_surface
from ..schedule import RollCalendar


class OptionOverlayRule(RebalanceRule):
    """Shared roll / mark / log machinery. Subclasses only declare their legs."""

    def __init__(self, underlying="SPY", reset_bars=63, rate=0.02, div_yield=0.0,
                 base_vol=0.16, vol_fn=None, hedge_ratio=1.0,
                 vol_mode="v1", iv_series=None, vol_kwargs=None,
                 monetise_drawdown=None, monetise_multiple=None,
                 reentry_iv=None, reentry_iv_pctile=None, pctile_window=252,
                 max_flat_bars=None, gate_rolls=False):
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

        # ============================================================================
        # v2 PRICING SWITCH (13-01). See the module docstring for why the two terms
        # are inseparable. Everything here validates at CONSTRUCTION time, because a
        # misconfigured vol source that only fails on bar 900 of 5,204 wastes the run.
        # ============================================================================
        if vol_mode not in ("v1", "v2"):
            raise ValueError(f"vol_mode must be 'v1' or 'v2', got {vol_mode!r}")
        self.vol_mode = vol_mode
        # Real measured implied vol, indexed by the SAME timestamps the simulator hands to
        # propose(). Decimal (0.166 = 16.6%), the same convention as vol.py's `base`.
        self.iv_series = iv_series
        # Passed straight through to synthetic_iv_surface, so a later plan can vary iv_floor or
        # term_coef without editing this rule. NOT used by v1, whose surface constants are
        # whatever _v1_vol's call leaves at default.
        self.vol_kwargs = dict(vol_kwargs) if vol_kwargs else {}
        if vol_mode == "v2":
            # A v2 rule with no vol history cannot price anything. Refuse rather than fall back
            # to 0.16, because a silent fallback is v1 wearing v2's name in the results table.
            if iv_series is None:
                raise ValueError("vol_mode='v2' requires iv_series (real implied vol by bar)")
            # vol_fn is v1's strike->vol hook and takes the STRIKE-TIME spot_ref. Honouring it
            # under v2 would reinstate the stale moneyness reference v2 exists to remove, so the
            # combination is rejected outright instead of one silently winning.
            if vol_fn is not None:
                raise ValueError("vol_fn cannot be combined with vol_mode='v2' — v2 builds its "
                                 "own surface call from the current bar's IV and spot")

        # ============================================================================
        # THE MONETISE / WAIT / REOPEN POLICY (13-01). Every parameter defaults to
        # None or False, and with all of them off the rule behaves EXACTLY as it did
        # before: hold to expiry, roll every reset_bars, never go flat.
        # ============================================================================

        # --- trigger: when to sell a paying hedge rather than hold it to expiry -----------
        # DRAWDOWN is the primary trigger (2026-09-20): close when the underlying has fallen
        # this far below its running peak, as a decimal (0.10 = 10%). This is the loss EVENT the
        # insurance exists for, which is why it leads rather than the option's own price action.
        self.monetise_drawdown = None if monetise_drawdown is None else float(monetise_drawdown)
        # MULTIPLE is the comparator: close when the long leg is worth this many times what it
        # cost. Measured on the LONG legs only, never the net position — a collar's or a put
        # spread's net premium can be near zero or negative, which makes a net multiple
        # meaningless, and the long leg is what actually pays off in a selloff.
        self.monetise_multiple = None if monetise_multiple is None else float(monetise_multiple)
        # Arming both would make the results table unreadable: a monetisation could not be
        # attributed to either policy, and 13-02 sweeps one axis at a time.
        if self.monetise_drawdown is not None and self.monetise_multiple is not None:
            raise ValueError("arm monetise_drawdown OR monetise_multiple, not both — the whole "
                             "point of the comparison is to attribute each close to one of them")

        # --- gate: how cheap vol must get before buying insurance again -------------------
        # Absolute level, or a percentile of the trailing window. Exactly one.
        self.reentry_iv = None if reentry_iv is None else float(reentry_iv)
        self.reentry_iv_pctile = None if reentry_iv_pctile is None else float(reentry_iv_pctile)
        if self.reentry_iv is not None and self.reentry_iv_pctile is not None:
            raise ValueError("set reentry_iv OR reentry_iv_pctile, not both")
        self.pctile_window = int(pctile_window)
        # Either gate reads the vol series, so a gate without one cannot be evaluated. Fail at
        # construction rather than on the first bar after a monetisation, which could be years in.
        if (self.reentry_iv is not None or self.reentry_iv_pctile is not None) \
                and iv_series is None:
            raise ValueError("a re-entry gate needs iv_series — there is no vol to test without it")
        # Reopen anyway after this many flat bars. None means WAIT INDEFINITELY, which is a real
        # risk rather than a neutral default: in 2008-09 implied vol stayed elevated for months,
        # and an indefinite wait is an UNHEDGED book through exactly the drawdown the hedge was
        # bought for. 13-02's sweep must include a finite value.
        self.max_flat_bars = None if max_flat_bars is None else int(max_flat_bars)
        # Whether the same vol gate also delays an ORDINARY expiry roll. Default False matches the
        # policy as described (the gate follows a monetisation). True is a sweepable extension,
        # because a routine roll into a vol spike buys the same overpriced insurance.
        self.gate_rolls = bool(gate_rolls)

        self.reset()

    # ---- state -----------------------------------------------------------------

    def reset(self):
        # Bar counter: the simulator hands us bars one at a time and never an index, so the rule
        # counts them itself. -1 so the first propose() lands on bar 0.
        self._bar = -1
        # The current bar's timestamp, set at the top of every propose(). v2's IV lookup is keyed
        # on it, so it must exist before any pricing happens — None until the first bar.
        self._ts = None
        # Open legs: dicts of {leg, qty (signed), spot_ref, premium}. spot_ref is the spot the leg
        # was struck at — the v1 moneyness reference, fixed for the leg's whole life. Under v2 it
        # is still RECORDED (the events log reports where each leg was struck) but is not used for
        # pricing, because v2 measures moneyness against the current bar instead.
        self._legs = []
        # Legs being closed / opened on THIS bar, with their fill prices, so synthetic_marks can
        # price them without re-deciding anything. Cleared at the top of every propose().
        self._closing = []
        self._opening = []
        # Bar and spot the current period started on. None until the first legs are opened.
        self._period_start_bar = None
        self._period_start_spot = None
        # --- policy state (13-01) ---------------------------------------------------
        # HEDGED or FLAT. Starts HEDGED: with no trigger armed the rule never leaves it, which is
        # what makes every pre-13-01 caller behave identically.
        self._state = "HEDGED"
        # Bar the rule went flat on, for max_flat_bars. None while hedged.
        self._flat_since_bar = None
        # Running peak of the underlying, for the drawdown trigger. Tracked from the rule's first
        # valid spot and NOT reset on a monetise or a roll: the drawdown that matters to a holder
        # is measured from the high water mark of the position, not from whenever the last hedge
        # happened to be struck. A per-period reset is a DIFFERENT policy, and 13-02 may want it.
        self._peak = None
        # What the current period's LONG legs cost, for the multiple trigger. 0.0 when flat.
        self._long_premium = 0.0
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

    def _iv_now(self):
        # This bar's REAL implied-vol level, for v2. Looked up by timestamp, not by bar index:
        # the IV history and the price panel are pulled separately and are only guaranteed to
        # agree on dates, not on row positions.
        #
        # A missing or NaN bar RAISES, naming the bar. It is never forward-filled here —
        # alignment is the pipeline's job (align_real_iv in option_probe_figures.py), and a rule
        # that quietly filled would hide a coverage hole inside a number that looks priced.
        iv = self.iv_series.get(self._ts)
        if iv is None or not np.isfinite(iv):
            raise ValueError(
                f"vol_mode='v2': no implied vol for bar {self._bar} ({self._ts}). Align the IV "
                "series to the price index before constructing the rule — the rule will not "
                "forward-fill a missing vol")
        return float(iv)

    def _vol_for(self, leg, tau, entry, spot):
        # The one place the v1 / v2 choice is made, so there is no path that mixes them.
        if self.vol_mode == "v1":
            # v1: frozen base, moneyness against the spot the leg was STRUCK at.
            return self.vol_fn(leg.strike, tau, entry["spot_ref"])
        # v2: both terms from THIS bar — the measured level as `base`, the current spot as the
        # moneyness reference. Same call shape as iv_paths_market() in option_probe_figures.py,
        # which is where this combination was first measured.
        return float(synthetic_iv_surface(leg.strike, tau, spot,
                                          base=self._iv_now(), **self.vol_kwargs))

    def _price(self, entry, spot):
        # Mark one open leg at this bar's spot. On (or after) its expiry bar the leg is worth its
        # exercise value exactly — the pricer's degenerate branch gives the same number, but
        # stating it keeps settlement explicit rather than an accident of tau hitting zero.
        leg = entry["leg"]
        if leg.is_expired(self._bar):
            return leg.intrinsic(spot)
        tau = leg.tau(self._bar)
        return float(leg.price(spot, self._vol_for(leg, tau, entry, spot), tau))

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

    # ---- the monetise / wait / reopen policy (13-01) ---------------------------

    def _drawdown_now(self, spot):
        # Depth below the running peak, as a positive decimal. 0.0 at a new high.
        if self._peak is None or self._peak <= 0:
            return 0.0
        return max(0.0, (self._peak - spot) / self._peak)

    def _long_value(self, spot):
        # Value of the LONG legs only — see the monetise_multiple comment for why the net
        # position is the wrong denominator and the wrong numerator.
        return sum(self._price(e, spot) for e in self._legs if e["qty"] > 0)

    def _multiple_now(self, spot):
        # Value of the long legs over what they cost. None when nothing was paid, which is not a
        # ratio of zero — it is an undefined ratio, and returning 0.0 would read as "worthless".
        if self._long_premium <= 0:
            return None
        return self._long_value(spot) / self._long_premium

    def _monetise_trigger(self, spot):
        # Returns the name of the trigger that fired, or None. Both quantities are computed
        # whichever is armed, because AC-3b compares them and a number that was never measured
        # cannot be compared after the fact.
        depth = self._drawdown_now(spot)
        mult = self._multiple_now(spot)
        if self.monetise_drawdown is not None and depth >= self.monetise_drawdown:
            return "drawdown"
        if self.monetise_multiple is not None and mult is not None \
                and mult >= self.monetise_multiple:
            return "multiple"
        return None

    def _gate_open(self):
        # Is insurance cheap enough to buy again? With no gate set, always yes — the policy then
        # reduces to "monetise, then reopen on the next bar".
        if self.reentry_iv is None and self.reentry_iv_pctile is None:
            return True
        iv_now = self._iv_now()
        if self.reentry_iv is not None:
            return iv_now <= self.reentry_iv
        # Percentile gate: where does today's vol sit in its own trailing window? Uses only bars
        # up to and including today — a window that reached forward would be lookahead, and this
        # gate is precisely a decision made in real time.
        window = self.iv_series.loc[:self._ts].iloc[-self.pctile_window:]
        if len(window) < 2:
            return False
        return float(iv_now) <= float(np.nanpercentile(window.to_numpy(),
                                                       self.reentry_iv_pctile))

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
        # The running peak the drawdown trigger measures against. Updated on EVERY bar, hedged or
        # flat, because a peak set while unhedged still defines the next drawdown.
        self._peak = spot if self._peak is None else max(self._peak, spot)
        held = self._held_units(book)

        # ============================================================================
        # POLICY BRANCH A — MONETISE. Tested before the roll gate, so a trigger that
        # fires ON a roll bar closes the structure and goes FLAT rather than rolling
        # into a new one. Monetising takes precedence: the roll would otherwise buy
        # fresh insurance at exactly the elevated vol the trigger just fired on.
        # ============================================================================
        if self._state == "HEDGED" and self._legs:
            fired = self._monetise_trigger(spot)
            if fired is not None:
                return self._monetise(ts, spot, book, fired)

        # ============================================================================
        # POLICY BRANCH B — REOPEN. While FLAT nothing else can happen: there are no
        # legs to roll and no trigger to test.
        # ============================================================================
        if self._state == "FLAT":
            return self._maybe_reopen(ts, spot, held)

        # Gate 2: is a roll due? The FIRST open happens on the first bar the Book holds the
        # underlying (bar 1 when a BuyAndHoldRule buys on bar 0 — the same bar would size off
        # zero units). Every roll after that is the calendar's call.
        if self._period_start_bar is None:
            if held <= 0:
                return {}
        elif not self.calendar.is_roll_day(self._bar, self._period_start_bar):
            return {}

        # gate_rolls: apply the same "don't buy dear insurance" test to an ordinary expiry roll.
        # Only ever delays a roll that is already due, and only when explicitly armed. The legs
        # stay open past their expiry bar meanwhile, marked at intrinsic by _price's expired
        # branch — which is what a holder who chose not to re-hedge actually owns.
        if self.gate_rolls and self._period_start_bar is not None and not self._gate_open():
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
            # drawdown and iv stamped on the ROLL legs too, not only the monetise ones. A roll
            # is the decision the policy makes by DEFAULT — it is what happens when the trigger
            # did not fire — so the depth the trigger was tested against and the vol the
            # replacement is struck into are exactly what a reader needs to see beside it. Without
            # them a roll row and a monetise row are not comparable, and the question "why did
            # this roll rather than monetise" has no answer on the blotter.
            self._log(ts, "close", leg.symbol, -qty, px, "roll", strike=leg.strike,
                      premium=entry["premium"], pnl_per_unit=np.sign(qty) * (px - entry["premium"]),
                      drawdown=self._drawdown_now(spot), multiple=self._multiple_now(spot),
                      iv=self._iv_now() if self.vol_mode == "v2" else None)
        self._legs = []

        # --- 2. Strike the replacement legs off the CURRENT spot. ---------------------------
        # Sized off the units held BEFORE any collar redeploy this bar — the redeploy is a
        # consequence of the settlement, and sizing the hedge off it would make the two circular.
        units = self.hedge_ratio * held
        new_legs, opened_premium = self._strike_legs(spot, units)

        # --- 3. Structure-specific settlement (collar redeploy / funding). ----------------
        if units_closed > 0:
            self._on_roll(spot, closed_value, opened_premium, units_closed, book, deltas)

        # --- 4. Open. ----------------------------------------------------------------------
        for entry in new_legs:
            leg = entry["leg"]
            deltas[leg.symbol] = deltas.get(leg.symbol, 0.0) + entry["qty"]
            self._opening.append((leg, entry["premium"]))
            self._log(ts, "open", leg.symbol, entry["qty"], entry["premium"], "roll",
                      strike=leg.strike, premium=entry["premium"], pnl_per_unit=0.0,
                      drawdown=self._drawdown_now(spot),
                      iv=self._iv_now() if self.vol_mode == "v2" else None)
        self._legs = new_legs
        self._period_start_bar, self._period_start_spot = self._bar, spot
        # What this period's LONG legs cost, for the multiple trigger. Recorded here, at the one
        # place a period begins, so the denominator can never drift from the legs it refers to.
        self._long_premium = sum(e["premium"] for e in new_legs if e["qty"] > 0)
        return deltas

    # ---- the two policy branches ------------------------------------------------

    def _strike_legs(self, spot, units):
        # Build (but do not register) this period's legs, struck off the CURRENT spot and priced.
        # Shared by the ordinary roll and by a reopen, so the two cannot diverge in how a leg is
        # constructed — the only difference between them is what happened just before.
        new_legs, opened_premium = [], 0.0
        for right, mult, sign in self._leg_spec():
            leg = OptionLeg(self.underlying, right, mult * spot, open_bar=self._bar,
                            expiry_bar=self.calendar.next_roll(self._bar),
                            rate=self.rate, div_yield=self.div_yield)
            entry = {"leg": leg, "qty": sign * units, "spot_ref": spot, "premium": 0.0}
            entry["premium"] = self._price(entry, spot)
            opened_premium += sign * entry["premium"]
            new_legs.append(entry)
        return new_legs, opened_premium

    def _monetise(self, ts, spot, book, fired):
        # Close EVERY leg of the structure on this bar at its model mark and go FLAT.
        #
        # Both trigger quantities are recorded on every monetisation whichever one fired, because
        # AC-3b asks how often the two agree — a comparison that cannot be made retrospectively
        # from a log that only stored the winner.
        depth = self._drawdown_now(spot)
        mult = self._multiple_now(spot)
        iv_now = self._iv_now() if self.vol_mode == "v2" else None

        deltas = {}
        closed_value, units_closed = 0.0, 0.0
        for entry in self._legs:
            px = self._price(entry, spot)
            leg, qty = entry["leg"], entry["qty"]
            deltas[leg.symbol] = deltas.get(leg.symbol, 0.0) - qty
            self._closing.append((leg, px, qty))
            closed_value += np.sign(qty) * px
            units_closed = max(units_closed, abs(qty))
            self._log(ts, "close", leg.symbol, -qty, px, "monetise", strike=leg.strike,
                      premium=entry["premium"],
                      pnl_per_unit=np.sign(qty) * (px - entry["premium"]),
                      trigger=fired, drawdown=depth, multiple=mult, iv=iv_now)
        self._legs = []

        # The collar's settle -> redeploy / fund branch runs on a monetise exactly as it does at a
        # roll, with opened_premium=0.0: nothing is reopened on this bar, so the branch sees the
        # CLOSING cash alone. That is the honest input — there is no replacement pair to pay for.
        if units_closed > 0:
            self._on_roll(spot, closed_value, 0.0, units_closed, book, deltas)

        self._state = "FLAT"
        self._flat_since_bar = self._bar
        self._long_premium = 0.0
        # The period is over. Left as it was rather than cleared, so the collar's period_return
        # reads against the spot this hedge was actually struck at if it is consulted again.
        self._log(ts, "monetise", self.underlying, 0.0, spot, f"monetise:{fired}",
                  trigger=fired, drawdown=depth, multiple=mult, iv=iv_now,
                  closed_value=closed_value)
        return deltas

    def _maybe_reopen(self, ts, spot, held):
        # FLAT: hold fire until insurance is cheap again, or until the patience limit bites.
        flat_bars = self._bar - self._flat_since_bar
        forced = self.max_flat_bars is not None and flat_bars >= self.max_flat_bars
        if not forced and not self._gate_open():
            return {}
        # Nothing to hedge yet — stay flat rather than striking legs against zero units.
        if held <= 0:
            return {}

        units = self.hedge_ratio * held
        new_legs, _ = self._strike_legs(spot, units)
        deltas = {}
        iv_now = self._iv_now() if self.vol_mode == "v2" else None
        for entry in new_legs:
            leg = entry["leg"]
            deltas[leg.symbol] = deltas.get(leg.symbol, 0.0) + entry["qty"]
            self._opening.append((leg, entry["premium"]))
            # drawdown stamped alongside iv: an open is a DECISION, and the two numbers that
            # decided it are the depth the market is at and the vol the leg was struck into.
            # Reading them off the blotter row is what makes a fill explainable without
            # cross-referencing a second frame by timestamp.
            self._log(ts, "open", leg.symbol, entry["qty"], entry["premium"], "reopen",
                      strike=leg.strike, premium=entry["premium"], pnl_per_unit=0.0,
                      flat_bars=flat_bars, forced=forced, iv=iv_now,
                      drawdown=self._drawdown_now(spot))
        self._legs = new_legs
        # A NEW period starts here, so the next roll is reset_bars from THIS bar, not from
        # whenever the monetised period began.
        self._period_start_bar, self._period_start_spot = self._bar, spot
        # The DRAWDOWN REFERENCE restarts here too, for the same reason the period and the
        # premium below do. _drawdown_now measures against _peak, and until this line _peak
        # only ever ratcheted UP (see propose), so it survived the monetise that just
        # happened. That is what made the rule monetise and reopen on CONSECUTIVE BARS:
        # after a real crash the market sits below its old peak for years, so the drawdown
        # condition stayed true, re-fired on the next bar the IV gate allowed, and the full
        # history logged 281 monetisations against 48 rolls.
        # Resetting to THIS spot also makes the trigger commensurate with the rest of the
        # state. The legs just struck are referenced to this spot (floor x spot), and
        # _long_premium below resets for exactly the same reason: a peak the new put never
        # saw cannot describe the loss event the new put is being bought to insure.
        # Deliberately NOT reset on a roll — a roll continues the same hedge programme,
        # and resetting every reset_bars would stop the trigger ever firing on a slow
        # grind down. Decision recorded 2026-09-21.
        self._peak = spot
        self._long_premium = sum(e["premium"] for e in new_legs if e["qty"] > 0)
        self._state = "HEDGED"
        self._flat_since_bar = None
        self._log(ts, "reopen", self.underlying, 0.0, spot, "reopen",
                  flat_bars=flat_bars, forced=forced, iv=iv_now,
                  # Stamped AFTER the _peak reset above, deliberately: this is the drawdown the
                  # NEW hedge starts from, which is 0.0 by construction. That zero is the point —
                  # read against the preceding monetise row's depth, it shows the reference
                  # restarting, which is the whole mechanism of the 2026-09-21 fix.
                  drawdown=self._drawdown_now(spot))
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
        #
        # Recorded on EVERY bar, including the ones holding no legs. It used to be guarded by
        # `if self._legs:`, which silently dropped exactly the bars a reader needs most: the
        # MONETISE bar (the branch sets self._legs = [] before this runs, so the bar the rule
        # made its decision on had no row at all, and fig_ladder's hover showed NaN drawdown and
        # NaN iv on the monetise markers) and every FLAT bar of the wait (so there was no IV path
        # to compare against the re-entry gate — the one comparison that explains why the rule is
        # still unhedged). 13-01.1 diagnosed the thrash by reading fifteen tooltips one at a time
        # precisely because this gap hid the rest.
        #
        # The leg-dependent fields degrade to 0.0 rather than being omitted, so the frame has one
        # stable schema for every bar and a consumer never has to branch on whether a row exists.
        # 0.0 is the honest value for both: a rule holding no legs has no position value and has
        # paid no premium for one.
        self.history[self._bar] = {
                "bar": self._bar,
                "ts": self._ts,
                "period_start_bar": self._period_start_bar,
                # What the current legs are worth now, and what they cost when struck.
                "net_value": sum(np.sign(e["qty"]) * marks[e["leg"].symbol]
                                 for e in self._legs) if self._legs else 0.0,
                "net_premium": sum(np.sign(e["qty"]) * e["premium"]
                                   for e in self._legs) if self._legs else 0.0,
                # On a roll bar only: what the EXPIRING legs settled at, so the figure can show the
                # period's closing value before the next period restarts at 1.0.
                "settle_value": (sum(np.sign(q) * px for _, px, q in self._closing)
                                 if self._closing else None),
                # --- policy read-outs (13-01), so a figure can draw the trigger paths ---
                # The state AFTER this bar's propose, the two trigger quantities as they stand,
                # and the vol the marks were struck with. Recorded every bar, not only on the
                # bars something happened, because the figure needs the whole path to show the
                # ribbon and the threshold crossings.
                "state": self._state,
                "drawdown": self._drawdown_now(spot),
                "multiple": self._multiple_now(spot),
                "iv": self._iv_now() if self.vol_mode == "v2" else None,
                # The re-entry gate's own verdict, recorded beside the IV it was computed from.
                # The gate is a comparison between a LEVEL and a SERIES, and reading the two
                # apart is what made "the gate is nearly always open" invisible for so long.
                # None while HEDGED, where the gate is not consulted and a True/False would read
                # as a decision the rule never made.
                "gate_open": self._gate_open() if self._state == "FLAT" else None,
                # Bars spent unhedged so far, so max_flat_bars can be read off the same frame as
                # the gate it overrides.
                "flat_bars": (self._bar - self._flat_since_bar
                              if self._flat_since_bar is not None else 0),
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
