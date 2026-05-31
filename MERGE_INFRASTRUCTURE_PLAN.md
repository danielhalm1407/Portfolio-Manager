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

> ⚠️ **Line-number caveat.** The `ibkr_requests.py` line refs below predate the
> conn→requests merge and have drifted **~+85–95 lines** (the `connect_ib`/`disconnect_ib`
> block, the merged docstring, and the expanded `IBApp.__init__` comments all sit above
> them now). `kts.py` refs are unaffected. **Locate library symbols by name**
> (`market_order`, `reserve_order_id`, `OrderApp.place_order`, …), not by the stale
> line number, when executing.

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
- STK/CASH market order: `market_order()` already covers it.
- Crypto IOC marketable-limit + tick-snap logic (kts.py:1525–1566) is **missing** from
  the library. Add a pure builder:
```python
def crypto_marketable_limit_order(action, qty, ref_price, tick=0.50, buf_bps=10):
    """IOC limit that crosses the spread by buf_bps and snaps to tick (PAXOS)."""
```
  Holds the logic at kts.py:1545–1566. No Tk, fully unit-testable.
- **Comment retention (critical here).** kts.py:1525–1566 carries the *only* written
  explanation of the PAXOS order rules — why MKT is rejected, why IOC marketable-limit,
  the 10 bps buffer rationale, the `minTick`/`$0.50` snap (ceil for BUY, floor for SELL),
  and `outsideRth=True` for 24/7 crypto. **Relocate all of those comments verbatim into
  the new builder.** They have no equivalent in the library today, so nothing is being
  "combined" — they would simply be lost if not moved.
- **The `ref_price` None-check stays in the GUI, not the builder.** The builder is pure
  and assumes a valid `ref_price`. kts's "no top-of-book → fall back to last_price →
  else `messagebox.showerror`" guard (kts.py:1538–1544) is Tk-coupled, so it remains in
  `place_trade` *before* the builder is called.

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
(kts.py:899 block). `OrderApp.__init__` requires `app.connected` — true there.

**3e. `eTradeOnly` / `firmQuoteOnly` — fix the library builders (BLOCKER, easy to miss).**
The library `market_order()` (and therefore `limit_order()`, which builds on it) set
`order.eTradeOnly = False` and `order.firmQuoteOnly = False` **unconditionally**
(ibkr_requests.py:153–154). Those attributes were **removed from the `Order` class in
ibapi ≥10.19**; on newer builds assigning them raises `AttributeError` or makes TWS
reject with error 321. kts already learned this and guards with `hasattr`
(kts.py:1571–1579). → **Port the `hasattr` guard into the library builders** so the
same code runs on old and new ibapi:
```python
if hasattr(order, "eTradeOnly"):
    order.eTradeOnly = False
if hasattr(order, "firmQuoteOnly"):
    order.firmQuoteOnly = False
```
Bring kts's explanatory comment (kts.py:1571–1575) with it. Without this, the very
first order placed through the merged path can fail on a modern ibapi install.

**3f. Don't drop the CLOSE path.** §3d shows only the OPEN case. `place_trade(0)`
(kts.py:1507–1517) flattens by sending the opposite-side order for `abs(self.position)`
units, and for crypto must *still* go through `crypto_marketable_limit_order` (PAXOS
rejects MKT on the close leg too). The refactor must route the close leg through the
same builders: compute `action = "SELL" if position > 0 else "BUY"`,
`qty = abs(position)`, then pick `market_order` vs `crypto_marketable_limit_order`
exactly as the open leg does. Keep the "no position to close" guard + messagebox in the
GUI.

---

## Blockers to reconcile (the real work)

1. **Two `IBApp` base classes.**
   **Effect of the conn→requests merge:** this blocker is *largely dissolved*. After
   `CONN_REQUESTS_MERGE_PLAN.md`, the library `IBApp` inherits `EWrapper, EClient`
   **directly** — exactly the same base kts.py:241 already uses. So there is no longer
   a base-class mismatch to reconcile; kts simply imports the one library `IBApp`.
   What remains is the *connection entry point*. kts's `KalmanTradingApp.connect_ib`
   (kts.py:887–917) is two things glued together — split them:

   **Part 1 — raw connection mechanics (kts.py:892–898): DELETE, replace with `start()`.**
   `self.ib.connect(host, port, 98)` + daemon `Thread(target=self.ib.run)` + a
   `50×0.1s` (5 s) poll on `self.ib.connected`. **None of this is unique** — the library
   does all of it and more:
   - module `connect_ib` = same connect + daemon thread + poll, **plus** a
     `serverVersion() > 0` handshake guard kts lacks, and a `100×0.1s` (10 s) wait.
   - `start()` wraps it and **auto-retries client_id on error 326** (123→124→125);
     kts hardcodes `98` with no retry.
   So the library is a strict superset. → Replace lines 892–898 with a single
   `self.ib.start(host=self.host_var.get(), port=int(self.port_var.get()), client_id=…)`
   inside a `try/except` that routes failure to `messagebox.showerror` (preserving kts's
   existing error popup).

   **Part 2 — GUI side-effects + post-connect seeding (kts.py:899–917): KEEP in the GUI.**
   Setting the GUI `connected` flag, `self.order_id`, enabling/disabling the four
   buttons, the status label, and the `reqAccountSummary(9001,…)` / `reqPositions()`
   seeding are Tk-coupled and app-specific — they do **not** belong in the library and
   stay in `KalmanTradingApp.connect_ib`. ⚠️ The seeding *reads* (`self.ib.positions[sym]`
   keyed by bare symbol, kts.py:912; and `account_value`) **break against the library's
   shapes** — fix per **Blocker #3** (positions keyed by `(account, conId or symbol)`;
   no `account_value` attr unless added per §1b).

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

6. **reqId collision: counter starts at 1, kts hardcodes `reqId=1` for the stream.**
   The library hands out request IDs from `self._req_id_source = itertools.count(1)`
   (ibkr_requests.py:346) via `next_req_id()` — so the **first** allocated id is `1`.
   kts uses a **literal `reqId=1`** for the live market-data subscription
   (`reqMktData(1, …)` / `cancelMktData(1)` — kts.py:1024, 980, 934) and a literal
   `reqId=2` for historical (kts.py:1288). Once kts uses library helpers
   (`get_historical_bars` → `next_req_id()`), the first historical pull draws id `1`
   and **collides with the live market-data stream** — TWS will mis-route ticks/bars.
   → Allocate the market-data stream id from the same source: store
   `self._mkt_data_req_id = self.ib.next_req_id()` once when opening the stream and use
   that variable for both `reqMktData` and `cancelMktData`, instead of the literal `1`.
   (Alternatively reserve `1` and start the counter higher — but routing *everything*
   through `next_req_id()` is the consistent fix.)

---

## Suggested execution sequence

1. **[done in planning]** Read `ibkr_conn.py` — confirmed `IBKRApp` surface
   (`start`/`close` via `connect_ib`/`disconnect_ib`; old-style `error`).
2. Port market-data logic into library `IBApp.tickPrice` + `__init__` (+ `_maybe_emit_mid`).
   (`error()` is already widened — done in the conn→requests merge.)
3. Add library `get_historical_bars()` and `crypto_marketable_limit_order()`; **fix the
   `eTradeOnly`/`firmQuoteOnly` guard in `market_order`/`limit_order`** (§3e).
4. Rewrite `kts.py`: import library `IBApp`, `contract`, `market_order`,
   `crypto_marketable_limit_order`, `OrderApp`, new helpers; delete kts's private
   `IBApp` class (kts.py:241–468) and inline order build. Route the live market-data
   subscription through `next_req_id()` (§Blocker #6). Keep the CLOSE path on the
   builders (§3f).
5. Fix the remaining blockers (`start()`, `on_tick` arity, account/position accessors,
   `dlog`, reqId collision).
6. **Test path:** historical pull → calibrate → stream ticks → manual order → auto
   order, on both **STK** and **CRYPTO**. Verify the first historical reqId does not
   clash with the stream, and that an order places without an `eTradeOnly`
   `AttributeError` on the installed ibapi version.

---

## Comment-retention requirement
Both files carry dense line-by-line rationale comments. **Per project `CLAUDE.md`,
preserve every existing comment when moving code between files** — relocate the
comment with its line, don't drop it. The ported tick/order logic must keep its kts
explanations (e.g. why delayed tick types exist, why PAXOS needs IOC limits).
