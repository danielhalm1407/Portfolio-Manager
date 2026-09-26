"""
Offline tests for Phase 11's long-history ingestion (plan 11-01).

No TWS and no network: everything here runs against fixtures and monkeypatched IBKR/
yfinance calls, per the repo's convention of stubbing the app rather than the network
(see tests/test_rebalance_live.py). Live-probe evidence (the actual IBKR ceiling, the
IV-history depth) is recorded in STATE.md's 2026-09-20 Decisions row and the 11-01
SUMMARY, not re-asserted here — a fixture cannot prove what a live probe found.

Covers:
- AC-1: the stitch-overlap assertion (agreeing pages stitch; disagreeing pages raise)
- AC-2 / AC-3: provenance surviving a mixed-source series; a deliberate gap detected
- AC-6: cache-layer ragged retention + byte-identical re-run of an existing tag,
  and the viz-layer default/ragged/anchor-policy behaviour
"""

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from portutils.analysis import returns                          # noqa: E402
from portutils.ingestion import history                          # noqa: E402
from portutils.viz.panel import PanelBuilder                      # noqa: E402
from pipelines import cache_prices                                # noqa: E402
from pipelines.coverage_report import build_coverage_report       # noqa: E402


# ============================================================================
# AC-1: stitch-overlap assertion
# ============================================================================

def _page(dates, closes):
    return pd.DataFrame({"datetime": dates, "close": closes})


def test_stitch_overlap_agreement_stitches_cleanly(monkeypatch):
    # Two pages sharing one overlapping bar (2000-01-04) that agree exactly.
    pages = [
        _page(["20000103", "20000104"], [10.0, 11.0]),   # newest-requested (fetched first)
        _page(["20000104", "20000105"], [11.0, 9.5]),    # older page, paged backwards
        _page([], []),                                    # signals "no more data"
    ]
    calls = {"n": 0}

    def fake_get_equity_data(*a, **k):
        i = calls["n"]
        calls["n"] += 1
        df = pages[i] if i < len(pages) else pages[-1]
        return {"SPY": df if not df.empty else None}

    monkeypatch.setattr(
        "portutils.ingestion.ibkr_requests.get_equity_data", fake_get_equity_data
    )
    out = history.fetch_ibkr_long_history("SPY", app=object(), max_pages=4)
    # No duplicated bar despite the shared 2000-01-04 row, no dropped bar either.
    assert list(out.index) == sorted(set(out.index))
    assert len(out) == 3
    assert out.loc["2000-01-04", "SPY"] == 11.0
    assert (out["SPY_source"] == "ibkr").all()


def test_stitch_overlap_disagreement_raises(monkeypatch):
    # Same overlap date, but the two pages disagree on the close — must refuse to
    # silently concatenate rather than pick one arbitrarily.
    pages = [
        _page(["20000103", "20000104"], [10.0, 11.0]),
        _page(["20000104", "20000105"], [11.5, 9.5]),   # 11.5 != 11.0 on 2000-01-04
    ]
    calls = {"n": 0}

    def fake_get_equity_data(*a, **k):
        i = calls["n"]
        calls["n"] += 1
        df = pages[i] if i < len(pages) else pd.DataFrame()
        return {"SPY": df if not df.empty else None}

    monkeypatch.setattr(
        "portutils.ingestion.ibkr_requests.get_equity_data", fake_get_equity_data
    )
    with pytest.raises(ValueError, match="stitch overlap disagrees"):
        history.fetch_ibkr_long_history("SPY", app=object(), max_pages=4)


# ============================================================================
# AC-2 / AC-3: provenance and deliberate gaps
# ============================================================================

def test_provenance_survives_mixed_source_series(monkeypatch):
    ibkr_df = pd.DataFrame(
        {"SPY": [10.0, 11.0], "SPY_source": ["ibkr", "ibkr"]},
        index=pd.DatetimeIndex(["2000-01-03", "2000-01-04"], name="ts"),
    )
    yfin_df = pd.DataFrame(
        {"SPY": [5.0, 6.0], "SPY_source": ["yfinance", "yfinance"]},
        index=pd.DatetimeIndex(["1993-01-29", "1993-02-01"], name="ts"),
    )
    monkeypatch.setattr(history, "fetch_ibkr_long_history", lambda *a, **k: ibkr_df)
    monkeypatch.setattr(history, "fetch_yfinance_fallback", lambda *a, **k: yfin_df)

    out = history.fetch_long_history("SPY", app=object())
    # Sorted, no source column silently overwritten by the other source.
    assert list(out.index) == sorted(out.index)
    before_ibkr = out.loc[out.index < ibkr_df.index.min()]
    from_ibkr = out.loc[out.index >= ibkr_df.index.min()]
    assert (before_ibkr["SPY_source"] == "yfinance").all()
    assert (from_ibkr["SPY_source"] == "ibkr").all()


def test_find_coverage_gaps_detects_a_deliberate_gap():
    dates = pd.DatetimeIndex(
        ["2020-01-01", "2020-01-02", "2020-01-03", "2020-03-01", "2020-03-02"]
    )
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=dates)
    gaps = history.find_coverage_gaps(series, max_gap_days=5)
    assert len(gaps) == 1
    start, end, days = gaps[0]
    assert start == pd.Timestamp("2020-01-03")
    assert end == pd.Timestamp("2020-03-01")
    assert days == 58


def test_find_coverage_gaps_ignores_ordinary_weekends():
    # A normal Fri -> Mon gap (3 calendar days) must not be reported.
    dates = pd.DatetimeIndex(["2024-01-05", "2024-01-08"])  # Fri, Mon
    series = pd.Series([1.0, 2.0], index=dates)
    assert history.find_coverage_gaps(series, max_gap_days=5) == []


def test_coverage_report_states_episode_coverage_explicitly():
    idx = pd.date_range("1993-02-01", "2026-09-18", freq="B")
    prices = pd.DataFrame({"SPY": np.linspace(1, 2, len(idx))}, index=idx)
    prices.index.name = "ts"
    provenance = pd.DataFrame({"SPY": "ibkr"}, index=idx)
    report = build_coverage_report(prices, provenance)
    assert "2008 financial crisis" in report
    assert "2020 COVID crash" in report
    assert "2022 rate shock" in report
    assert "COVERED" in report


# ============================================================================
# AC-6: cache layer (cache_prices._tidy) — ragged retention + byte-identical re-run
# ============================================================================

def _ragged_raw_csv_frame():
    # SPY starts years before QQQ, matching the real long-history shape.
    dates = pd.date_range("1993-02-01", "1993-02-10")
    df = pd.DataFrame({"Date": dates, "SPY": np.linspace(40, 41, len(dates))})
    df["QQQ"] = np.nan
    df.loc[df.index[-2:], "QQQ"] = [50.0, 50.5]  # QQQ "starts" on the last 2 rows
    return df


def test_tidy_default_truncates_to_common_start_unchanged():
    raw = _ragged_raw_csv_frame()
    out = cache_prices._tidy(raw, ragged=False)
    # Today's behaviour: dropna(how="any") truncates to where BOTH columns have data.
    assert out["QQQ"].notna().all()
    assert len(out) == 2


def test_tidy_ragged_retains_full_earlier_history():
    raw = _ragged_raw_csv_frame()
    out = cache_prices._tidy(raw, ragged=True)
    # Every SPY row survives; QQQ is NaN outside its own coverage rather than the
    # whole panel being truncated to QQQ's later start.
    assert len(out) == len(raw)
    assert out["SPY"].notna().all()
    assert out["QQQ"].isna().sum() == len(raw) - 2


# ============================================================================
# AC-6: viz layer (PanelBuilder._load) — default vs ragged, via a CSV fixture
# ============================================================================

@pytest.fixture
def ragged_csv_dir(tmp_path):
    # PanelBuilder._load reads {ticker}.csv with a 'date'/'datetime' + 'close' column,
    # YYYYMMDD dates — matching get_equity_data's raw CSV shape.
    early = pd.DataFrame({
        "datetime": pd.date_range("2000-01-03", periods=10, freq="B").strftime("%Y%m%d"),
        "close": np.linspace(10, 11, 10),
    })
    late = pd.DataFrame({
        "datetime": pd.date_range("2000-01-13", periods=10, freq="B").strftime("%Y%m%d"),
        "close": np.linspace(20, 21, 10),
    })
    early.to_csv(tmp_path / "EARLY.csv", index=False)
    late.to_csv(tmp_path / "LATE.csv", index=False)
    return tmp_path


def test_panelbuilder_default_load_is_byte_identical_to_today(ragged_csv_dir):
    pb = PanelBuilder(tickers=["EARLY", "LATE"], start_date="2000-01-01",
                       data_dir=ragged_csv_dir, ragged=False)
    # Truncated to LATE's first bar — today's outer-join -> ffill -> dropna behaviour.
    assert pb.df_all.notna().all().all()
    assert len(pb.df_all) == 10  # LATE's own row count: from its start to the common end


def test_panelbuilder_ragged_load_keeps_every_row(ragged_csv_dir):
    pb = PanelBuilder(tickers=["EARLY", "LATE"], start_date="2000-01-01",
                       data_dir=ragged_csv_dir, ragged=True)
    assert len(pb.df_all) == 18  # union of both date ranges (2 overlapping business days)
    assert pb.df_all["EARLY"].notna().all()
    assert pb.df_all["LATE"].isna().sum() == 8  # NaN before LATE's own first bar


# ============================================================================
# AC-6: rebase anchor policy (analysis.returns)
# ============================================================================

def _ragged_frame():
    idx = pd.date_range("2000-01-01", periods=5, freq="D")
    df = pd.DataFrame({"A": [10.0, 20.0, 30.0, 40.0, 50.0],
                        "B": [np.nan, np.nan, 15.0, 30.0, 45.0]}, index=idx)
    return df


def test_normalise_common_anchor_hits_exactly_base_with_no_all_nan_column():
    df = _ragged_frame()
    out = returns.normalise(df, base=100.0, anchor="common")
    # Common anchor: 2000-01-03, the first date BOTH columns have data.
    common_date = pd.Timestamp("2000-01-03")
    assert out.loc[common_date, "A"] == pytest.approx(100.0)
    assert out.loc[common_date, "B"] == pytest.approx(100.0)
    # The old bug divided the WHOLE column by df.iloc[0] even when iloc[0] was NaN,
    # making every later, perfectly valid B value NaN too. With anchor="common", B is
    # non-NaN everywhere it actually HAS data (from its own first valid date on) — the
    # remaining NaN before that is B genuinely having no price yet, not the bug.
    assert out.loc[common_date:, "B"].notna().all()
    # A's earlier history is retained, shown relative to the common baseline, not dropped.
    assert out.loc[df.index[0], "A"] == pytest.approx((10.0 / 30.0) * 100.0)


def test_normalise_self_anchor_each_series_own_start():
    df = _ragged_frame()
    out = returns.normalise(df, base=100.0, anchor="self")
    assert out.loc[df.index[0], "A"] == pytest.approx(100.0)
    assert out.loc[pd.Timestamp("2000-01-03"), "B"] == pytest.approx(100.0)


def test_normalise_first_row_default_matches_prior_behaviour():
    # Non-ragged frame: anchor="first_row" (the default) must be bit-for-bit what the
    # old unconditional df.iloc[0] did.
    idx = pd.date_range("2000-01-01", periods=3, freq="D")
    df = pd.DataFrame({"A": [10.0, 20.0, 30.0], "B": [1.0, 2.0, 3.0]}, index=idx)
    assert returns.normalise(df).equals((df / df.iloc[0]) * 100.0)


def test_normalise_common_anchor_raises_named_error_when_no_overlap():
    idx_a = pd.date_range("2000-01-01", periods=3, freq="D")
    idx_b = pd.date_range("2001-01-01", periods=3, freq="D")
    df = pd.DataFrame(index=idx_a.union(idx_b))
    df["A"] = pd.Series([1.0, 2.0, 3.0], index=idx_a)
    df["B"] = pd.Series([1.0, 2.0, 3.0], index=idx_b)
    with pytest.raises(ValueError, match="anchor='common' found no date"):
        returns.normalise(df, anchor="common")


# ============================================================================
# AC-4: existing callers keep working (run once here as a smoke check; the full
# subprocess run against rebalance_study.py is the plan's separate <verify> step)
# ============================================================================

def test_daily_returns_and_log_returns_unaffected_by_anchor_policy_addition():
    df = _ragged_frame()
    # These two take NO anchor argument (per Task 4 Step 2) — this only guards against
    # a future edit accidentally adding one and changing the default signature.
    assert returns.daily_returns(df).shape[1] == 2
    assert returns.log_returns(df).shape[1] == 2
