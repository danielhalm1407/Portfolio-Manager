# Multi-currency live rebalancer — dry-run first, then live

Repo copy (canonical): `.claude/plans/rebalance-live-multicurrency.md` — rename the mirrored file to that.

## Context

The account holds `5MVL, AINF, BARC, HSBA, LNG` — London lines quoted in pence alongside US lines in dollars. [rebalance_live.py](../../src/pipelines/rebalance_live.py) was written for the two-ticker `spy_kmlm` study and breaks on every one of them. Run today it does **nothing at all**: `build_orders` iterates `sorted(weights)` = `SPY, KMLM` ([L107](../../src/pipelines/rebalance_live.py#L107)), neither is held, so neither has a `marketPrice` row and both come back `SKIP no mark` → "nothing to trade".

Four defects sit behind that, and each would produce a *wrong order* rather than no order once the universe is fixed:

1. **Currency is hardcoded.** `tradeable.assign(currency="USD")` ([L217](../../src/pipelines/rebalance_live.py#L217)) stamps USD on every leg, and `OrderApp.submit_market_order` defaults `exchange='SMART', currency='USD'` with no `conId` ([ibkr_requests.py L1980](../../src/portutils/ingestion/ibkr_requests.py#L1980)). That is the same bare-symbol contract that returned error 200 for these tickers on the historical pull — as an *order* it either rejects or, worse, resolves to a different listing.
2. **Weights mix currencies.** `current_w = qty * marketPrice / net_liq` ([L116](../../src/pipelines/rebalance_live.py#L116)) divides a pence-quoted GBP position by a base-currency NetLiq. Every weight for a London line is wrong by the FX rate **and** by 100.
3. **Non-target holdings are invisible.** Iterating only `weights` means a held symbol outside the target can never be sold.
4. **No marks for unheld targets.** `marks` is built solely from portfolio rows ([L195](../../src/pipelines/rebalance_live.py#L195)), so any symbol being bought for the first time has no price.

**Decided with the user:** target book is all seven tickers equally weighted across 60% of NetLiq (**0.0857143 each**), leaving 40% cash. Market orders throughout. Full diagnostic pre-trade table. The user runs the dry run themselves, inspects it, then re-runs live.

## Changes

### 1. `config/asset_universe.yaml` — the five unknown tickers + the target vector

Add universe entries so `cfg.asset_role` stops returning `unclassified` (which also fixes the by-role tables in `check_existing_port.py`), then the weight vector:

```yaml
  live_book:
    5MVL: 0.0857143
    AINF: 0.0857143
    BARC: 0.0857143
    HSBA: 0.0857143
    LNG:  0.0857143
    SPY:  0.0857143
    KMLM: 0.0857143      # sums to 0.60 — the remaining 40% is cash, by construction
```

Weights deliberately do not sum to 1; the file's own header already sanctions that, and `Book`/`ConstantMixRule` size off `net_liq * w` so the residual simply stays uninvested. `BARC`/`HSBA` are financials-beta and `LNG` energy; **`AINF` and `5MVL` go in with a `role` I cannot verify and a `note: VERIFY` marker** — I will not invent a description of an instrument the user holds. Flag both for correction.

Point `live_trading.portfolio` at `live_book` in `config/settings.yaml`.

### 2. `ibkr_requests.py` — let an order name its contract

- `OrderApp.submit_market_order` / `submit_limit_order`: add `con_id=None`, pass through to `contract()` (which already takes it).
- `submit_rebalance_orders`: add `contract_specs=None` — the same `{symbol: kwargs}` shape `get_equity_data` takes — and apply `specs.get(symbol, {})` per row, with the existing `currency_col` as the fallback so current callers are untouched. The dry-run print gains the resolved contract, since "DRY RUN: BUY 100 BARC (USD)" is exactly the line that would have hidden this bug.

Reuses **`contract_specs_from_portfolio`** ([ibkr_requests.py](../../src/portutils/ingestion/ibkr_requests.py)) — already written and tested for the historical pull.

### 3. `rebalance_live.py` — value in base currency, not price × quantity

**The FX fix is to stop computing value.** IBKR already reports `marketValue` per position in the account's base currency, so:

```python
base_px = marketValue / position      # base-currency price per share, FX and pence included
```

No FX request, no pence factor, no assumption — the broker's own conversion, which is also the one NetLiq was computed with. Guard `position == 0`. Keep the raw `marketPrice` alongside for display only, and print the implied `marketValue / (position * marketPrice)` ratio per leg — that single number exposes both the 100× and the FX factor, the same trick cell 7b uses in `check_existing_port.py`.

Other changes:

- **Universe** = `set(weights) | set(held)`, so a holding outside the target is sold to zero rather than ignored.
- **Marks for unheld targets** — one `get_equity_data(..., duration="5 D", output_format="dict")` call for symbols with no portfolio row, taking the last close. Those are US-SMART lines (`SPY`, `KMLM`); anything non-US and unheld cannot be priced this way and is reported `SKIP no mark`, never guessed.
- **Order sizing stays in base currency**; the units sent to IB are share counts, which are currency-free. Only the notional guard needs the base-currency price — and `max_order_value` is therefore explicitly a base-currency ceiling. Say so in the config comment.
- **`build_orders` gains a `contract_specs` column set** so the printed table and the submitted order come from the *same* resolved contract, not two independent guesses.

### 4. The full pre-trade table

Per leg: `symbol, conId, exchange, currency, quote_price, base_price, fx_ratio, current_qty, current_weight, target_weight, weight_diff, units, action, notional_base, status`. Plus a header block naming account, NetLiq **and its currency**, the portfolio, resolved mode, and the guard settings in force. Printed identically on dry and live runs — the live path prints it *before* transmitting, as it does today.

## Verification

1. `python -m pytest tests/test_rebalance_live.py -q` — the existing suite must stay green; `build_orders`'s signature change is additive. Add cases: a GBP/pence leg gets base-currency weights (`marketValue`-derived) not `marketPrice` ones; a held symbol absent from `weights` is sold to zero; `contract_specs` reaches the submitter.
2. `python -m pytest tests/ -q` — full suite.
3. **User runs the dry run themselves**: `python src/pipelines/rebalance_live.py --dry-run`. Check before going further: every leg shows a real `conId`, London lines show `GBP` + their LSE exchange, `fx_ratio` is ~1 for US and ~100×FX for the pence lines, weights sum to ~0.60, and the seven targets are each ~8.6%.
4. `outputs/live/orders_<ts>_dryrun.csv` written and matching the printed table.
5. **Then the user runs it live**: `python src/pipelines/rebalance_live.py --live`, and confirms the orders appear in TWS. Paper account `DUP102412`, so `require_paper` passes without `--i-know-this-is-real`.

I will not run step 3 or 5 — the user asked to run these, and the live one transmits orders.

## Progress Log

| # | Step | Status |
|---|------|--------|
| 1 | `asset_universe.yaml`: 5 ticker entries + `live_book` vector; settings points at it | pending |
| 2 | `submit_market_order` / `submit_limit_order` take `con_id` | pending |
| 3 | `submit_rebalance_orders` takes `contract_specs` | pending |
| 4 | `rebalance_live.py`: base-currency valuation via `marketValue` | pending |
| 5 | `rebalance_live.py`: universe = weights ∪ held; marks for unheld targets | pending |
| 6 | Full diagnostic table + contract specs threaded to the submitter | pending |
| 7 | Tests extended; full suite green | pending |
| 8 | User's dry run, then user's live run | pending — user-driven |

## Decisions

- 2026-07-26 — Target is **seven names equally weighted over 60% of NetLiq**, 40% cash. Weights sum to 0.6 deliberately; the uninvested remainder is the cash allocation, not an error.
- 2026-07-26 — Base-currency value comes from IBKR's **`marketValue`**, never from `marketPrice × qty`. It removes the pence factor and the FX rate in one step, using the broker's own conversion — the same one NetLiq was computed with, so weights are internally consistent by construction.
- 2026-07-26 — Contracts for held names come from **`conId`** via the existing `contract_specs_from_portfolio`. An order is the worst possible place to let TWS re-resolve a bare ticker.
- 2026-07-26 — **Market orders**, per the user, matching the studied policy. Noted as a live risk for the London lines: thin books and no limit protection, so do not run this into an illiquid open.
- 2026-07-26 — `AINF` and `5MVL` enter the universe with an **unverified role** and a `VERIFY` note rather than an invented description.
