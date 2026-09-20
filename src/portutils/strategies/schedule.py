"""
SCHEDULE — when a periodic action fires, counted in bars of the price panel.

A CADENCE, NOT AN OPTION CONCEPT
--------------------------------
``RollCalendar`` answers one question — "is this bar a reset bar?" — and nothing in it knows
what is being reset. The collar uses it to roll its legs every 63 bars; a quarterly rebalance
could use the same object unchanged. That is why it lives at the schedule stage and not beside
``OptionLeg`` (CONTEXT.md, "the nouns, by stage").

TRADING DAYS (BARS), NOT CALENDAR DAYS — decided 2026-08-26 in 12-01 AC-2
------------------------------------------------------------------------
The reset is counted in BARS of the price panel, never in ``timedelta`` days. Three reasons, in
order of weight:

1. **It matches the source.** ``bse.py:4134`` ``simulate_hedge_strategy_from_underlying`` walks
   ``start + reset_days`` as integer indices into a path array with ``trading_days_per_year=252``
   — ``HEDGE_RESET_DAYS = 63`` there is a quarter of trading days, i.e. ~91 calendar days, not 63.
2. **It is simpler here.** The rule already receives one bar at a time from
   ``PortfolioSimulator.run()``; a bar counter needs no date arithmetic, no weekend or holiday
   handling, and no market-calendar dependency.
3. **It cannot silently desynchronise from the panel.** A calendar-day roll can land on a date
   the panel has no row for, and would then either be skipped or fire on an arbitrary
   neighbouring bar. A bar count cannot drift from the data it indexes.

``tau`` for pricing is still a genuine year fraction: ``remaining_bars / 252``, matching
``crash.py:1901`` (``time_to_expiry = remaining_days / 252.0``).
"""

from .instruments.options import BARS_PER_YEAR


class RollCalendar:
    """A fixed reset every ``reset_bars`` bars. Pure integer arithmetic — no dates anywhere."""

    def __init__(self, reset_bars=63):
        # 63 trading bars = one quarter, matching HEDGE_RESET_DAYS = 63 at bse.py:3939, which
        # indexes bars rather than dates. Must be positive: a zero reset would roll every bar.
        if int(reset_bars) <= 0:
            raise ValueError(f"reset_bars must be a positive integer, got {reset_bars!r}")
        self.reset_bars = int(reset_bars)

    def next_roll(self, from_bar):
        # The bar the NEXT reset fires on, given the bar the current period started.
        return int(from_bar) + self.reset_bars

    def is_roll_day(self, current_bar, entry_bar):
        # True once a full period has elapsed since `entry_bar`. `>=` rather than `==` so a rule
        # that somehow misses the exact bar still rolls on the next one, instead of never.
        return (int(current_bar) - int(entry_bar)) >= self.reset_bars

    def year_fraction(self, remaining_bars):
        # The `t` fed to the pricer: remaining bars over 252. Same constant as OptionLeg.tau, so
        # the calendar and the leg can never disagree about how much life a contract has left.
        return int(remaining_bars) / BARS_PER_YEAR
