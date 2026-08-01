# `cancel_orders.py` — clear open orders

The undo for [`rebalance_live.py`](rebalance_live.py). A rebalance that goes wrong leaves working orders behind, and until this existed there was no way to clear them except by hand in TWS.

Deliberately a separate pipeline rather than a flag on the rebalancer: the thing you reach for when something has gone wrong must not share a code path with the thing that went wrong.

## Usage

```bash
python src/pipelines/cancel_orders.py                    # list only — cancels nothing
python src/pipelines/cancel_orders.py --cancel           # cancel every open order
python src/pipelines/cancel_orders.py --cancel --ids 8,9 # cancel just these
python src/pipelines/cancel_orders.py --cancel --global  # reqGlobalCancel, the blunt one
```

## Safety model — inverted relative to the rebalancer

For `rebalance_live` the dangerous act is trading, so the default is a dry run. Here the dangerous act is cancelling something you wanted to keep, so the default **lists** and `--cancel` acts.

Cancellation is a *request*, not a guarantee. Every path re-reads the open orders afterwards and reports what survived, rather than assuming the call worked — the same lesson `placeOrder` taught.

## Why the defaults are the wide ones

`reqOpenOrders` returns only orders placed by **this client id**. An order submitted by `rebalance_live` (client 151) is invisible to a session connecting as 152 — which is how you end up believing you cancelled something you did not. So this uses `reqAllOpenOrders` and `reqGlobalCancel`, both of which span every client on the account. `--this-client-only` narrows it.

Client id defaults to **152**, beside the rebalancer's 151. TWS misbehaves silently when two connections share an id.

## An untransmitted order is not an order

If TWS holds an order for manual confirmation — the Pending panel, blue **Transmit** button, IB warning 10311 — it was never sent to IB's servers. It exists only in your TWS client. Consequences:

- `reqAllOpenOrders` returns nothing for it, so this script will report "no open orders" while five rows sit visibly on screen.
- `cancelOrder` has nothing to cancel.
- **It cannot fill on its own.** It is inert until somebody clicks Transmit.

Clear those in the TWS UI (the ✕ in the Cancel column). The API cannot.

The usual cause was direct routing — see the routing note in [rebalance_live.md](rebalance_live.md). With `exchange="SMART"` and the venue in `primaryExchange`, orders transmit normally and become visible here.

## Reads / writes

| | |
|---|---|
| reads | IBKR open orders; `config/settings.yaml` (account only) |
| writes | cancel requests to IBKR — nothing to disk |
