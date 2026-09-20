"""
OPTIONS — synthetic option contracts as instruments a Book can hold.

WHY A SYNTHETIC CONTRACT IS AN INSTRUMENT, NOT A STRATEGY
--------------------------------------------------------
Phase 16 groups code by STAGE (``.paul/phases/16-strategy-architecture/CONTEXT.md``, "the nouns,
by stage"). An option leg is a thing a portfolio can HOLD — it has a symbol, a quantity and a
mark — exactly like a share of SPY. What to do with it (buy a floor, sell a cap, roll every 63
bars) is a RULE, and lives in ``strategies/rules/options.py``. Keeping the two apart means an
option bug surfaces in the stage that actually failed: a wrong price is here, a wrong roll is in
the rule.

WHAT THIS MODULE DOES NOT DO
----------------------------
* It does not implement Black-Scholes. ``OptionLeg`` is a thin wrapper over ``pricing.py`` — a
  second implementation of the formula would be a different model wearing the same numbers,
  which is the failure the 2026-08-31 probe was written to prevent.
* It does not fetch vol. 12-01's constraint: vol reaches the leg ALREADY evaluated at the leg's
  own strike. The surface is a pipeline concern, passed in; the leg never calls it.
* It does not count calendar days. Time is BARS of the price panel (``RollCalendar`` in
  ``strategies/schedule.py``), converted to a year fraction at 252 per year.

SPY IS AMERICAN, THIS IS EUROPEAN
---------------------------------
Every price here is European (decision 2026-08-26). For SPX that is exact; for SPY it understates
a deep-ITM put's early-exercise value. That is accepted and conservative — see ``pricing.py``.
"""

from dataclasses import dataclass

from .pricing import black_scholes_call, black_scholes_delta, black_scholes_put


# Trading bars per year. The same 252 as crash.py:1901 (`time_to_expiry = remaining_days / 252.0`)
# and RollCalendar.year_fraction, so a leg's tau and the calendar's can never disagree.
BARS_PER_YEAR = 252.0


class SyntheticContract:
    """Base for any instrument that has no price-panel column and must be marked by model.

    The contract every subclass answers: a stable ``symbol`` (so the Book can key a Position on
    it) and a ``mark(spot, vol, tau)`` (so the simulator can value it on a bar where no market
    price exists). Kept deliberately tiny — a future variance swap or a futures roll would
    subclass this too, which is why it is not option-specific.
    """

    @property
    def symbol(self):
        raise NotImplementedError

    def mark(self, spot, vol, tau):
        raise NotImplementedError


@dataclass(frozen=True)
class OptionLeg(SyntheticContract):
    """One European option on ``underlying``, opened on ``open_bar`` and expiring on ``expiry_bar``.

    Frozen on purpose: a leg is a CONTRACT, and a contract's terms do not change after it is
    struck. A roll closes this leg and opens a new one — it never edits the old one's strike.
    Quantity is NOT a field: how many the book holds is the Book's business (a Position), and a
    rule's business to propose, not the contract's.
    """

    # Ticker of the underlying, e.g. "SPY". Also the prefix of the leg's symbol.
    underlying: str
    # "P" or "C". Validated in __post_init__ so a typo can never price the wrong side.
    right: str
    # Absolute strike, in the underlying's price units.
    strike: float
    # Bar index the leg was struck on — recorded for the rationale log, not used in pricing.
    open_bar: int
    # Bar index the leg expires on. tau is measured from here, in bars.
    expiry_bar: int
    # Continuously compounded risk-free rate. 0.02 matches 12-01's worked examples and the probe.
    rate: float = 0.02
    # Continuous dividend yield. 0.0 matches 12-01 and the probe so numbers can be cross-checked
    # by hand; SPY's real ~1.2% is a value 16-03's rules may pass, and should for a real result.
    div_yield: float = 0.0

    def __post_init__(self):
        # Gate 1: the right must be one of the two things the pricer knows how to price.
        if self.right not in ("P", "C"):
            raise ValueError(f"right must be 'P' or 'C', got {self.right!r}")
        # Gate 2: a strike of zero or below has no Black-Scholes meaning (log(S/K) blows up).
        if not self.strike > 0:
            raise ValueError(f"strike must be positive, got {self.strike!r}")
        # Gate 3: a leg cannot expire before it is struck.
        if self.expiry_bar < self.open_bar:
            raise ValueError(f"expiry_bar {self.expiry_bar} precedes open_bar {self.open_bar}")

    # ---- identity --------------------------------------------------------------

    @property
    def symbol(self):
        # Deterministic, human-readable and unique per (underlying, right, strike, expiry_bar):
        # e.g. "SPY P 625.94 @b63". It becomes a Book position key and a ledger column name, so
        # it must be stable across runs (no ids, no timestamps). Strike is printed to 2dp — two
        # strikes within half a cent on the same expiry would collide, which no rule here produces.
        return f"{self.underlying} {self.right} {self.strike:.2f} @b{self.expiry_bar}"

    # ---- time ------------------------------------------------------------------

    def tau(self, current_bar):
        # Remaining life as a YEAR fraction, from remaining BARS. Floored at zero so a leg marked
        # after its expiry bar is priced as expired (its degenerate branch) rather than negative.
        return max(self.expiry_bar - current_bar, 0) / BARS_PER_YEAR

    def is_expired(self, current_bar):
        # On or after the expiry bar the leg settles; this is the bar the rule closes it.
        return current_bar >= self.expiry_bar

    # ---- value -----------------------------------------------------------------

    def price(self, spot, vol, tau):
        # Thin wrapper: route to the one European pricer for this right. `vol` must already be
        # evaluated AT THIS STRIKE by the caller (12-01's strike-dependent-vol constraint).
        pricer = black_scholes_put if self.right == "P" else black_scholes_call
        return pricer(spot, self.strike, tau, vol, rate=self.rate, div_yield=self.div_yield)

    def mark(self, spot, vol, tau):
        # SyntheticContract's interface name for the same thing — what the simulator calls.
        return self.price(spot, vol, tau)

    def intrinsic(self, spot):
        # UNDISCOUNTED exercise value — what the leg pays if settled right now. This is the
        # settlement price on the expiry bar (tau = 0, where discounted and undiscounted agree).
        # It is NOT a floor to clamp price() to before expiry: see pricing.py's AC-1 note.
        if self.right == "P":
            return max(self.strike - float(spot), 0.0)
        return max(float(spot) - self.strike, 0.0)

    # ---- greeks ----------------------------------------------------------------

    def delta(self, spot, vol, tau):
        # dV/dS from the closed form in pricing.py; put in [-1, 0], call in [0, 1].
        return black_scholes_delta(spot, self.strike, tau, vol, self.right,
                                   rate=self.rate, div_yield=self.div_yield)

    def theta(self, spot, vol, tau):
        # Value change from ONE trading bar passing, spot and vol held fixed. Defined as a
        # one-bar price difference rather than the analytic dV/dt because the bar is the unit the
        # simulator actually steps in — this is the decay a book really books between two bars,
        # including the discrete jump into expiry that the continuous theta misses.
        return self.price(spot, vol, max(tau - 1.0 / BARS_PER_YEAR, 0.0)) - self.price(spot, vol, tau)
