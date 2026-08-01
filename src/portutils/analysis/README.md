# `analysis/` — returns, performance, attribution, factors

Measurement, not decision-making. Nothing here places an order or holds a position; it takes price
and weight series and reports what happened.

| module | main API |
|---|---|
| [`returns.py`](returns.py) | `ReturnsCalculator`, `ReturnsConfig`, `pct_returns`, `log_returns`, `daily_returns`, `normalise` |
| [`performance.py`](performance.py) | `PerformanceSummary`, `PerformanceCompare`, `ReturnsToLevels`, `simulate_weights`, `ReturnAttribution` / `AttributionResult` |
| [`factor_analysis.py`](factor_analysis.py) | `PCAResults`, `scale_data`, `daily_returns`, `log_returns` — factor decomposition |
| [`strategies.py`](strategies.py) | `QuantileRiskControlStrategy` and its `Grid`, `QuantileForecastsMerger`, `IntradayIndexLevelsCleaner`, `StrategyReturnsMerger` |

Deeper notes on the factor work: [**factor_analysis.md**](factor_analysis.md).

## Two things worth knowing

**Simple vs log returns are not interchangeable.** Log returns add across time, which makes them
right for compounding a single series; simple returns add across *assets*, which makes them right
for weighting a portfolio. `returns.py` offers both and expects the caller to have decided.

**Attribution answers "where did the P&L come from".** `ReturnAttribution` decomposes a portfolio's
return into per-asset contributions; the realised-vs-unrealised split it feeds on is the accounting
engine's ([`portfolio/book.py`](../portfolio/README.md)), not a re-derivation. Where the two would
disagree, the book wins — it is the thing reconciled against the broker.

For how rebalancing itself generates realised P&L, see
[`src/pipelines/rebalance_study.py`](../../pipelines/rebalance_study.py) and
[`research/rebalance_realisation.md`](../../../research/rebalance_realisation.md).
