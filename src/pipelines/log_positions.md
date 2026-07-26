# `log_positions.py` — daily position snapshot

The only permanent fix for the 7-day history ceiling.

## Why it exists

TWS serves roughly the last **7 days** of executions and nothing before that. Everything earlier has to be reconstructed under an assumption, with all the caveats in [`backcast.py`](../portutils/portfolio/backcast.py) — and on a quiet market window the reconstruction cannot even be validated.

That problem is unavoidable for the past and **entirely avoidable for the future**, at the cost of one small parquet a day. After a few weeks of running this, the position path is *recorded* rather than inferred, and the whole backcast question becomes moot for anything going forward.

**Every day this does not run is a day of history that cannot be recovered later.** That is the entire argument for it.

## What it records

One row per holding per day — `position`, `averageCost`, `marketPrice`, `marketValue`, IB's `unrealizedPnL` / `realizedPnL` — plus the account totals (`net_liquidation`, `total_cash`) stamped on every row. NetLiq matters as much as the positions: without it a historical *weight* cannot be reconstructed from quantities alone.

An empty account is recorded as a dated row with no holdings, not skipped — a hole in the panel is indistinguishable from a day the script failed to run.

## Reads / writes

| | |
|---|---|
| reads | IBKR account (`get_account_updates`, `get_account_data`) |
| writes | `data/raw/ibkr/positions_YYYY-MM-DD.parquet` — one file per day |
| | `data/raw/ibkr/positions_history.parquet` — the accumulated panel |

`data/raw/` is append-only by repo convention. Re-running for a date that already exists **replaces only that date's rows** and leaves every other day untouched — so opening TWS late and running it twice does not double-count.

## Running it

```bash
python src/pipelines/log_positions.py
python src/pipelines/log_positions.py --account DUP102412 --client-id 131
```

Needs TWS on `127.0.0.1:7497`. Uses client id 131 by default, distinct from the other scripts so it can run alongside them.

Worth scheduling — Task Scheduler on Windows, once a day after the close. It disconnects cleanly even if the snapshot raises, so a failed run does not leave a dangling connection blocking the next one.

## Caveats

- **Snapshots, not trades.** This records where the book *was*, not what it *did*. Two positions a day apart imply a trade but do not identify its price, so a realised/unrealised split still needs either the executions window or a backcast. It makes reconstruction dramatically better-anchored, not unnecessary.
- **End-of-day granularity.** Intraday round trips that open and close between runs are invisible.
- Dates come from the local machine clock, not the exchange calendar, so a run on a market holiday records an unchanged row.

## Related

- [`research/check_existing_port.md`](../../research/check_existing_port.md) — the analysis this feeds
- [`portutils/portfolio/backcast.py`](../portutils/portfolio/backcast.py) — what you need *instead* of this for the past
