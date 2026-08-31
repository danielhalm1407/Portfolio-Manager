---
description: "Phase 11 discussion context — what history we already hold, and how to reach the maximum IBKR will serve"
phase: 11-long-history-data
type: Context
about: "Portfolio-Manager"
---

# Phase 11 — Discussion Context

Written 2026-08-31 from `/paul:discuss phase 11-01`. This sits ABOVE the existing
[11-01-PLAN.md](11-01-PLAN.md), which was created 2026-08-24 and is still awaiting approval. It
records the survey the user asked for (what SPX/NDX history the repo already holds, and how a
maximum-history request is actually made) plus one finding that was not in the plan.

---

## 1. What we already have — measured, not assumed

Every price file in the repo was inventoried on 2026-08-31. There are exactly three parquets and
27 CSVs, and **nothing reaches beyond a single year.**

### Processed parquets — `data/processed/`

| File | Shape | Index span | Columns |
|---|---|---|---|
| `prices_spy_kmlm.parquet` | 250 × 2 | 2025-07-28 → 2026-07-24 | SPY, KMLM |
| `prices_hedge_rotation.parquet` | 250 × 6 | 2025-07-28 → 2026-07-24 | KMLM, MNA, FLSP, FTXL, JPM, SPY |
| `prices_holdings.parquet` | 256 × 4 | 2025-07-28 → 2026-07-24 | AINF, BARC, HSBA, LNG |

### Raw CSVs — `data/raw/market/`

| File group | Rows | Span |
|---|---|---|
| `portfolio_prices.csv` (wide, 14 symbols) | 250 | 2025-07-28 → 2026-07-24 |
| `SPY.csv` / `stock_data_SPY.csv` and 11 other single-name pairs | 249 | 2025-04-14 → 2026-04-10 |
| `WMT.csv` / `stock_data_WMT.csv` | 248 | 2025-04-15 → 2026-04-10 |

`SPY.csv` and `stock_data_SPY.csv` are byte-identical duplicates — the same pull written under both
of `get_equity_data`'s naming conventions.

### Answering the two questions directly

**SPX.** There is no SPX series in the repo at all. The S&P 500 exposure exists only as **SPY**, the
ETF proxy. The earliest SPY bar anywhere in `data/` is **2025-04-14** (`SPY.csv`, 249 rows). The
parquet the study actually reads starts later still, **2025-07-28**.

**NDX.** There is **nothing**. A case-insensitive search for `NDX`, `QQQ` and `SPX` across `data/`
and `config/` returns zero hits. The Nasdaq-100 leg does not exist as a ticker, a column, a file or
a universe entry. `data/reference/` is an empty directory.

So the position is worse than the roadmap's "250 rows over one regime" summary implies: for the
index leg the deepest history is **~1 year of SPY**, and the second index leg the milestone will
want has **no history at all**.

---

## 2. How to request maximum history for SPX and NDX

The mechanics are documented in full, with line links, in
[`ibkr_requests.md`](../../../src/portutils/ingestion/ibkr_requests.md) (written in the same
session). The Phase-11-relevant conclusions:

### 2a. Use the ETF proxies unless there is a reason not to

`contract()` ([`ibkr_requests.py:124`](../../../src/portutils/ingestion/ibkr_requests.py#L124))
defaults to `secType='STK'`, `exchange='SMART'`, `currency='USD'`. **SPY** and **QQQ** match those
defaults exactly and need no `contract_specs` entry. The true indices do:

```python
contract_specs = {
    'SPX': {'sec_type': 'IND', 'exchange': 'CBOE'},
    'NDX': {'sec_type': 'IND', 'exchange': 'NASDAQ'},
}
```

Indices are quoted, not traded, so `what_to_show='TRADES'` is the wrong basis for them and index
data sits behind separate market-data entitlements. The ETFs carry a trade tape and are what the
account could actually hold. **Recommendation: proxy with SPY/QQQ, and treat SPX/NDX index history
as a probe question rather than a build assumption.** ETF inception is the hard floor either way —
SPY lists 1993, QQQ lists 1999 — so an ETF-only panel cannot reach 1990 no matter what TWS serves.

### 2b. The request itself

```python
from portutils.ingestion.ibkr_requests import get_equity_data

df = get_equity_data(
    symbols=['SPY', 'QQQ'],
    duration='30 Y',            # ← the unverified number; see 2c
    bar_size='1 day',
    what_to_show='TRADES',
    output_format='combined',   # wide Date + one close column per symbol
    output_dir=None,            # nothing written to disk
)
```

### 2c. The duration ceiling is UNVERIFIED and must stay that way until probed

`duration` is passed straight through to `reqHistoricalData` as `durationStr`
([`ibkr_requests.py:1395`](../../../src/portutils/ingestion/ibkr_requests.py#L1395)). Nothing in
this repo validates or caps it, and nothing here has ever asked for more than `'1 Y'` — the default
at [`ibkr_requests.py:1251`](../../../src/portutils/ingestion/ibkr_requests.py#L1251) and the
`--duration` default at [`cache_prices.py:102`](../../../src/pipelines/cache_prices.py#L102).

Whether TWS answers `'30 Y'`, refuses it, or silently returns a shorter series **has not been
tested against this account**. Per the standing project decision (2026-08-02, PROJECT.md), the
running TWS outranks every written source, and Phase 4 lost two plans to a documented limit that
proved wrong. This is exactly what 11-01 Task 1 exists to establish. Nothing in this document
should be read as a claim about the real ceiling.

The three failure modes worth watching for, because `get_equity_data` distinguishes them:

- **Refusal** — `error()` records it in `req_errors` and sets the waiting event itself, so the
  symbol comes back as `Skipped SPY: IB error <code> - <text>`
  ([`ibkr_requests.py:1438-1442`](../../../src/portutils/ingestion/ibkr_requests.py#L1438-L1442)).
  Capture that text verbatim — it seeds 11-02.
- **Silent truncation** — bars arrive, `historicalDataEnd` fires, and the series is simply shorter
  than asked for. Nothing prints. This is the dangerous one, and the only detection is comparing
  the returned first bar against the requested start.
- **Timeout** — 30s per symbol
  ([`ibkr_requests.py:1421`](../../../src/portutils/ingestion/ibkr_requests.py#L1421)); the symbol
  is skipped and the others still return.

### 2d. Getting a parquet out of it

`get_equity_data` **cannot write a parquet.** It writes CSV only —
`stock_data_{sym}.csv` per symbol ([`:1480`](../../../src/portutils/ingestion/ibkr_requests.py#L1480))
or one merged CSV ([`:1553`](../../../src/portutils/ingestion/ibkr_requests.py#L1553)). There is no
argument that changes this.

Parquet is produced one layer up, by `cache_prices.py`, which calls `get_equity_data`, tidies the
frame and writes `data/processed/prices_<tag>.parquet`
([`cache_prices.py:121`](../../../src/pipelines/cache_prices.py#L121)):

```bash
python src/pipelines/cache_prices.py --portfolio <name> --duration "30 Y" --bar-size "1 day"
```

`--portfolio` resolves its tickers from `config/asset_universe.yaml`, so **a long-history universe
containing SPY and QQQ has to be added to that YAML first** — the pipeline deliberately refuses to
take a hardcoded ticker list from anywhere else. `--symbols` exists as an override but bypasses the
convention 11-01 Task 2 is meant to follow.

Why parquet rather than CSV for the processed layer: typed columns survive the round trip (the raw
CSVs store dates as bare integers like `20250414` and every column as text), the `DatetimeIndex`
and its `ts` name persist, the format is columnar and compressed so a wide multi-decade panel reads
a single symbol without parsing the rest, and float precision is exact rather than reformatted.
CSV keeps its place in `data/raw/` because that layer is append-only, human-diffable and is what a
future `--from-csv` run replays.

---

## 3. New finding — a SECOND ragged-truncation site, not in the plan

The 2026-08-31 blocker in ROADMAP.md names one location: `PanelBuilder._load`
([`viz/panel.py:165-167`](../../../src/portutils/viz/panel.py#L165-L167)), whose outer join →
`ffill` → `dropna` truncates the whole panel to the latest first-bar across tickers.

**`cache_prices.py` has the identical defect, and it runs first.** `_tidy`
([`cache_prices.py:45`](../../../src/pipelines/cache_prices.py#L45)) ends at
[`:55`](../../../src/pipelines/cache_prices.py#L55) with:

```python
out = out.astype(float).ffill().dropna(how="any")
```

So a SPY-from-1993 series cached beside a QQQ-from-1999 series is truncated to **1999 in the
parquet itself** — before `PanelBuilder` is ever constructed, and before `COVERAGE.md` is
generated. Fixing only the viz loader would leave the data loss baked into the file that
`COVERAGE.md` describes, which defeats AC-3 and AC-6 together.

Note the two sites have different risk profiles, and the 2026-08-31 "keep the current behaviour as
the default" decision applies cleanly to only one of them:

- `panel.py` is read by existing figures, so an opt-in mode is the right shape — that is 11-01
  Task 4 as written.
- `cache_prices.py` **writes new files under new tags**. A long-history tag has no existing reader
  to regress, so ragged retention can be the behaviour for new panels without any compatibility
  argument. The three existing parquets are protected by the plan's DO-NOT-CHANGE list regardless.

`_tidy`'s own comment already states the intent — *"drop any leading rows still missing a quote —
the sim must never mark a leg at a made-up price"* — which is correct for a simulator and wrong for
a coverage report. Both needs are real; they are not the same frame.

**Consequence for 11-01:** `src/pipelines/cache_prices.py` is already in `files_modified`, but Task
4 names only `panel.py`, `returns.py` and `dash_timeseries_app.py`. Task 4 should gain
`cache_prices.py::_tidy`, or AC-6 should be extended to cover the cache layer explicitly.

---

## 4. Goals for the phase, as discussed

1. **Know what we hold before fetching anything.** Done, and recorded in §1 — the answer is ~1 year
   of SPY and no NDX at all.
2. **Reach the maximum history IBKR will actually serve for the two index legs**, with the ceiling
   established by probe rather than by documentation.
3. **Land it as a parquet** under `data/processed/`, through `cache_prices.py`, with the
   long-history universe declared in `config/asset_universe.yaml`.
4. **Document the request path itself** — done, as
   [`ibkr_requests.md`](../../../src/portutils/ingestion/ibkr_requests.md), covering historical and
   market-data requests: what they take, how the threading handshake runs, what they return, how
   the CSV/parquet split works and why.
5. **Do not let a cached span the figures cannot show be reported as coverage** — now a two-site
   fix, per §3.

## 5. Open questions

- **Proxy or index?** SPY/QQQ (tradeable, trade tape, no extra entitlement, but capped at 1993/1999
  inception) versus SPX/NDX (further back in principle, `secType='IND'`, entitlement-dependent,
  not tradeable). Task 1 can probe both cheaply; the choice belongs in the existing blocking
  checkpoint alongside the source split.
- **Is ETF inception an acceptable floor?** 11-01's stated success measure is 2008, 2020 and 2022,
  all of which SPY (1993) and QQQ (1999) clear comfortably. 1990 and the 2000 dot-com peak are
  reachable for SPY-as-index only via a free source or the index itself. Whether 2000 is in scope
  changes the source-split decision.
- **Does the vol leg still start at VIX?** AC-5 is unchanged by this survey — no vol series of any
  kind exists in `data/` today.
- **Task 4 scope** — extend to `cache_prices.py::_tidy`, or split the cache-layer fix out. §3
  argues for extending, since the two halves fail independently.

---

*Created 2026-08-31 by `/paul:discuss`. Feeds `/paul:plan` — or, since 11-01 already exists, an
amendment to 11-01 Task 4 and AC-6.*
