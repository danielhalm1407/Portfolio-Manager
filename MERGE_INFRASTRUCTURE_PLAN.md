# Merge Infrastructure Plan — `kts.py` ⇄ `portutils.ingestion.ibkr_requests`

## Goal
Eliminate the duplicate `IBApp` implementations. `orders/kts.py` (the Kalman/OU
Tkinter trading GUI) currently ships its **own** `IBApp(EWrapper, EClient)` plus
inline contract/order/historical logic. The library at
`src/portutils/ingestion/ibkr_requests.py` already has a more capable, thread-safe
`IBApp(IBKRApp)` plus request helpers.

**Direction:** `kts.py` drops its private `IBApp` and consumes the library `IBApp`
+ builders. The market-data smarts that *only* exist in `kts.py` (delayed tick
tiers, mid synthesis, sectype gating) get **ported into** the library so nothing
is lost.

> **Prerequisite — `CONN_REQUESTS_MERGE_PLAN.md` is now ✅ DONE.** That plan folded
> `ibkr_conn.py` into `ibkr_requests.py` and collapsed the `IBKRApp` base into a single
> `IBApp(EWrapper, EClient)` (the old file is preserved at `sandbox/ibkr_conn.py`).
> The library `IBApp` now inherits `EWrapper, EClient` directly and carries the
> widened `*args` `error()`. The "Effect of the conn→requests merge" callouts below
> therefore describe state that **already exists** — Blockers #1 (base mismatch) and
> #4 (`error()` signature) are largely pre-resolved.

After **both** merges the layering is:
- `src/portutils/ingestion/ibkr_requests.py` — connection lifecycle (`connect_ib`/`disconnect_ib`) at the top, then the **single** `IBApp(EWrapper, EClient)`, pure builders, request helpers, `OrderApp`. **Owns all TWS plumbing.** (`ibkr_conn.py` no longer exists.)
- `orders/kts.py` — GUI + OU/Kalman model only. Holds Tk state and the model; treats `IBApp` as a pure data pipe.

---

## Current overlap (authoritative line references)

| Concern | `orders/kts.py` | `ibkr_requests.py` | Keep |
|---|---|---|---|
| Base class | `IBApp(EWrapper, EClient)` — kts.py:241 | `IBApp(IBKRApp)` → **`IBApp(EWrapper, EClient)`** after conn→requests merge | **library** |
| Contract build | `KalmanTradingApp.contract()` — kts.py:869 | `contract()` func — :36 | **library builder**, GUI delegates |
| Tick handling | `tickPrice` w/ delayed 66/67/68 + `_maybe_emit_mid` — kts.py:378, 413 | `tickPrice` live-only 1/2/4 — :375 | **library, after porting kts logic in** |
| Historical | single `hist_done` Event, hardcoded reqId=2 — kts.py:453, 1288 | per-reqId `_hist_events` + `get_equity_data` — :410, 633 | **library pattern**, add intraday helper |
| Order build | inline in `place_trade` — kts.py:1471 | `market_order` / `limit_order` builders — :59, 70 | **library builders**, add crypto builder |
| Order send | `placeOrder` + un-locked `next_order_id += 1` — kts.py:1584 | `OrderApp.place_order` + locked `reserve_order_id` — :1044, 301 | **library `OrderApp`** |
| Order id alloc | manual increment, no lock — kts.py:1586 | `reserve_order_id()` under `self.lock` — :301 | **library (fixes race)** |
| `error()` cb | widened `*args`, forward-compatible — kts.py:343 | fixed 4-arg signature — ibkr_conn.py:86 | **port kts widened version into the single `IBApp`** (done in conn→requests merge) |

---

## 1. Market data merge

Library `tickPrice` (ibkr_requests.py:375) is the weaker one. Port `kts.py` logic into it.

**1a. Tick types.** Add delayed tiers so crypto/FX/off-hours don't freeze:
- `4, 68` → LAST, `1, 66` → BID, `2, 67` → ASK. (from kts.py:391–411)

**1b. Mid synthesis + the `IBApp.__init__` attributes it needs.** Port
`_maybe_emit_mid()` (kts.py:413–442) into library `IBApp`. It depends on three
market-data attributes that exist **only in kts today** and must be added to the
merged library `IBApp.__init__`. A fourth attribute, `account_value`, also migrates
from kts but is tied to Blocker #3. All four are kts-only (absent from both the old
`ibkr_conn.py` base and the merged `ibkr_requests.py`), so their **kts comments are the
base, with no other version to merge in** — relocate them verbatim.

| Attribute (init) | kts source | Role | Comment base + layering |
|---|---|---|---|
| `self.sectype = "STK"` | kts.py:339 | Active secType; gates `_maybe_emit_mid` so FX/CRYPTO keep emitting mids even after a LAST tick, while STK suppresses mids in favour of real LAST prints. | **Base: kts** (sole source). |
| `self._last_emitted_mid = None` | kts.py:341 | Dedup guard — skip re-emitting an unchanged mid when neither bid nor ask moved. | **Base: kts** (sole source). |
| `self._last_tick_source = "?"` | kts.py:335 | Provenance tag (`"LAST(4)"` / `"MID"`) set right before each `on_tick`, so the consumer/log can see where the price came from. | **Base: kts** (sole source). |
| `self.account_value = None` | kts.py:332 | GUI net-liquidation convenience read by `update_portfolio_display` (kts.py:1599). **See Blocker #3:** the library has no such attr — prefer deriving from `account_summary_by_tag`; decide during this merge whether to keep the attr (populate it from `accountSummary`) or compute it on read. | **Base: kts** *if* kept. |

> The first three were noted in `CONN_REQUESTS_MERGE_PLAN.md`'s attribute inventory as
> *"later (kts merge)"* — they are **owned here**, not by the conn→requests merge.

**1c. `on_tick` signature — BLOCKER.**
- kts: `on_tick(price, ts)` (kts.py:402)
- library: `on_tick(price)` (ibkr_requests.py:384)
- **Resolution:** standardise on `on_tick(price, ts=None)`. Library-internal callers
  may pass nothing; kts passes `datetime.now()`. Backward-safe for both.

**1d. Tier selection stays in GUI.** `_mkt_data_type_code()` (kts.py:856) is UI-bound —
leave it in `KalmanTradingApp`. GUI keeps calling
`self.ib.reqMarketDataType(code)` then `self.ib.reqMktData(1, self.contract(), ...)`
(kts.py:1009, 1024) — works against the library `IBApp` unchanged.

**1e. `dlog` coupling.** The kts file-logger (`dlog`, kts.py:67) must **not** leak into
library code. When porting tick logic, drop the `dlog(...)` calls or gate them behind a
no-op `self._log = log_fn or (lambda *a, **k: None)` injected at construction. Library
stays side-effect-free on import (per `src/CLAUDE.md`).

---

## 2. Historical data merge

`kts.refresh_30m` (kts.py:1214) hand-rolls a single-Event historical pull with a
hardcoded `reqId=2`. The library has the robust per-reqId pattern, but
`get_equity_data` (ibkr_requests.py:633) is **batch CSV-to-disk** — wrong shape for
live intraday calibration.

**2a. New library helper.** Add to `ibkr_requests.py`:
```python
def get_historical_bars(app, contract_obj, duration, bar_size,
                        what_to_show='TRADES', use_rth=1, end='', timeout=20):
    """Return a list[dict] of OHLCV bars for ONE contract (no DataFrame, no CSV)."""
```
Extract the request/wait/collect core from `get_equity_data` (lines 718–780),
dropping the DataFrame + CSV tail. Uses `app.next_req_id()` and `app._hist_events`.

**2b. Rewrite `refresh_30m`.** Keep ALL its GUI/calibration logic (kts.py:1304–1347).
Replace the plumbing block (kts.py:1249–1298) with one call:
```python
bars = get_historical_bars(self.ib, self.contract(),
                           duration_str, bar_size_setting, what_to_show, use_rth)
```
Gains: per-reqId events (no `hist_done` collision), no hardcoded reqId.

**2c. Contract dedup.** Keep GUI `self.contract()` (reads Tk vars) but delegate to the
library builder so `Contract` construction lives in one place:
```python
def contract(self):
    return contract(self.symbol_var.get().strip().upper(),
                    sec_type=self.sectype_var.get().strip().upper(),
                    exchange=self.exchange_var.get().strip().upper())
```

---

## 3. Order sending merge

`kts.place_trade` (kts.py:1471) mixes three concerns: GUI bookkeeping, order
construction, send. Split them.

**3a. Order construction → library builders.**
- STK/CASH market order: `market_order()` (ibkr_requests.py:59) already covers it.
- Crypto IOC marketable-limit + tick-snap logic (kts.py:1525–1566) is **missing** from
  the library. Add a pure builder:
```python
def crypto_marketable_limit_order(action, qty, ref_price, tick=0.50, buf_bps=10):
    """IOC limit that crosses the spread by buf_bps and snaps to tick (PAXOS)."""
```
  Holds kts.py:1545–1566. No Tk, fully unit-testable.

**3b. Order send → `OrderApp.place_order`** (ibkr_requests.py:1044). It calls
`reserve_order_id()` (locked, :301) instead of kts's **un-locked**
`self.ib.next_order_id += 1` (kts.py:1586) — fixes a real race when auto-trade +
manual click overlap. Keep the existing `root.after(0, ...)` dispatch (kts.py:1469);
the lock is now the real serialization guarantee.

**3c. GUI keeps:** position-size cap, optimistic `self.position` update
(kts.py:1593–1595), status labels.

**3d. Refactored `place_trade` shape:**
```python
contract_obj = self.contract()
if is_crypto:
    ref = self.ib.ask if action == "BUY" else self.ib.bid
    ref = ref if ref is not None else self.ib.last_price
    order = crypto_marketable_limit_order(action, qty, ref)
else:
    order = market_order(action, qty)
self.order_app.place_order(contract_obj, order)
# ...optimistic bookkeeping unchanged (kts.py:1593-1595)
```
Build `self.order_app = OrderApp(self.ib)` once in `connect_ib` after the handshake
(kts.py:899 block). `OrderApp.__init__` requires `app.connected` (ibkr_requests.py:1037)
— true there.

---

## Blockers to reconcile (the real work)

1. **Two `IBApp` base classes.**
   **Effect of the conn→requests merge:** this blocker is *largely dissolved*. After
   `CONN_REQUESTS_MERGE_PLAN.md`, the library `IBApp` inherits `EWrapper, EClient`
   **directly** — exactly the same base kts.py:241 already uses. So there is no longer
   a base-class mismatch to reconcile; kts simply imports the one library `IBApp`.
   What remains is the *connection entry point*: kts `connect_ib` (kts.py:887) does its
   own `connect` + daemon thread + 5 s poll; the library exposes
   `app.start(host, port, client_id)` (ibkr_requests.py:254) which wraps the
   now-co-located `connect_ib` and **retries client ids on error 326**.
   → kts switches to `self.ib.start(...)` and deletes its private connect/thread code.

2. **`on_tick` arity** — unify to `(price, ts=None)` (see 1c).

3. **Account / position attribute shapes differ.**
   - kts reads `self.ib.account_value` (kts.py:1599) and
     `self.ib.positions[sym]['position']` (kts.py:912, keyed by bare symbol).
   - library stores `account_summary_by_tag[(account, tag)]` (ibkr_requests.py:455)
     and `positions[(account, conId or symbol)]` (ibkr_requests.py:468) — different keys,
     no `account_value` attr.
   - → Rewrite GUI accessors at kts.py:911–913 and kts.py:1599 to the library keys,
     or route through `get_positions_data` / `get_account_data`.

4. **`error()` signature.** `IBKRApp.error` is the OLD 4-arg form (ibkr_conn.py:86);
   ibapi ≥10.47 passes an extra `errorTime` and will break it. kts has the
   forward-compatible `*args` handler (kts.py:343).
   **Effect of the conn→requests merge:** this is **resolved during** that merge, not
   here. `CONN_REQUESTS_MERGE_PLAN.md` step 4 ports kts's widened `*args` `error()`
   straight into the single `IBApp` as it collapses `IBKRApp`. By the time this plan
   runs, `IBApp.error` is already forward-compatible — nothing left to do.

5. **`dlog` coupling** — keep file logging in kts only; never import it into library
   (see 1e).

---

## Suggested execution sequence

1. **[done in planning]** Read `ibkr_conn.py` — confirmed `IBKRApp` surface
   (`start`/`close` via `connect_ib`/`disconnect_ib`; old-style `error`).
2. Port market-data logic into library `IBApp.tickPrice` + `__init__` (+ `_maybe_emit_mid`); widen `IBKRApp.error`.
3. Add library `get_historical_bars()` and `crypto_marketable_limit_order()`.
4. Rewrite `kts.py`: import library `IBApp`, `contract`, `market_order`, `OrderApp`,
   new helpers; delete kts's private `IBApp` class (kts.py:241–468) and inline order build.
5. Fix the 5 blockers (`start()`, `on_tick` arity, account/position accessors,
   `error()`, `dlog`).
6. **Test path:** historical pull → calibrate → stream ticks → manual order → auto
   order, on both **STK** and **CRYPTO**.

---

## Comment-retention requirement
Both files carry dense line-by-line rationale comments. **Per project `CLAUDE.md`,
preserve every existing comment when moving code between files** — relocate the
comment with its line, don't drop it. The ported tick/order logic must keep its kts
explanations (e.g. why delayed tick types exist, why PAXOS needs IOC limits).
