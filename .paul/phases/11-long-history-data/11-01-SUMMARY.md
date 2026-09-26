---
phase: 11-long-history-data
plan: 01
type: Summary
about: "Portfolio-Manager"
---

# 11-01 SUMMARY — Reach back decades

## Outcome

All 4 tasks executed. All plan `<verification>` checklist items pass. AC-1 through AC-6
satisfied. `pytest tests/test_history.py -q` — 15 passed offline, no TWS. `pytest -q`
full suite — 182 passed, 1 known pre-existing failure (`ARM_LIVE=True` committed
deliberately as a live-gate reminder per STATE.md Blockers; unrelated to this plan).
`rebalance_study.py` runs clean and unchanged.

## Task 1 — Probe findings (live TWS, port 7497, 2026-09-20)

The assumed pagination ceiling (carried over from Phase 4's executions precedent) does
**not exist** for daily bars on these two symbols:

- SPY TRADES: single request returns full listing history, 1993-02-01 -> today (8464
  rows). Tested up to 60-year requested duration — no refusal, no truncation, no error
  at any length.
- QQQ TRADES: same pattern, 1999-03-11 -> today (6925 rows), exact ETF-inception match.
- yfinance SPY/QQQ: matches IBKR depth to within a day (1993-01-29 / 1999-03-10).
- Pacing: 5 rapid sequential single-symbol requests, no sleep — zero errors, ~2s total.
- **AC-5:** `OPTION_IMPLIED_VOLATILITY` whatToShow gives a genuine (non-synthetic)
  implied-vol history for SPY/QQQ back to **2006-01-09**. `HISTORICAL_VOLATILITY`
  (realised) reaches **2005-02-16**. Not a full strike/tenor surface, but real
  market-implied vol — materially better than the frozen `base=0.16` assumption 12-01
  currently prices with. This determines Phase 12's pricing approach: a real IV level
  series is available and should be used ahead of any synthetic surface for the SPY/QQQ
  legs, once Phase 12 is planned.

## Checkpoint decision (recorded in STATE.md, 2026-09-20)

**ibkr-primary** selected. Since IBKR already reaches full ETF-inception depth with zero
pagination and matches yfinance to the day, `free-primary` and `both-parallel` were
rejected as adding work for no new information.

## Files changed

- `src/portutils/ingestion/history.py` (NEW) — `fetch_ibkr_long_history` (paginated,
  stitch-overlap assertion), `fetch_yfinance_fallback`, `fetch_long_history` (merges
  the two with per-row `<symbol>_source` provenance), `find_coverage_gaps`.
- `src/pipelines/cache_prices.py` — `_tidy(df, ragged=False)` now takes a `ragged` flag
  (Step 0 of Task 4); `from_csv`/`from_ibkr` thread it through; new `from_long_history`
  helper and `--long-history` / `--ragged` CLI flags. Default behaviour unchanged.
- `src/pipelines/coverage_report.py` (NEW) — generates `data/processed/COVERAGE.md`
  from the cached panel + provenance CSV; `build_coverage_report` is the pure formatter
  tests exercise directly.
- `config/asset_universe.yaml` — added `QQQ` ticker metadata and the `long_history`
  named portfolio (SPY 0.5 / QQQ 0.5), per the 2026-08-31 ETF-proxy decision.
- `src/portutils/viz/panel.py` — `PanelBuilder.__init__` gains `ragged=False` (default
  preserves today's outer-join -> ffill -> dropna exactly); `_load` branches on it;
  `_normalise`/`_pct_returns` thread an `anchor` parameter through to `analysis.returns`.
- `src/portutils/analysis/returns.py` — `normalise`/`pct_returns` gain `anchor`
  ("first_row" default / "common" / "self" / explicit `pd.Timestamp`), with a named
  error (not a bare `IndexError`) when `"common"` finds no overlapping date.
  `daily_returns`/`log_returns` left unchanged with a comment explaining why (per-series
  `pct_change()` is already correct on ragged input).
- `src/portutils/viz/dash_timeseries_app.py` — `TimeSeriesAppConfig.reindex` now also
  accepts `"common"` / `"self"` / a `pd.Timestamp` alongside its original bool; bool
  `True`/`False` keep their exact prior meaning, verified via `make_level_figure`.
- `tests/test_history.py` (NEW) — 15 offline tests: stitch-overlap agree/disagree,
  provenance across a mixed-source series, deliberate-gap detection, coverage-report
  episode statements, cache-layer ragged retention, `PanelBuilder` default-vs-ragged
  loading, and the `normalise` anchor policy (`first_row`/`common`/`self`/error case).
- `data/processed/prices_long_history.parquet`, `provenance_long_history.csv`,
  `COVERAGE.md` (NEW) — the actual long-history cache. States 2008/2020/2022 as
  **COVERED** for both SPY and QQQ. One internal gap detected and named: 2001-09-10 to
  2001-09-17 (the post-9/11 market closure).

## Verified unchanged (AC-4 / AC-6 byte-identity)

- `git status` clean on the three protected parquets (`spy_kmlm`, `hedge_rotation`,
  `holdings`) — never touched.
- Re-ran `spy_kmlm` via `--from-csv` under a different tag and diffed against the
  original: `DataFrame.equals() == True`.
- `rebalance_study.py` ran clean, output structurally unchanged.
- `normalise(df)` with no `anchor` argument asserted bit-identical to the old
  unconditional `df.iloc[0]` formula.

## Deviations from plan

- None structural. `history.py` is function-based rather than class-based — the plan
  did not mandate a shape, and functions match the existing `ibkr_requests.py` /
  `cache_prices.py` style (module-level functions, no class wrapper for a stateless
  fetch).

## Scope boundaries respected

No option pricing, no walk-forward code, no regime labelling — all deferred to Phases
12-14 as scoped. No new paid data subscription used (yfinance was already declared).
