# `portutils/` — the installable library

Importable, side-effect-free code. Install with `pip install -e .` from the repo root; import as
`from portutils.<subpackage> import ...`.

Five subpackages:

| subpackage | owns | doc |
|---|---|---|
| [`ingestion/`](ingestion/) | everything that talks to the outside world — all TWS/IBKR plumbing, the Reuters scraper | [README](ingestion/README.md) |
| [`portfolio/`](portfolio/) | the accounting engine, rebalance policies, the simulator, the broker→book bridge | [README](portfolio/README.md) |
| [`analysis/`](analysis/) | returns, performance, attribution, factor decomposition, strategy research helpers | [README](analysis/README.md) |
| [`utils/`](utils/) | the single config loader — `.env` + `settings.yaml` + `asset_universe.yaml` | [README](utils/README.md) |
| [`viz/`](viz/) | Plotly/Dash/Panel helpers and the one colour theme every figure uses | [README](viz/README.md) |

## The dependency direction

```
utils   ← nobody depends on anything above it
  ↑
ingestion   (talks to TWS and the web)
  ↑
portfolio   (accounting, rules, simulation)  ── ingestion/ibkr_sync bridges the two
  ↑
analysis, viz
```

`portfolio/` is deliberately ignorant of IBKR: `Book`, `Position` and the rules know about symbols,
quantities and prices, nothing else. The one bridge is
[`portfolio/ibkr_sync.py`](portfolio/ibkr_sync.py), which turns what IBKR reports into the
accounting engine's own objects — so the same `Book` serves a live account and a simulated one, and
[`tests/test_book_parity.py`](../../tests/test_book_parity.py) can hold the two to the same numbers.

## Rules that apply to every module here

- **No side effects on import.** No connections, no file writes, no network calls at module level.
- **No `os.getenv()`.** Config comes from [`utils/config.py`](utils/config.py); see
  [`src/README.md`](../README.md) for why.
- Every new module needs an `__init__.py` in its package.
