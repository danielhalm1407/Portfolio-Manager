# SMART routing, held orders, and an untradeable universe

## Context

The TIF fix worked — all seven orders reached TWS. What came back splits cleanly into three
outcomes, and the ack table reported the first of them wrongly.

### 1. Five orders are HELD, not lost (5MVL, AINF, BARC, HSBA, LNG)

```
IB Error 1: 10311 - This order will be directly routed to IBIS2. Direct routed orders may
result in higher trade fees. Restriction is specified in Precautionary Settings of Global
Configuration/API.
```

The TWS Pending panel shows all five with a blue **Transmit** button — TWS accepted them and
is holding them for manual confirmation. They are not working orders.

**Cause is ours.** [`contract_specs_from_portfolio`](../../src/portutils/ingestion/ibkr_requests.py)
copies the position's `exchange` field verbatim (`IBIS2`, `LSEETF`, `LSE`, `NYSE`), which is
the *listing* venue. Putting that in `Contract.exchange` means **direct routing**, which trips
IB's precautionary warning and the manual-transmit hold. The listing venue belongs in
`primaryExchange`, whose entire purpose is disambiguating a ticker without dictating a route.

### 2. Two orders are genuinely REJECTED (SPY, KMLM)

```
IB Error 5: 201 - Order rejected - reason:No Trading Permission, Customer Ineligible;
This product does not have a KID in English or in a language approved for your country.
```

PRIIPs. US-domiciled ETFs cannot be bought by a UK/EU retail client without a Key Information
Document, and SPY and KMLM do not have one. This is an account-level restriction, not a bug:
no code change makes these two tradeable. They must leave the target book.

### 3. The ack table called the held orders "never received"

`error()` treats codes `>= 2100` as advisory and does not record them. 10311 is in that band,
so nothing was recorded, and `wait_for_order_ack` reported `acknowledged=False` — *"most likely
NOT received"* — for five orders sitting visibly in TWS. The classification is right (10311 does
not terminate the order) but discarding the message loses the only evidence of what happened.

## Changes

### 1. `ibkr_requests.py` — route SMART, name the listing venue

`contract_specs_from_portfolio` returns `exchange="SMART"` with `primary_exchange=<the row's
exchange>`. Keeps the contract unambiguous (conId + primaryExchange) while letting IB route,
which removes 10311 and the manual-transmit hold. `contract()` already accepts
`primary_exchange`.

### 2. `ibkr_requests.py` — keep advisory messages instead of dropping them

New `app.req_notices` dict, populated for every non-suppressed, non-terminal message.
`wait_for_order_ack` gains a `notice` column and counts a notice as evidence the order was
seen. The "never acknowledged" banner then only fires for genuine silence.

### 3. `config/asset_universe.yaml` — a universe this account can actually trade

Drop `SPY` and `KMLM` from `live_book`; five names at **0.02** each, still 10% equity / 90%
cash. Both stay in the `universe:` catalogue (the research vectors use them) with a note
recording the KID restriction, so nobody re-adds them to a live book without seeing why they
left.

### 4. `src/pipelines/cancel_orders.py` — a standalone undo

Separate tiny pipeline, not a flag on the rebalancer: the thing you reach for when something
has gone wrong must not share a code path with the thing that went wrong. Defaults are the
wide ones (`reqAllOpenOrders` / `reqGlobalCancel` cover every client id, since an order placed
by client 151 is invisible to a session connecting as 152). Safety model is INVERTED relative
to the rebalancer — there the dangerous act is trading, here it is cancelling something you
wanted, so the default lists and `--cancel` acts. Every path re-reads the book afterwards and
reports what survived.

### 5. Code 399 is a warning, not a rejection

The live run printed `orderId 8 REJECTED` for five orders that were **PreSubmitted and
healthy**. 399 is IB's "Order Message" (*"your order will not be placed at the exchange until
2026-07-27 09:00"*), and it sits below the 2100 advisory band, so the terminal test caught it.
Two layers of fix, because a code list alone goes stale:

- `_ADVISORY_ORDER_CODES = {399}` — known sub-2100 warnings routed to notices.
- **A live order status overrides any recorded error.** `PreSubmitted`/`Submitted`/… means
  alive, whatever message arrived with it. IB adds warnings faster than anyone updates a
  constant; the order's own status is authoritative and needs no maintenance.

## What actually happened when this was run

1. `cancel_orders.py` (list) from **both** client 152 and client 151: **no open orders**.
   The five "Pending" rows in the TWS panel were never transmitted to IB's servers — an order
   held for manual Transmit is not yet an order, so there was nothing for `reqAllOpenOrders`
   to return and nothing for `cancelOrder` to cancel. They are inert: they cannot fill unless
   somebody clicks Transmit, and only the TWS UI can clear them.
2. `rebalance_live.py --live` → **five orders accepted, `PreSubmitted`, no 10311, no hold.**
   SMART routing fixed it. orderIds 8-12: 5MVL 191, AINF 1580, BARC 3691, HSBA 1252, LNG 97.
3. `cancel_orders.py` (list) again → all five visible **from a different client id**, which is
   the definitive proof they are on IB's servers this time rather than in the TWS client.
4. They queue until Monday 2026-07-27 (09:00 MET for the European legs, 09:30 US/Eastern for
   LNG) and then go to the exchange.

## Verification

1. `pytest tests/ -q` — **90 passing**; new cases for SMART routing, the notice column,
   the `live_book` weights, 399-as-warning, and the live-status override.
2. Offline assertion that `contract_specs_from_portfolio` never returns a direct-routed
   exchange for a held position.
3. Done live, above — orders accepted with no Transmit hold.
4. **Outstanding for the user:** clear the five stale untransmitted rows in the TWS Pending
   panel (the ✕ in the Cancel column). They cannot fill on their own, but clicking Transmit
   later would duplicate the live orders.

## Decisions

- 2026-07-26 — Listing venue goes in `primaryExchange`, never `exchange`. Direct routing was
  never intended; it was an accident of copying the account snapshot's field straight across,
  and it cost a manual-confirmation hold on every foreign leg.
- 2026-07-26 — SPY and KMLM leave the live book. The rejection is a client-eligibility rule
  (PRIIPs/KID), so retrying it in any form is pointless. UCITS equivalents (e.g. CSPX for
  S&P 500 exposure) are the substitute if the exposure is still wanted — a decision for the
  user, not an automatic swap.
- 2026-07-26 — Advisory messages are recorded, not discarded. "Not terminal" and "not worth
  keeping" are different claims, and conflating them made five held orders look lost.
- 2026-07-26 — An **untransmitted order is not an order**. It exists only in the TWS client,
  is invisible to `reqAllOpenOrders`, and cannot be cancelled through the API. Anything that
  reports on order state must be able to say this rather than implying the API's silence
  means the order is gone.
- 2026-07-26 — Order classification believes the **status over the code**. A code list is a
  maintenance burden that fails silently as IB adds warnings; `PreSubmitted` is unambiguous.
  The code set stays as a fast path, the status check as the guarantee.
- 2026-07-26 — Cancellation lives in its own pipeline with `--cancel` required to act. The
  recovery tool must not be reachable by accident from the tool being recovered from.
