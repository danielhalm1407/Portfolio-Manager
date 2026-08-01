# `rebalance_realisation.py` — rebalancing as the source of realised P&L

Interactive companion to [`src/pipelines/rebalance_study.py`](../src/pipelines/rebalance_study.py). A clean copy of `hedge_sleeves/hedge_sleeves.py`'s structure — same `# %%` cells, same `PanelBuilder` → `attribution` → `make_level_figure` flow, same explicit colour/label maps — extended with the P&L figures that file cannot produce.

## The question it answers

`hedge_sleeves.py` models the portfolio as `contrib = weights * rets` with a **constant** weight vector. Holding weights fixed on every bar is mathematically a **daily-rebalanced constant-mix portfolio**: it already assumes you sell whatever rallied and buy whatever fell, every day. When the hedge sleeve rallies into an equity drawdown, that model is quietly selling the hedge at the high.

But a weights-times-returns model carries no units and no cost basis, so it cannot show:

- the **realised P&L** the rebalancing crystallises (a continuous stream, not a one-off),
- the **turnover** cost of doing it ~250×/year,
- which leg did the crystallising.

This file runs the same idea through the fill-based accounting engine, where all three are visible.

## What it found

SPY 60 / KMLM 40, 250 real daily bars, 1,000,000 capital:

| book | final equity | realised | unrealised | turnover | trades |
|---|---|---|---|---|---|
| drift (buy & hold) | 1,137,670 | **0** | 137,670 | 1.0× | 2 |
| mix (daily rebal) | 1,142,005 | **29,098** | 112,907 | 2.1× | 500 |
| mix + drawdown overlay | 1,139,086 | 125,347 | 13,740 | **13.1×** | 500 |

Daily constant-mix produced 29,098 of realised P&L **with no threshold rule anywhere**. Of 250 bars, 67 had SPY falling while KMLM rose, and **86% of KMLM's entire year of realised P&L was booked on those 67 bars** — the hedge banks money by being sold into its own rally, because targets are sized off live equity.

The discretionary drawdown overlay **did not earn its complexity**: it ended below plain `mix` while trading 13.1× capital versus 2.1×. The two rules fight — the overlay dumps half the sleeve on a trigger, the rebalance buys it back next bar. Its large realised figure is churn, not edge.

## Structure

| cells | what |
|---|---|
| 1–2 | imports, reload block, load the cached parquet |
| 3–4 | the `hedge_sleeves.py` view: constant weights × returns, attribution chart |
| 5–6 | run the three books through the fill-based engine; realised P&L by ticker |
| 7 | attribution driven by **actual** per-bar weights from the ledger, not a constant vector |
| 8–11 | equity paths, realised-vs-unrealised split, realised by ticker, weight drift vs target |
| 12–13 | slippage sweep; does the overlay earn its complexity? |

## Two deliberate differences from `hedge_sleeves.py`

- **Data comes from a parquet**, not a live `get_equity_data` call — runs with TWS closed and gives the same bars every time.
- **Tickers and weights come from `config/asset_universe.yaml`**, not literals in the script.

`hedge_sleeves.py` itself is left untouched.

## Running it

```bash
python src/pipelines/cache_prices.py --portfolio spy_kmlm --from-csv   # once
```

Then run the cells. No TWS needed.

## Reads / writes

| | |
|---|---|
| reads | `data/processed/prices_spy_kmlm.parquet`; `config/asset_universe.yaml` |
| writes | nothing — figures render inline. The pipeline version writes HTML to `outputs/scenarios/` |

## Caveats

- **Fill/cost-basis accounting, not a return-compounding backtester.** It answers "what did I crystallise and what am I still carrying". For weight-based return studies with slippage and funding models, `portutils.analysis.strategies.QuantileRiskControlStrategy` remains the right tool.
- No FX, no contract multipliers, no financing, no dividends, no commissions. Slippage is a parameter (`slippage_k`), not a model.
- Weights in the attribution cell are shifted one bar before multiplying by returns, so the chart does not look ahead.

## Related

- [`src/pipelines/rebalance_study.py`](../src/pipelines/rebalance_study.py) — the pipeline version, writes HTML figures
- [`check_existing_port.md`](check_existing_port.md) — the same breakdown for the real IBKR account
- [`src/pipelines/rebalance_live.md`](../src/pipelines/rebalance_live.md) — this policy, trading for real
