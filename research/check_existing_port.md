# `check_existing_port.py` — what my real account actually did

Breaks down realised vs unrealised P&L for every holding in the IBKR paper account, using data pulled from the broker rather than simulated fills.

Companion to [rebalance_realisation.py](rebalance_realisation.md): same cell-script idiom, same plotting. The difference is the source of truth — that file simulates a policy over cached prices, this one reads the account that exists.

## How far back it can honestly see

This is the central limitation and the script prints it rather than burying it:

| period | source | status |
|---|---|---|
| **today** | positions, average cost, IB's own P&L | **exact** — from the broker |
| **last ~7 days** | real executions via `reqExecutions` | **exact** — real fills |
| **before that** | nothing. TWS will not serve it | **reconstructed** — see below |

The ~7-day ceiling is a TWS limitation, not a bug and not something a larger `days_back` defeats. **An empty executions result means "no fills in the window TWS serves", never "no trades ever".**

## The policy-anchored backcast

For the period before the executions window, the script reconstructs rather than guessing, via [`portutils.portfolio.backcast`](../src/portutils/portfolio/backcast.py):

1. **Anchor** — roll the real fills backwards off today's positions → the exact position at the window start. This end is real data.
2. **Horizon** — default 3 months back, overridable globally (`BACKCAST_MONTHS`) and per symbol (`HELD_SINCE`).
3. **Backward walk** under a named policy:
   - `buy_and_hold` — units constant going back. Not an approximation: if no trades happened, this is exactly what the book was.
   - `constant_mix` — weights at target every bar, so equity steps back by the exact recursion `E(t−1) = E(t) / (1 + Σ wᵢ·rᵢ(t))`.
4. **Forward replay** through a real `Book`, using the same `apply_fill` as everywhere else. This is what produces a realised/unrealised split for the pre-window period — a split is a property of *trades*, and positions alone do not carry one.
5. **Falsify** — check the replay lands on the position and average cost IBKR reports today.

### The three verdicts, which are not interchangeable

| verdict | meaning |
|---|---|
| `consistent` | the assumption fits, **and** the data could have rejected it |
| `INCONSISTENT` | the assumption does not fit — do not believe the reconstruction |
| `indeterminate` | it fits, but the candidate policies are indistinguishable on this data, so the fit is **not evidence** |

That third verdict exists because the check compares cost bases, and two policies build different cost bases only to the extent prices moved. Measured on synthetic paths: **~1.2% separation at 0.8% daily vol versus ~5–8% at 2%.** On a quiet window the wrong policy passes. `separation()` measures this, and the script reports `indeterminate` rather than letting a pass that could not have failed read as confirmation.

The verdict prints **before** any numbers it produced, so it is not possible to read the chart without first seeing whether it should be believed.

## Running it

Cell script — run cell by cell in VS Code / Jupyter, or top to bottom. Needs TWS on `127.0.0.1:7497`.

Settings at the top: `ACCOUNT`, `CLIENT_ID` (must differ from other scripts connected at the same time), `EXEC_DAYS_BACK`, `BACKCAST_POLICY` (`BUY_AND_HOLD` / `CONSTANT_MIX` / `None`), `BACKCAST_MONTHS`, `HELD_SINCE`.

## Reads / writes

| | |
|---|---|
| reads | IBKR account + executions + price history; `config/asset_universe.yaml` |
| writes | `data/processed/prices_holdings.parquet` (cached prices) |

## Caveats

- **IB's `realizedPnL` is session-scoped**; our `Position.realised` is cumulative since the position opened. `book_from_portfolio` therefore does **not** seed realised from IB by default — the two are not the same quantity.
- **IB's `averageCost` includes commissions**, and for non-stock instruments is per contract (multiplied). Fine for equities; futures need a multiplier passed in.
- The backcast models no dividends, splits, financing or FX — a long horizon over a dividend payer drifts for reasons unrelated to the policy.
- The `avgCost` check is a **consistency** test, not a proof. It can refute an assumption confidently; it can only support one.

## The permanent fix

The 7-day ceiling is unavoidable for the past and entirely avoidable for the future. Run [`log_positions.py`](../src/pipelines/log_positions.md) daily and the position path becomes *recorded* rather than inferred. Every day it does not run is a day that cannot be recovered later.

For genuine deep history there is IBKR's Flex Web Service (a query and token created in Account Management) or a manually downloaded activity statement. Neither is reachable through this API connection.
