# `rebalance_live.py` — live constant-mix rebalancer

**This script sends real orders to Interactive Brokers.** Read the *Dry run vs live* section before running it.

## What it does

Points the constant-mix policy — the one measured in [rebalance_study.py](rebalance_study.py) — at a real IBKR account, and trades the account back to its target weights.

It does **not** reimplement the policy. It imports and calls the same `ConstantMixRule` used in the research, so the code deciding what to trade with real money is literally the code that was backtested. A live rebalancer with its own copy of the arithmetic is one that will eventually disagree with its own research.

```
connect → get_account_updates → base-currency marks → book_from_portfolio
        → ConstantMixRule.propose() → signed unit deltas → round to whole shares
        → min-turnover gate → max-order-value guard → print table → (maybe) submit
```

## Mixed currencies and mixed venues

The account holds London lines beside US ones, and both facts below are handled by taking the broker's own numbers rather than computing our own.

**Value comes from `marketValue`, never from `marketPrice × qty`.** An LSE line is quoted in **pence** while its contract currency is GBP, and `averageCost` on the very same row arrives in **pounds**. NetLiquidation is in the account's base currency. So `qty * marketPrice / net_liq` is wrong by the FX rate *and* by 100 — a 1% holding reads as 105%, and a rebalancer acting on that sells most of the account. IBKR already reports `marketValue` per position converted to base currency, using the same conversion NetLiq was computed with, so:

```python
base_price = marketValue / position
```

carries both corrections, applied by the broker. The implied `marketPrice / base_price` ratio is printed per leg — `~1` for a US line, `~100` for a pence line — so the correction is visible rather than assumed. No FX rate is fetched: an independently pulled rate would be a *second opinion*, and the weights must agree with the NetLiq they are divided by.

That substitution is also applied to the frame handed to `book_from_portfolio`, because that function back-solves the book's base equity from the frame's own `marketPrice`. Skip it and one mispriced row corrupts `book.equity()`, which `ConstantMixRule` sizes **every** leg against — including the US lines that were fine. Pinned by `test_book_equity_is_wrong_without_the_base_price_substitution`.

**Orders name a contract, not a ticker.** `submit_market_order` defaults to `STK/SMART/USD`; for a London line that is a different instrument or an outright rejection. Every held position's `conId` is in the account snapshot, so `contract_specs_from_portfolio` feeds it straight back into the order via `submit_rebalance_orders(..., contract_specs=...)`. The dry-run line prints the resolved contract, not just the currency — `DRY RUN: SELL 200 BARC [STK LSE GBP conId=776655]`.

A target symbol that is **not held** has neither a `marketValue` nor a `conId`. Its mark comes from the last close via `get_equity_data`, which works for US-SMART lines; anything non-US and unheld is reported `SKIP no mark` and never priced by assumption.

## Dry run vs live

Resolution order, most specific first:

| # | condition | result |
|---|---|---|
| 1 | `--dry-run` on the command line | **always dry**, whatever the config says |
| 2 | `--live` on the command line | transmit |
| 3 | `live_trading.enabled: true` in `config/settings.yaml` | transmit (standing default) |
| — | none of the above | dry run |

`--dry-run` is deliberately **not** configurable away. It is the escape hatch.

Setting `enabled: true` in config means every subsequent run transmits with no flag and no prompt. That is intentional standing authorization. Because it removes the "did you mean it" step, the guards below stay in place rather than also becoming optional.

## Guards that survive `enabled: true`

- The **full order table prints before anything is transmitted**, on every run: `symbol, conId, exchange, currency, quote_price, base_price, fx_ratio, current_qty, current_weight, target_weight, weight_diff, units, action, notional_base, status` — preceded by the account, NetLiq *and its currency*, and the guard settings in force.
- A holding **outside the target is closed**, not ignored. Iterating only the target weights meant a position we no longer want could never be sold; `target_weight = 0` is an instruction. A close is exempt from `min_turnover` (it is a decision about the position's existence, not a drift correction) but **not** from `max_order_value`.
- `require_paper` refuses account ids without IB's `DU` paper prefix. A real account additionally needs `--i-know-this-is-real`.
- `max_order_value` **rejects** (does not clip) any single order above the ceiling. An order that large means something upstream is wrong — a bad tick, a stale NetLiq — and quietly trading a smaller amount would hide the fault while still acting on it.
- Weights must resolve from `config/asset_universe.yaml`, so the live policy cannot silently drift from the studied one.
- Every run writes `outputs/live/orders_<timestamp>.csv` — including dry runs, so the intent is on record either way.
- A symbol with no `marketPrice` is **skipped**, never filled at an assumed price.

## Configuration

`config/settings.yaml`:

```yaml
live_trading:
  enabled: false          # true → every run transmits, no flag needed
  account: "DUP102412"
  require_paper: true
  max_order_value: 50000  # in the account's BASE currency, after conversion
  min_turnover: 0.02      # skip legs already within this of target
  portfolio: "live_book"
  client_id: 151
```

`live_book` in `config/asset_universe.yaml` is the seven names actually traded — `5MVL, AINF, BARC, HSBA, LNG, SPY, KMLM` — each at `0.0857143`. The vector **sums to 0.60 on purpose**: weights are fractions of capital, not of the invested portion, so the missing 0.40 is the cash allocation. `spy_kmlm` is the two-ticker *research* vector; pointing live trading at it would liquidate the account into SPY and KMLM.

> `AINF` and `5MVL` are in `asset_universe.yaml` with a placeholder `role` and a `VERIFY` note — the instruments were not identified. Their role feeds the hedge-vs-beta roll-ups, so correct them before trusting any role-level aggregate.

Every field is overridable per-run: `--account`, `--portfolio`, `--max-order-value`, `--client-id`. Defaults are the safe end of every switch, so a missing or partial config yields a dry run against the paper account.

## Running it

```bash
python src/pipelines/rebalance_live.py            # dry run (unless config says otherwise)
python src/pipelines/rebalance_live.py --live     # transmit
python src/pipelines/rebalance_live.py --dry-run  # force dry, overrides config
```

Needs TWS or IB Gateway running on `127.0.0.1:7497` with API access enabled.

## Reads / writes

| | |
|---|---|
| reads | IBKR account (positions, marks, NetLiq); `config/asset_universe.yaml`; `config/settings.yaml` |
| writes | `outputs/live/orders_<timestamp>.csv`; real orders to IBKR when live |

## Caveats

- **Whole shares only.** Deltas are truncated, so the book lands slightly off target on small positions. Truncation (not rounding) is used so a rebalance never overshoots.
- **Market orders.** Submitted via `submit_rebalance_orders` from [`portutils.ingestion.ibkr_requests`](../portutils/ingestion/ibkr_requests.py). No limit protection — do not run this into an illiquid open.
  - That function used to be imported from `orders/rebalance_port_basic.py`. It is not, and must not be: that file is a cell script whose module-level code opens a connection and runs a live rebalance, so importing it would transmit orders **before any guard in this pipeline ran, even on a dry run**. Pinned by `test_does_not_import_the_cell_script`.
- **One-shot.** Each run is a single rebalance. Daily cadence means running it daily; there is no scheduler here.
- **No commission modelling.** The order table's notional is gross. Turnover cost is measured in the research, not here.
- IB's `averageCost` includes commissions and, for non-stock instruments, the contract multiplier — see the header of [ibkr_sync.py](../portutils/portfolio/ibkr_sync.py). Fine for SPY/KMLM; wrong for futures without a multiplier.
- **Market orders on the London lines have no limit protection.** Thinner books than the US ETFs, and a market order outside that venue's regular hours is rejected or fills badly. `OrderApp.submit_limit_order` exists if this proves to be a problem in practice.
- The unheld-target fallback prices from the **last close**, not a live quote, so a name being opened from flat is sized off yesterday's price. Only affects the first buy of a new position.

## What it deliberately does not do

Order-type selection, scheduling, partial-fill handling, retry logic, or any policy of its own. It is the studied policy plus the guards that only matter when the money is real.
