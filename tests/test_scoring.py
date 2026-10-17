"""
SCORING TESTS (13-02 Task 1) — the window set, the peak-reset convention and the floored Calmar.

WHAT THESE TESTS ARE FOR
------------------------
``scoring.py``'s metric half is pure arithmetic over a series, which means every one of its
decisions is assertable on a hand-built path — and two of those decisions are exactly the kind
that are easy to get wrong and invisible afterwards:

1. **The peak resets at the slice start.** If it carried in from before the window, a window that
   opens mid-decline would read as calm and every score in the grid would inherit the error. The
   test slices ONE series two ways and asserts the two drawdowns differ in the direction the
   convention requires, rather than asserting a single number that a carry-in implementation
   could also produce.
2. **The floor is flagged, not hidden.** A score set by the floor is not a Calmar. The flag is
   what lets the sweep report the floor-bound share AC-3 requires, so it is asserted as a
   returned value and not merely as a side effect on the score.

Everything is synthetic — no parquet, no real IV, no simulator — so the suite stays fast.
"""

import numpy as np
import pandas as pd
import pytest

from portutils.strategies import scoring
from portutils.strategies.scoring import (CALM, DRAWDOWN, DRAWDOWN_FLOOR, INDICATIVE_WINDOWS,
                                          calmar, max_drawdown, rank_configs, score_grid,
                                          window_return)


def _series(values, start="2020-01-01"):
    """A daily-indexed equity path. Business days, so the index looks like the real thing."""
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series([float(v) for v in values], index=idx)


# ============================================================================
# THE WINDOW SET (AC-4)
# ============================================================================

def test_window_set_has_both_kinds():
    # AC-4's substantive requirement: the set is not crisis-only. A drawdown-only set selects
    # mechanically for the most expensive hedge available, which is the failure mode the calm
    # windows exist to prevent.
    kinds = {kind for (_, _, kind) in INDICATIVE_WINDOWS.values()}
    assert kinds == {DRAWDOWN, CALM}
    assert sum(1 for v in INDICATIVE_WINDOWS.values() if v[2] == DRAWDOWN) == 3
    assert sum(1 for v in INDICATIVE_WINDOWS.values() if v[2] == CALM) == 3


def test_no_window_starts_before_the_real_iv_history():
    # v2 pricing cannot reach before 2006-01-09 (11-01: real OPTION_IMPLIED_VOLATILITY starts
    # there). A window that did would be scored on bars no configuration can actually price.
    for name, (start, _end, _kind) in INDICATIVE_WINDOWS.items():
        assert pd.Timestamp(start) >= pd.Timestamp("2006-01-09"), name


# ============================================================================
# MAX DRAWDOWN AND THE PEAK RESET
# ============================================================================

def test_max_drawdown_on_a_v_shape():
    # 100 up to 120, down to 90, back to 110. Deepest fall is 120 -> 90 = 25%.
    eq = _series([100, 110, 120, 105, 90, 100, 110])
    assert max_drawdown(eq) == pytest.approx(0.25)


def test_max_drawdown_is_zero_on_a_monotone_rise():
    assert max_drawdown(_series([100, 101, 102, 103])) == pytest.approx(0.0)


def test_peak_resets_at_the_slice_start():
    # ONE series, sliced two ways. The full path peaks at 120 before falling to 90 (25%); the
    # tail slice starts at 105, already mid-decline, and may only see 105 -> 90 (14.3%).
    #
    # This is the convention assertion: a carry-in implementation would report 25% for BOTH
    # slices, because it would remember the 120 that the tail slice does not contain.
    eq = _series([100, 110, 120, 105, 90, 95])
    full = max_drawdown(eq)
    tail = max_drawdown(eq.iloc[3:])
    assert full == pytest.approx(0.25)
    assert tail == pytest.approx((105 - 90) / 105)
    assert tail < full


def test_max_drawdown_accepts_a_persisted_frame():
    # The sweep persists a FRAME (equity, ret, spot, ...). Scoring it must read `equity` and not
    # silently pick a column by position.
    eq = _series([100, 120, 90])
    frame = pd.DataFrame({"spot": [50.0, 50.0, 50.0], "equity": eq.to_numpy()}, index=eq.index)
    assert max_drawdown(frame) == pytest.approx(0.25)


def test_frame_without_equity_raises():
    frame = pd.DataFrame({"spot": [1.0, 2.0]})
    with pytest.raises(KeyError):
        max_drawdown(frame)


# ============================================================================
# THE FLOORED CALMAR (AC-3)
# ============================================================================

def test_calmar_unfloored_uses_the_realised_drawdown():
    # 100 -> 120 -> 90 -> 110: return +10%, max drawdown 25%, both well clear of the floor.
    eq = _series([100, 120, 90, 110])
    score, floor_bound = calmar(eq)
    assert floor_bound is False
    assert score == pytest.approx(0.10 / 0.25)


def test_calmar_floors_a_shallow_drawdown_and_flags_it():
    # A 1% dip is shallower than the 2% floor, so the floor sets the denominator. Without the
    # floor this would read 10 / 0.01 = enormous and would dominate any surface fit.
    eq = _series([100, 99, 110])
    score, floor_bound = calmar(eq)
    assert floor_bound is True
    assert score == pytest.approx(0.10 / DRAWDOWN_FLOOR)


def test_monotone_series_hits_the_floor_and_is_flagged():
    # Zero drawdown is the limiting case of the above: an unfloored Calmar divides by zero.
    eq = _series([100, 105, 110])
    score, floor_bound = calmar(eq)
    assert floor_bound is True
    assert np.isfinite(score)
    assert score == pytest.approx(0.10 / DRAWDOWN_FLOOR)


def test_custom_floor_changes_what_binds():
    # The floor is a named constant and an argument, not a hidden magic number: raising it to 30%
    # makes a 25% realised drawdown floor-bound where the default 2% did not.
    eq = _series([100, 120, 90, 110])
    _, bound_default = calmar(eq)
    _, bound_raised = calmar(eq, drawdown_floor=0.30)
    assert bound_default is False
    assert bound_raised is True


def test_window_return_is_not_annualised():
    # Deliberate: the windows differ in length by a factor of thirty, and annualising a two-month
    # COVID slice produces a number no reader can hold next to a two-year calm window.
    assert window_return(_series([100, 110])) == pytest.approx(0.10)
    assert window_return(_series([100, 101, 102, 110])) == pytest.approx(0.10)


# ============================================================================
# THE TIDY SCORE FRAME
# ============================================================================

def _two_year_paths():
    """Two configurations and a SPY path over the same bars, long enough to slice two windows."""
    idx = pd.bdate_range("2020-01-01", periods=400)
    # SPY: a rise, a 30% crash, a recovery. Built by hand so the drawdown is exactly known.
    spy = pd.Series(np.concatenate([
        np.linspace(100, 120, 100),   # rise
        np.linspace(120, 84, 100),    # -30% from the peak
        np.linspace(84, 130, 200),    # recovery and beyond
    ]), index=idx)
    # "hedged" halves the crash; "unhedged" is SPY itself.
    hedged = pd.Series(np.concatenate([
        np.linspace(100, 118, 100),
        np.linspace(118, 100, 100),
        np.linspace(100, 120, 200),
    ]), index=idx)
    return spy, {"hedged": hedged, "unhedged": spy}


def test_score_grid_one_row_per_config_window_pair():
    spy, series = _two_year_paths()
    windows = {
        "crash": ("2020-05-01", "2020-10-01", DRAWDOWN),
        "recovery": ("2020-10-02", "2021-06-01", CALM),
    }
    out = score_grid(series, spy, windows=windows)
    assert len(out) == len(series) * len(windows)
    assert set(out["config"]) == {"hedged", "unhedged"}
    assert set(out["kind"]) == {DRAWDOWN, CALM}


def test_spy_reference_row_is_computed_on_the_same_bounds():
    # AC-3 requires SPY's own score beside every result. The unhedged configuration IS SPY here,
    # so its score must equal the reference exactly — which is what proves the reference is
    # sliced on the same bounds and not, say, over the whole history.
    spy, series = _two_year_paths()
    windows = {"crash": ("2020-05-01", "2020-10-01", DRAWDOWN)}
    out = score_grid(series, spy, windows=windows).set_index("config")
    assert out.loc["unhedged", "score"] == pytest.approx(out.loc["unhedged", "spy_score"])
    assert out.loc["unhedged", "max_dd"] == pytest.approx(out.loc["unhedged", "spy_max_dd"])


def test_descriptive_columns_are_present_and_do_not_rank():
    # They are computed because "it kept 60% of the loss" is the interpretable sentence a Calmar
    # cannot say. The demotion itself is asserted by the next test, which ranks on `score` alone.
    spy, series = _two_year_paths()
    out = score_grid(series, spy, windows={"crash": ("2020-05-01", "2020-10-01", DRAWDOWN)})
    for col in ("dd_vs_spy", "premium_pct_spot", "dd_reduction_per_premium"):
        assert col in out.columns
    hedged = out.set_index("config").loc["hedged"]
    # The hedge took a shallower drawdown than SPY over the crash window.
    assert hedged["dd_vs_spy"] < 1.0
    # No blotter was supplied, so the premium column is NaN rather than a zero that would read
    # as "the hedge was free".
    assert np.isnan(hedged["premium_pct_spot"])


def test_window_outside_coverage_is_a_nan_row_not_a_dropped_one():
    # A dropped row is indistinguishable from a row that scored badly. A NaN row is not.
    spy, series = _two_year_paths()
    out = score_grid(series, spy, windows={"ancient": ("2007-01-01", "2007-06-01", DRAWDOWN)})
    assert len(out) == len(series)
    assert out["score"].isna().all()


# ============================================================================
# RANKING (AC-4)
# ============================================================================

def test_headline_sorts_by_the_worse_of_the_two_ranks():
    # Three configurations, built so that the average and the worst-of-two disagree:
    #   crisis_only : best in the drawdown set (rank 1), worst in calm (rank 3)
    #   balanced    : second in both (rank 2, rank 2)
    #   calm_only   : worst in drawdown (rank 3), best in calm (rank 1)
    # By AVERAGE rank all three tie at 2.0. By worst-of-two, `balanced` wins outright — which is
    # the behaviour AC-4 asks for, because a hedge that tops the crisis set and bottoms the calm
    # set has not found anything, it has just bought the most expensive protection available.
    rows = []
    for config, dd_score, calm_score in [("crisis_only", 3.0, 1.0),
                                         ("balanced", 2.0, 2.0),
                                         ("calm_only", 1.0, 3.0)]:
        rows.append({"config": config, "window": "w1", "kind": DRAWDOWN,
                     "score": dd_score, "floor_bound": False})
        rows.append({"config": config, "window": "w2", "kind": CALM,
                     "score": calm_score, "floor_bound": False})
    ranked = rank_configs(pd.DataFrame(rows))
    assert ranked.index[0] == "balanced"
    assert ranked.loc["balanced", "worst_rank"] == 2.0
    assert ranked.loc["crisis_only", "worst_rank"] == 3.0
    assert ranked.loc["calm_only", "worst_rank"] == 3.0


def test_ranked_table_carries_the_floor_bound_share():
    # A top row that is mostly floor-bound is reporting the floor, not the strategy. The share
    # travels onto the headline table so that cannot be read off a footnote nobody opens.
    rows = [
        {"config": "a", "window": "w1", "kind": DRAWDOWN, "score": 2.0, "floor_bound": False},
        {"config": "a", "window": "w2", "kind": CALM, "score": 2.0, "floor_bound": True},
        {"config": "b", "window": "w1", "kind": DRAWDOWN, "score": 1.0, "floor_bound": False},
        {"config": "b", "window": "w2", "kind": CALM, "score": 1.0, "floor_bound": False},
    ]
    ranked = rank_configs(pd.DataFrame(rows))
    assert ranked.loc["a", "floor_bound_share"] == pytest.approx(0.5)
    assert ranked.loc["b", "floor_bound_share"] == pytest.approx(0.0)
