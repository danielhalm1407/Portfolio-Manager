# Fix: `get_equity_data` hangs + rejects non-US holdings

Repo copy (canonical): `.claude/plans/ibkr-hist-nonus-contracts.md` — rename the mirrored file to that.

## Context

Cell 7 of [research/check_existing_port.py](../../research/check_existing_port.py#L161-L179) dies with:

```
IB Error 1: ... - No security definition has been found for the request
IB Error 3: ... - No security definition has been found for the request
IB Error 2: ... - No security definition has been found for the request
IB Error 4: ... - No security definition has been found for the request
TimeoutError: Timed out waiting for IBKR historical data for 5MVL.
```

Two independent defects, both in [src/portutils/ingestion/ibkr_requests.py](../../src/portutils/ingestion/ibkr_requests.py):

1. **Wrong contract.** `get_equity_data` builds every contract with `contract(sym)` ([L1110](../../src/portutils/ingestion/ibkr_requests.py#L1110)), whose defaults are hardcoded `STK / SMART / USD` ([L124-L144](../../src/portutils/ingestion/ibkr_requests.py#L124-L144)). The account holds non-US listings (`5MVL` is an LSE line), so TWS cannot resolve them — **all four** reqIds were rejected, not just one. The account snapshot already carries the exact answer: `updatePortfolio` stores `conId`, `secType`, `exchange`, `currency` per holding ([L818-L832](../../src/portutils/ingestion/ibkr_requests.py#L818-L832)) and `get_account_updates` returns them as DataFrame columns ([L1424-L1440](../../src/portutils/ingestion/ibkr_requests.py#L1424-L1440)) — they are simply thrown away.

2. **A rejected request hangs, then kills the batch.** `IBApp.error` only prints ([L616-L635](../../src/portutils/ingestion/ibkr_requests.py#L616-L635)); it never sets the request's `threading.Event`. `historicalDataEnd` never fires for a rejected reqId, so `_wait_for` ([L243-L251](../../src/portutils/ingestion/ibkr_requests.py#L243-L251)) blocks the full 30 s and then raises `TimeoutError`, aborting the whole pull. One bad ticker takes down every good one. Same exposure in `get_historical_bars` ([L1258-L1298](../../src/portutils/ingestion/ibkr_requests.py#L1258-L1298)) and every other `_wait_for` caller.

Outcome wanted: cell 7 returns bars for whatever TWS can serve, names anything it cannot, and never blocks 30 s per bad symbol.

## Changes

### 1. `error()` — fail fast, record why

[ibkr_requests.py L616](../../src/portutils/ingestion/ibkr_requests.py#L616)

- Add `self.req_errors = {}` next to `self._hist_events` in `__init__` ([L424](../../src/portutils/ingestion/ibkr_requests.py#L424)).
- Keep the existing 2104/2106/2158/2176 swallow and the print verbatim.
- After printing: if `reqId >= 0` **and** `errorCode < 2100` (the 2100+ band is advisory — data-farm state, delayed-data notices — and does *not* terminate a request), treat it as terminal: store `req_errors[reqId] = (errorCode, errorString)` under `self.lock`, then set whichever pending event matches that reqId — `_hist_events`, `_account_events`, `_pnl_events`.
- Comment the 2100 boundary and *why* the event must be set here: TWS sends **no** `*End` callback for a rejected request, so without this the waiter blocks until timeout.

### 2. `contract()` — accept a conId

[L124](../../src/portutils/ingestion/ibkr_requests.py#L124). Add `con_id=None`; when set, assign `c.conId`. A conId is venue- and currency-unambiguous, so it resolves an LSE line that `SMART/USD` cannot. Symbol still set for readability in logs.

### 3. New helper `contract_specs_from_portfolio(portfolio_df)`

Near `contract()`. Pure, no side effects. Maps each row of a `get_account_updates` frame to `{symbol: {"con_id":…, "sec_type":…, "currency":…, "exchange": row.exchange or "SMART"}}`. Skips zero/blank rows. This is the piece that makes the holdings resolvable at all.

### 4. `get_equity_data` — per-symbol contracts, partial success

[L990](../../src/portutils/ingestion/ibkr_requests.py#L990)

- New kwarg `contract_specs=None` (dict symbol → kwargs for `contract()`); `contract(sym, **contract_specs.get(sym, {}))` at [L1110](../../src/portutils/ingestion/ibkr_requests.py#L1110). Default `None` → today's behaviour, so `cache_prices.py`, `hedge_sleeves.py`, `daily_ingest.py` and the notebook are untouched.
- Clear `app.req_errors` for the ids about to be issued.
- Replace the bare `_wait_for` loop at [L1132-L1137](../../src/portutils/ingestion/ibkr_requests.py#L1132-L1137) with per-symbol `try/except TimeoutError` → print `skipped {sym}: timed out` and continue. After the wait, if `req_errors` holds that reqId, print `skipped {sym}: {code} {msg}` and continue. The collection loop already tolerates empty results ([L1151-L1154](../../src/portutils/ingestion/ibkr_requests.py#L1151-L1154)) and the combined merge already skips empty frames ([L1191-L1193](../../src/portutils/ingestion/ibkr_requests.py#L1191-L1193)).
- Print a one-line summary: `fetched N/M symbols; missing: [...]` — silent partial data is worse than loud partial data.
- New kwarg `what_to_show='TRADES'`, threaded into `reqHistoricalData` ([L1121](../../src/portutils/ingestion/ibkr_requests.py#L1121)). Non-US ETFs frequently have no TRADES entitlement; `MIDPOINT` is then the working basis. Kwarg only — no automatic retry, so the failure stays visible.

### 5. Cell 7 of `check_existing_port.py`

[research/check_existing_port.py L161-L179](../../research/check_existing_port.py#L161-L179)

- Pass `app=ib_app`. Today the call omits it, so `_ensure_connected_app` opens a **second** TWS connection on the default client id (that is the stray `Connected/Disconnected from IBKR` pair in the output) — a silent client-id collision risk the repo warns about at [L65-L68](../../research/check_existing_port.py#L65-L68).
- Pass `contract_specs=ib.contract_specs_from_portfolio(portfolio_df)`.
- Build the frame from the columns that actually came back, and print any holding with no price history. Downstream is already safe: `backcast.run` filters to `prices.columns` ([backcast.py L288](../../src/portutils/portfolio/backcast.py#L288), [L100](../../src/portutils/portfolio/backcast.py#L100)), and cells 10/11 key off `state.columns` / `path.columns`.
- Note in the cell comment that a holding without price history is excluded from the backcast — so the verdict is about the priced subset only.

Comments throughout in the `orders/kts.py` house style: header block on the new helper, a *why* line above each non-obvious step. No existing comment deleted.

## Verification

1. `~/miniconda3/envs/venv-stats/python.exe -c "import portutils.ingestion.ibkr_requests"` — imports clean.
2. Offline unit check of the pure parts: `contract_specs_from_portfolio` on a hand-built DataFrame; `contract(sym, con_id=123, exchange='LSE', currency='GBP')` sets `conId`/`exchange`/`currency`.
3. TWS running, re-run cell 3 then cell 7. Expect: no `No security definition` for the held lines, no 30 s stall, `fetched 4/4` (or an explicit named skip), parquet written to `data/processed/prices_holdings.parquet`.
4. Negative test: append a junk ticker (`"ZZZZNOTREAL"`) to `symbols` — must print `skipped ZZZZNOTREAL: 200 …` within ~1 s and still return the real symbols.
5. Regression: `python src/pipelines/cache_prices.py` (US tickers, no `contract_specs`) behaves exactly as before.
6. `python -m pytest tests/ -q`.

## Progress Log

| # | Step | Status |
|---|------|--------|
| 1 | `error()` sets pending events + `req_errors` | done — 2026-07-26 |
| 2 | `contract(con_id=…)` | done — 2026-07-26 |
| 3 | `contract_specs_from_portfolio` (+ exported from `portutils.ingestion`) | done — 2026-07-26 |
| 4 | `get_equity_data` specs + partial success + `what_to_show` | done — 2026-07-26 |
| 5 | cell 7 rewired (`app=ib_app`, specs, unpriced-holding report) | done — 2026-07-26 |
| 6 | Offline verification 1, 2, 4, 6 | done — 2026-07-26 |
| 7 | Live re-run of cell 7 against TWS (verification 3, 5) | **pending — needs TWS** |

### Verification results (offline, 2026-07-26)

- `contract("5MVL", con_id=123456, exchange="LSE", currency="GBP")` → `conId=123456 / LSE / GBP`; `contract("SPY")` unchanged (`0 / SMART / USD`).
- `contract_specs_from_portfolio` on a hand-built frame: blank exchange → `SMART`, NaN conId omitted, empty frame → `{}`.
- Rejection path, `FakeTWS` subclass firing `error(reqId, 200, ...)`: **0.29 s**, not 30 s — `Skipped 5MVL: IB error 200 …`, `Fetched 1/2 symbols.`, and SPY's bars still returned. Was: `TimeoutError`, whole batch lost.
- Silent-request path (no callback at all): 30.4 s, `Skipped GHOST: timed out`, batch still returns SPY.
- `pytest tests/ -q` → **52 passed**. `py_compile` clean on the three touched files.

## Decisions

- 2026-07-26 — Resolve holdings by **conId from the account snapshot**, not by guessing exchange/currency per symbol. The broker already told us the contract; asking TWS to re-resolve a bare symbol is the bug.
- 2026-07-26 — A rejected symbol is a **warning, not an abort**. The 30 s-per-bad-symbol stall was the real cost of routing rejections through the timeout path.
- 2026-07-26 — `what_to_show` stays a kwarg with no auto-retry, so an entitlement gap surfaces instead of being papered over.
