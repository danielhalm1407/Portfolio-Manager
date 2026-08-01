# `pipelines/` — the runnable scripts

Workflow scripts with side effects: they connect, fetch, compute, write files and — for two of them
— place real orders. Each has its entry point behind `if __name__ == "__main__":`, so importing one
to reuse its functions never runs it.

Run by path, from the repo root:

```bash
python src/pipelines/<name>.py [--flags]
```

## The ten

| pipeline | what it does | broker? | doc |
|---|---|---|---|
| [`rebalance_live.py`](rebalance_live.py) | **The live constant-mix rebalancer.** The studied policy pointed at a real IBKR account. Dry run unless `--live`. | **places orders** | [rebalance_live.md](rebalance_live.md) |
| [`cancel_orders.py`](cancel_orders.py) | The undo. Cancel one order or everything on the account, across all client ids. | **cancels orders** | [cancel_orders.md](cancel_orders.md) |
| [`log_positions.py`](log_positions.py) | Daily position snapshot — the only permanent fix for TWS's ~7-day history ceiling. | reads | [log_positions.md](log_positions.md) |
| [`cache_prices.py`](cache_prices.py) | Caches a wide daily close-price panel to `data/processed/` for offline scenario work. | reads | — |
| [`rebalance_study.py`](rebalance_study.py) | Rebalancing as the source of realised P&L — three books, one price panel. | offline | [research/rebalance_realisation.md](../../research/rebalance_realisation.md) |
| [`drawdown_rotation_sim.py`](drawdown_rotation_sim.py) | Scenario: what happens to realised vs unrealised P&L if the hedge is cut on a drawdown. | offline | — |
| [`scrape_commentary.py`](scrape_commentary.py) | Scrapes Reuters commentary into `data/raw/commentary/`. | web | — |
| [`theme_scan.py`](theme_scan.py) | Extracts and scores themes from that commentary. | offline | — |
| [`daily_ingest.py`](daily_ingest.py) | Daily market data + commentary ingestion. | web | — |
| [`weekly_review.py`](weekly_review.py) | Generates the portfolio tilt report. | offline | — |

## Before running anything that trades

Read [EXECUTION_STACK.md](../../EXECUTION_STACK.md) first, and note the safety model:

- `rebalance_live.py` is a **dry run by default**. `--live` is the opt-in, per invocation.
- `max_order_value` **rejects** an oversized order rather than clipping it — an order that big means
  something upstream is wrong, and trading a smaller amount would hide the fault while still acting
  on it.
- A symbol with an already-working order is **skipped**: the position is mid-flight, so no target
  computed from it is trustworthy, and re-running would double the exposure.
- Market orders cannot execute outside trading hours. `market_hours_note()` is a crude weekday/clock
  check, not a venue calendar — no holidays, no half-days.

To understand a rebalance rather than perform one, use the cell harness
[`orders/rebalance_live_debug.py`](../../orders/rebalance_live_debug.py), which imports this
pipeline's own functions instead of restating them.

## Outputs

`outputs/live/orders_*.csv` — one file per run, dry runs included (suffixed `_dryrun`), recording
what was intended **and what TWS actually said** about it.
