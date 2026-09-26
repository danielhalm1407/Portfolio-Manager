"""
13-01 — v2 PRICING AND THE MONETISE / WAIT / REOPEN POLICY.

WHAT THIS SUITE PINS
--------------------
Two separable things, both added by 13-01 and both defaulting to OFF:

1. ``vol_mode="v2"`` — the vol LEVEL comes bar by bar from a real implied-vol series and the
   moneyness reference becomes the current bar's spot. The pair is inseparable by construction,
   and a missing vol number raises rather than being filled.
2. The monetisation state machine — close the structure early when the underlying has fallen
   far enough (the DRAWDOWN trigger, primary) or when the long leg is worth a multiple of its
   premium (the MULTIPLE trigger, comparator), then wait for cheaper vol before reopening.

Everything runs on hand-built price paths: no panel, no parquet, no TWS. A policy test that
needed 5,204 real bars to say whether a threshold fired would be an integration test wearing a
unit test's name, and it would not be able to construct the cases that matter (a path that
reaches the multiple exactly, a vol series that sits above the gate for a known number of bars).
"""

import numpy as np
import pandas as pd
import pytest

from portutils.portfolio.rules import BuyAndHoldRule
from portutils.portfolio.simulator import PortfolioSimulator
from portutils.strategies.instruments.vol import synthetic_iv_surface
from portutils.strategies.rules.options import (
    ProtectivePutRule,
    PutSpreadRule,
    RollingCollarRule,
)

UNDERLYING = "SPY"


# ============================================================================
# HELPERS — price paths and runs. Built here rather than imported so a change
# to the probe's own window can never silently change what these tests assert.
# ============================================================================

def _path(values, start="2020-01-01"):
    # A daily business-day spot path. Business days because the rule counts BARS, never dates —
    # the index only has to be unique and ordered for the IV lookup to key on it.
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series([float(v) for v in values], index=idx, name=UNDERLYING)


def _flat_then_fall(n_flat, n_fall, start=100.0, end=70.0):
    # Flat, then a straight-line decline. The shape of Finding 7: a put struck at a peak, with
    # the market then falling far enough to put it deep in the money before expiry.
    flat = [start] * n_flat
    fall = list(np.linspace(start, end, n_fall))
    return _path(flat + fall)


def _run(rule, spot_path):
    # One unit of the underlying, hedged one-for-one, exactly as overlay_runs does.
    prices = spot_path.to_frame(UNDERLYING)
    rules = [BuyAndHoldRule({UNDERLYING: 1.0})] + ([rule] if rule is not None else [])
    sim = PortfolioSimulator(prices, rules, starting_capital=float(spot_path.iloc[0]))
    return sim.run(), sim


def _iv(spot_path, level=0.20):
    # A constant real-IV series aligned to the path. Constant by default so a test that is about
    # the STRIKE or the TRIGGER is not also about the vol level.
    return pd.Series(float(level), index=spot_path.index)


def _events(rule, action):
    return [e for e in rule.events if e["action"] == action]


# ============================================================================
# AC-2 — v2 COUPLES THE TWO TERMS, AND CANNOT BE HALF-ENABLED
# ============================================================================

def test_v2_marks_use_this_bars_iv_and_this_bars_spot():
    """The v2 mark must equal a direct synthetic_iv_surface call at bar t's IV AND spot.

    This is the whole content of AC-2: not "v2 is different from v1", which a typo would also
    satisfy, but that it is the specific surface the probe's iv_paths_market measured.
    """
    spot_path = _flat_then_fall(5, 40)
    iv = _iv(spot_path, 0.25)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             vol_mode="v2", iv_series=iv)
    _run(rule, spot_path)

    entry = rule._legs[0]
    leg = entry["leg"]
    bar = rule._bar
    spot_now = float(spot_path.iloc[bar])
    tau = leg.tau(bar)

    expected_vol = float(synthetic_iv_surface(leg.strike, tau, spot_now, base=0.25))
    expected_px = float(leg.price(spot_now, expected_vol, tau))
    assert rule._price(entry, spot_now) == pytest.approx(expected_px, rel=1e-12)

    # And the v1 reference it is NOT: same leg, same bar, priced off the strike-time spot and a
    # frozen base. If these ever coincide the test above has stopped discriminating.
    v1_vol = float(synthetic_iv_surface(leg.strike, tau, entry["spot_ref"], base=0.16))
    assert expected_vol != pytest.approx(v1_vol, rel=1e-9)


def test_v2_moneyness_reference_is_the_current_spot_not_the_strike_time_spot():
    """Pin term B specifically, by moving spot far from where the leg was struck."""
    spot_path = _flat_then_fall(5, 40, start=100.0, end=70.0)
    iv = _iv(spot_path, 0.20)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, vol_mode="v2", iv_series=iv)
    _run(rule, spot_path)

    entry = rule._legs[0]
    leg, bar = entry["leg"], rule._bar
    spot_now, tau = float(spot_path.iloc[bar]), leg.tau(bar)
    # Spot must actually have moved, or the assertion below is vacuous.
    assert abs(spot_now - entry["spot_ref"]) > 1.0

    used = rule._vol_for(leg, tau, entry, spot_now)
    against_current = float(synthetic_iv_surface(leg.strike, tau, spot_now, base=0.20))
    against_struck = float(synthetic_iv_surface(leg.strike, tau, entry["spot_ref"], base=0.20))
    assert used == pytest.approx(against_current, rel=1e-12)
    assert used != pytest.approx(against_struck, rel=1e-9)


def test_v2_without_an_iv_series_raises_at_construction():
    with pytest.raises(ValueError, match="requires iv_series"):
        ProtectivePutRule(vol_mode="v2")


def test_v2_rejects_a_vol_fn_so_the_two_terms_cannot_be_split():
    """The only way to get term B without term A would be a custom vol_fn under v2."""
    spot_path = _path([100.0] * 10)
    with pytest.raises(ValueError, match="vol_fn cannot be combined"):
        ProtectivePutRule(vol_mode="v2", iv_series=_iv(spot_path),
                          vol_fn=lambda strike, tau, spot_ref: 0.3)


def test_unknown_vol_mode_raises():
    with pytest.raises(ValueError, match="vol_mode must be"):
        ProtectivePutRule(vol_mode="v3")


def test_v2_raises_naming_the_bar_when_iv_is_missing():
    """A coverage hole must be loud. Never forward-filled inside the rule."""
    spot_path = _flat_then_fall(3, 30)
    iv = _iv(spot_path, 0.20)
    iv.iloc[10] = np.nan
    rule = ProtectivePutRule(underlying=UNDERLYING, vol_mode="v2", iv_series=iv)
    with pytest.raises(ValueError, match="no implied vol for bar"):
        _run(rule, spot_path)


def test_v2_raises_when_the_iv_series_does_not_cover_the_window():
    spot_path = _flat_then_fall(3, 30)
    short_iv = _iv(spot_path, 0.20).iloc[:5]
    rule = ProtectivePutRule(underlying=UNDERLYING, vol_mode="v2", iv_series=short_iv)
    with pytest.raises(ValueError, match="no implied vol for bar"):
        _run(rule, spot_path)


# ============================================================================
# AC-1 — DEFAULTS CHANGE NOTHING
# ============================================================================

def test_v1_is_the_default_and_prices_exactly_as_before():
    """A rule constructed with no new arguments must be bit-identical to the old path."""
    spot_path = _flat_then_fall(5, 60)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING)
    assert rule.vol_mode == "v1"
    assert rule.iv_series is None
    _run(rule, spot_path)

    entry = rule._legs[0]
    leg, bar = entry["leg"], rule._bar
    spot_now, tau = float(spot_path.iloc[bar]), leg.tau(bar)
    # The exact pre-13-01 expression: frozen base_vol, strike-time spot_ref.
    expected = float(synthetic_iv_surface(leg.strike, tau, entry["spot_ref"], base=0.16))
    assert rule._vol_for(leg, tau, entry, spot_now) == pytest.approx(expected, rel=1e-15)


def test_all_three_structures_still_construct_with_no_new_arguments():
    for rule in (ProtectivePutRule(), PutSpreadRule(), RollingCollarRule()):
        assert rule.vol_mode == "v1"
        assert rule.vol_kwargs == {}


# ============================================================================
# AC-3 — MONETISATION CLOSES THE WHOLE STRUCTURE, ON EITHER TRIGGER
# ============================================================================

def test_drawdown_trigger_closes_on_the_bar_the_threshold_is_crossed():
    """Finding 7's shape: a put struck at a peak, the market then falling through the floor.

    The drawdown trigger is the PRIMARY one, so this is the headline behaviour of the plan.
    """
    spot_path = _flat_then_fall(3, 40, start=100.0, end=70.0)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             monetise_drawdown=0.10)
    _run(rule, spot_path)

    closes = _events(rule, "close")
    assert closes, "the drawdown trigger never fired"
    first = closes[0]
    assert first["reason"] == "monetise"
    assert first["trigger"] == "drawdown"
    # It must fire on the FIRST bar at or past the threshold, not some bars later.
    bar = first["bar"]
    peak = float(spot_path.iloc[:bar + 1].max())
    depth_at = (peak - float(spot_path.iloc[bar])) / peak
    depth_before = (peak - float(spot_path.iloc[bar - 1])) / peak
    assert depth_at >= 0.10
    assert depth_before < 0.10
    # And the rule went FLAT on that bar with its legs cleared. The "monetise" marker is
    # logged from the FLAT branch AFTER self._legs = [], so its presence on the close bar is
    # the state assertion — the state object itself has moved on by the time this runs.
    #
    # The rule's state at the END of the path is HEDGED, not FLAT, and that is correct. This
    # configuration arms no reentry_iv, so _gate_open() returns True unconditionally and the
    # rule reopens on the bar after each monetise. Before the 2026-09-21 _peak reset the
    # stale peak let it re-monetise immediately, so it alternated monetise/reopen for the
    # rest of the path (14 monetisations here) and ended FLAT only because the last bar
    # happened to be a monetise. It now takes a further 10% fall to re-fire.
    monetised = [e for e in _events(rule, "monetise") if e["bar"] == bar]
    assert monetised, "no monetise marker on the bar the close fired"
    assert monetised[0]["trigger"] == "drawdown"


def test_a_reopen_resets_the_drawdown_reference_so_the_trigger_cannot_refire_next_bar():
    """The monetise/reopen thrash, and the _peak reset that fixes it (13-01.1 finding 1).

    _drawdown_now measures against _peak, which only ever ratchets UP. Before the reopen
    reset, a monetise left _peak at the PRE-CRASH high, so the drawdown condition was still
    true on the very next bar: the rule re-struck into a market still below that old peak
    and fired again immediately. Over the full 2006-2026 history that was 281 monetisations
    against 48 rolls; on the 43-bar fixture below it was 14, on alternating bars.

    The property that must hold: a reopen strikes a fresh hedge at THIS spot, so the market
    has to fall monetise_drawdown AGAIN, from there, before the trigger may fire.
    """
    spot_path = _flat_then_fall(3, 40, start=100.0, end=70.0)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             monetise_drawdown=0.10)
    _run(rule, spot_path)

    monetise_bars = [e["bar"] for e in _events(rule, "monetise")]
    reopen_bars = [e["bar"] for e in _events(rule, "reopen")]
    assert monetise_bars, "the drawdown trigger never fired"
    assert reopen_bars, "the fixture must reopen at least once or it proves nothing"

    # THE REGRESSION GUARD. No monetise may land on the bar straight after a reopen: that
    # adjacency IS the thrash, and it is the one thing this test exists to keep out.
    for r in reopen_bars:
        assert r + 1 not in monetise_bars, (
            f"monetised on bar {r + 1}, one bar after reopening on bar {r} — the drawdown "
            f"reference did not reset on reopen")

    # And the economics behind that guard: every monetise after the first must sit a full
    # monetise_drawdown below the spot the PRECEDING reopen struck at, so the triggers walk
    # down a staircase instead of firing against a peak the current hedge never saw.
    for m in monetise_bars:
        prior = [r for r in reopen_bars if r < m]
        if not prior:
            # The first monetise is referenced to the path's own running peak, not to a
            # reopen — there has not been one yet. Nothing to check.
            continue
        entry = float(spot_path.iloc[max(prior)])
        depth = (entry - float(spot_path.iloc[m])) / entry
        assert depth >= 0.10, (
            f"monetised at bar {m} only {depth:.4f} below the reopen spot at bar "
            f"{max(prior)} — the reference is stale")


def test_multiple_trigger_closes_and_is_labelled_as_such():
    spot_path = _flat_then_fall(3, 40, start=100.0, end=70.0)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             monetise_multiple=1.5)
    _run(rule, spot_path)

    closes = _events(rule, "close")
    assert closes
    assert closes[0]["trigger"] == "multiple"
    assert closes[0]["multiple"] >= 1.5


def test_both_trigger_quantities_are_logged_whichever_fired():
    """AC-3b needs both numbers on every monetisation to compare the two policies."""
    spot_path = _flat_then_fall(3, 40, start=100.0, end=70.0)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, monetise_drawdown=0.10)
    _run(rule, spot_path)

    mon = _events(rule, "monetise")
    assert mon
    for e in mon:
        assert e["drawdown"] is not None
        assert e["multiple"] is not None


def test_arming_both_triggers_raises():
    with pytest.raises(ValueError, match="not both"):
        ProtectivePutRule(monetise_drawdown=0.10, monetise_multiple=2.0)


def test_defaults_never_monetise():
    spot_path = _flat_then_fall(3, 60, start=100.0, end=50.0)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING)
    _run(rule, spot_path)
    assert not _events(rule, "monetise")
    assert rule._state == "HEDGED"


def test_a_never_reached_threshold_equals_the_blind_roll_rule():
    """The equality that makes every default safe: an armed but unreachable trigger is a no-op."""
    spot_path = _path(list(np.linspace(100.0, 130.0, 200)))
    blind = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63)
    armed = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                              monetise_drawdown=0.99)
    st_blind, _ = _run(blind, spot_path)
    st_armed, _ = _run(armed, spot_path)

    eq_b = pd.Series(st_blind.equity if hasattr(st_blind, "equity") else st_blind)
    eq_a = pd.Series(st_armed.equity if hasattr(st_armed, "equity") else st_armed)
    pd.testing.assert_series_equal(eq_b, eq_a, atol=1e-12, rtol=0)
    assert not _events(armed, "monetise")


def test_collar_monetise_runs_the_settle_branch():
    """AC-3: the collar's redeploy / fund branch must run on a monetise as it does at a roll."""
    spot_path = _flat_then_fall(3, 40, start=100.0, end=70.0)
    rule = RollingCollarRule(floor=0.90, cap=1.28, underlying=UNDERLYING, reset_bars=63,
                             monetise_drawdown=0.10)
    _run(rule, spot_path)

    assert _events(rule, "monetise")
    branch = [e for e in rule.events if e["action"] in ("redeploy", "funding", "carry")]
    assert branch, "the collar's settle branch never ran on the monetise"
    # A 10%+ fall is below the -5% redeploy trigger and the put pays, so it must REDEPLOY.
    assert branch[0]["action"] == "redeploy"


# ============================================================================
# AC-4 — RE-ENTRY WAITS FOR CHEAPER INSURANCE, THEN THE CYCLE REPEATS
# ============================================================================

def _fall_then_flat(n_flat, n_fall, n_after, start=100.0, end=70.0):
    return _path([start] * n_flat + list(np.linspace(start, end, n_fall)) + [end] * n_after)


def test_high_vol_keeps_the_rule_flat_then_it_reopens_when_vol_falls():
    spot_path = _fall_then_flat(3, 30, 60)
    iv = pd.Series(0.40, index=spot_path.index)
    drop_at = 60
    iv.iloc[drop_at:] = 0.12
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             vol_mode="v2", iv_series=iv,
                             monetise_drawdown=0.10, reentry_iv=0.15)
    _run(rule, spot_path)

    mon = _events(rule, "monetise")
    reopen = _events(rule, "reopen")
    assert mon and reopen, "the cycle did not complete"
    # Flat for every bar between the monetisation and the vol drop — no earlier reopen.
    assert reopen[0]["bar"] == drop_at
    assert reopen[0]["forced"] is False
    assert mon[0]["bar"] < drop_at
    # Fresh legs are struck off THAT bar's spot, not the old strike.
    opens = [e for e in _events(rule, "open") if e["reason"] == "reopen"]
    assert opens
    assert opens[0]["strike"] == pytest.approx(0.90 * float(spot_path.iloc[drop_at]), rel=1e-12)
    # And the monetise test applies again from the next bar.
    assert rule._state == "HEDGED"


def test_max_flat_bars_forces_a_reopen_with_the_gate_still_failing():
    spot_path = _fall_then_flat(3, 30, 80)
    iv = pd.Series(0.40, index=spot_path.index)   # never falls to the gate
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             vol_mode="v2", iv_series=iv,
                             monetise_drawdown=0.10, reentry_iv=0.15, max_flat_bars=10)
    _run(rule, spot_path)

    mon = _events(rule, "monetise")
    reopen = _events(rule, "reopen")
    assert mon and reopen
    assert reopen[0]["forced"] is True
    assert reopen[0]["bar"] - mon[0]["bar"] == 10


def test_without_max_flat_bars_a_permanently_high_vol_leaves_the_book_unhedged():
    """The risk max_flat_bars exists to bound, pinned so it cannot be forgotten."""
    spot_path = _fall_then_flat(3, 30, 80)
    iv = pd.Series(0.40, index=spot_path.index)
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             vol_mode="v2", iv_series=iv,
                             monetise_drawdown=0.10, reentry_iv=0.15)
    _run(rule, spot_path)
    assert _events(rule, "monetise")
    assert not _events(rule, "reopen")
    assert rule._state == "FLAT"


def test_setting_both_gates_raises():
    spot_path = _path([100.0] * 10)
    with pytest.raises(ValueError, match="not both"):
        ProtectivePutRule(iv_series=_iv(spot_path), reentry_iv=0.15, reentry_iv_pctile=20.0)


def test_a_gate_without_an_iv_series_raises():
    with pytest.raises(ValueError, match="needs iv_series"):
        ProtectivePutRule(reentry_iv=0.15)


def test_percentile_gate_holds_while_vol_is_high_relative_to_its_own_history():
    """The percentile gate is RELATIVE, so the series must actually spike for it to bite.

    A constant vol series is trivially at every one of its own percentiles, so it would open the
    gate on the first bar — correct behaviour (vol is not elevated against its own history) but
    a fixture that tests nothing. Hence a calm stretch, then a spike, then cheap vol.
    """
    # Two things the fixture has to get right, both of which are properties of the gate itself:
    #
    # 1. The spike must COINCIDE with the fall, as it does in a real selloff — the vol that makes
    #    the old put pay is the same vol that makes its replacement dear. A spike arriving after
    #    the monetisation would let the rule reopen into calm vol before the spike ever started.
    # 2. The calm history must be LONG relative to the spike. A percentile gate compares today's
    #    vol to its own trailing window, so if the spike comes to dominate that window the low
    #    percentile rises to meet it and the gate opens while vol is still objectively high. With
    #    a 252-bar window and only a handful of calm bars behind it, the gate defeats itself
    #    within a month. That is a genuine limitation of the percentile form, and the reason
    #    reentry_iv (an absolute level) exists as the alternative.
    spot_path = _fall_then_flat(200, 30, 80)
    iv = pd.Series(0.15, index=spot_path.index)   # 200 bars of calm history
    iv.iloc[200:260] = 0.40                        # the spike, from the first bar of the fall
    iv.iloc[260:] = 0.05                           # cheap again
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             vol_mode="v2", iv_series=iv,
                             monetise_drawdown=0.10, reentry_iv_pctile=10.0,
                             pctile_window=252)
    _run(rule, spot_path)

    mon = _events(rule, "monetise")
    reopen = _events(rule, "reopen")
    assert mon, "the drawdown trigger never fired"
    assert reopen, "the percentile gate never reopened"
    # Held flat through the whole spike, and reopened only once vol was cheap again. The gate
    # reads ONLY bars up to and including today — a window that reached forward would have let
    # it reopen during the spike because it could already see the 0.05 bars coming.
    assert reopen[0]["bar"] >= 260
    assert reopen[0]["forced"] is False


def test_gate_rolls_delays_an_ordinary_expiry_roll():
    """gate_rolls=True applies the same 'do not buy dear insurance' test to a routine roll."""
    spot_path = _path(list(np.linspace(100.0, 115.0, 200)))
    iv = pd.Series(0.40, index=spot_path.index)   # always above the gate
    common = dict(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                  vol_mode="v2", iv_series=iv, reentry_iv=0.15)
    ungated = ProtectivePutRule(gate_rolls=False, **common)
    gated = ProtectivePutRule(gate_rolls=True, **common)
    _run(ungated, spot_path)
    _run(gated, spot_path)

    assert len(_events(ungated, "open")) > len(_events(gated, "open")), \
        "gate_rolls did not delay any roll"


# ============================================================================
# THE DECISION RECORD — history on every bar, and the two numbers on every event
# 13-01.1 finding 3's underlying cause. The probe figure reads its hovertext from
# rule.history, and until 2026-09-22 that dict was written under `if self._legs:`
# — so the MONETISE bar (whose branch clears the legs before the record runs) and
# every FLAT bar of the wait had no row at all. The markers the figure exists to
# explain hovered "drawdown NaN, iv NaN", and there was no IV path across the
# unhedged stretch to compare against the re-entry gate.
# ============================================================================

def _gfc_shaped_rule():
    """A configuration that monetises, waits out a forced flat stretch, and reopens."""
    spot_path = _flat_then_fall(5, 80, start=100.0, end=55.0)
    iv = pd.Series(0.40, index=spot_path.index)   # pinned above the gate: every reopen forced
    rule = ProtectivePutRule(floor=0.90, underlying=UNDERLYING, reset_bars=63,
                             monetise_drawdown=0.10, reentry_iv=0.15, max_flat_bars=10,
                             vol_mode="v2", iv_series=iv)
    _run(rule, spot_path)
    return rule, spot_path


def test_history_covers_every_bar_including_the_monetise_and_the_wait():
    rule, spot_path = _gfc_shaped_rule()
    assert len(rule.history) == len(spot_path), (
        f"history has {len(rule.history)} rows for {len(spot_path)} bars")

    monetise_bars = [e["bar"] for e in _events(rule, "monetise")]
    assert monetise_bars, "the fixture must monetise or it proves nothing"
    # The specific bars that used to be missing: the monetise itself, and the flat stretch after.
    for b in monetise_bars:
        assert b in rule.history, f"no history row on monetise bar {b}"
        assert rule.history[b]["state"] == "FLAT"

    flat_rows = [h for h in rule.history.values() if h["state"] == "FLAT"]
    assert flat_rows, "the fixture must spend bars FLAT"
    # drawdown and iv are the two numbers the figure and the blotter are read for. They must be
    # present THROUGH the wait, not just where legs happen to be held.
    assert all(h["drawdown"] is not None for h in flat_rows)
    assert all(h["iv"] is not None for h in flat_rows)
    # The leg-dependent fields degrade to 0.0 rather than vanishing, so the frame keeps one
    # schema and a consumer never branches on whether a row exists.
    assert all(h["net_value"] == 0.0 and h["net_premium"] == 0.0 for h in flat_rows)


def test_the_gate_verdict_is_recorded_beside_the_iv_it_was_computed_from():
    # The re-entry gate is a comparison between a LEVEL and a SERIES. Persisting only `iv` left
    # every reader to re-derive the verdict — and re-deriving it with a different rounding or a
    # different window is how a figure comes to disagree with the rule it depicts.
    rule, _ = _gfc_shaped_rule()
    flat_rows = [h for h in rule.history.values() if h["state"] == "FLAT"]
    assert flat_rows
    assert all(isinstance(h["gate_open"], bool) for h in flat_rows)
    # IV is pinned at 0.40 against a 0.15 gate, so it is shut for the whole wait and every
    # reopen in this fixture is forced by max_flat_bars.
    assert not any(h["gate_open"] for h in flat_rows)
    assert all(e["forced"] for e in _events(rule, "reopen"))
    # While HEDGED the gate is not consulted, and a True/False there would read as a decision
    # the rule never made.
    assert all(h["gate_open"] is None
               for h in rule.history.values() if h["state"] == "HEDGED")


def test_every_event_carries_the_drawdown_and_the_iv_that_decided_it():
    # A fill is only explainable beside the state that produced it. Rolls needed this as much as
    # monetisations: a roll is what happens when the trigger did NOT fire, so without the depth
    # it was tested against, "why did this roll rather than monetise" has no answer on the row.
    rule, _ = _gfc_shaped_rule()
    assert rule.events
    for e in rule.events:
        assert e.get("drawdown") is not None, f"{e['action']}/{e['reason']} has no drawdown"
        assert e.get("iv") is not None, f"{e['action']}/{e['reason']} has no iv"
    # A reopen's drawdown is 0.0 by construction — the reference restarts there. That zero read
    # against the preceding monetise's depth IS the 2026-09-21 fix, visible on the blotter.
    for e in _events(rule, "reopen"):
        assert e["drawdown"] == pytest.approx(0.0)


def test_the_monetise_bar_records_the_multiple_the_trigger_fired_on():
    """The series a figure plots must agree with the event log on the bar that matters.

    The monetise branch clears _legs and zeroes _long_premium, so by the time synthetic_marks
    writes the bar's history row, _multiple_now returns None — the position it is a ratio of is
    gone. The read-out is therefore stashed at the decision and preferred here, otherwise the
    multiple series breaks at exactly the bar the policy acted on and a panel plotting it tops
    out at whatever the PREVIOUS bar reached.
    """
    rule, _ = _gfc_shaped_rule()
    monetisations = _events(rule, "monetise")
    assert monetisations, "the fixture must monetise or it proves nothing"
    for e in monetisations:
        row = rule.history[e["bar"]]
        assert row["multiple"] == pytest.approx(e["multiple"]), (
            f"bar {e['bar']}: event says {e['multiple']}, history says {row['multiple']}")
        assert row["drawdown"] == pytest.approx(e["drawdown"])
        # And it must be a real ratio, not the None that _multiple_now would return post-close.
        assert row["multiple"] is not None and row["multiple"] > 0


def test_the_decision_readout_does_not_leak_into_the_following_bar():
    # The stash is cleared at the top of every propose. If it were not, every bar after a
    # monetise would report that monetise's drawdown and multiple for the rest of the run.
    rule, _ = _gfc_shaped_rule()
    monetise_bars = [e["bar"] for e in _events(rule, "monetise")]
    assert monetise_bars
    for b in monetise_bars:
        nxt = rule.history.get(b + 1)
        if nxt is None:
            continue
        # The bar after a monetise is FLAT: no legs, so no premium, so no ratio.
        assert nxt["state"] == "FLAT"
        assert nxt["multiple"] is None, "the monetise bar's multiple leaked forward"
