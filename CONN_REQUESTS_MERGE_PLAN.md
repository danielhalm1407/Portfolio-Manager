# Merge Plan — fold `ibkr_conn.py` into `ibkr_requests.py`

## Goal
Collapse the two ingestion files into **one**. `ibkr_requests.py` should hold *all*
IBKR infrastructure — the connection stage at the very top, then the single `IBApp`
definition, then the request helpers. `ibkr_conn.py` is deleted.

Right now there is an unnecessary base-class indirection:
- `ibkr_conn.py` defines `IBKRApp(EWrapper, EClient)` (the base) + `connect_ib` /
  `disconnect_ib` module helpers.
- `ibkr_requests.py` does `from .ibkr_conn import IBKRApp, connect_ib, disconnect_ib`
  and defines `IBApp(IBKRApp)`.

`IBKRApp` only adds: `connected`/`data`/`finished` flags, `nextValidId`,
`connectionClosed`, `error`. `IBApp` already overrides `nextValidId` and re-implements
most state. The base earns its keep only if multiple subclasses share it — but there
is exactly **one** subclass (`IBApp`). So: **fold `IBKRApp` into `IBApp` and let
`IBApp` inherit `EWrapper, EClient` directly** (the same pattern `orders/kts.py`
already uses for its own `IBApp`).

## Pre-checks (already done)
- **Importers of `ibkr_conn` / `IBKRApp`:** only `ibkr_requests.py`. No pipelines,
  notebooks, or other modules import them.
- **Package re-exports:** `src/portutils/ingestion/__init__.py` re-exports only from
  `ibkr_requests` (`IBApp, OrderApp, get_equity_data, get_account_data`) — `ibkr_conn`
  is **not** re-exported. Deleting it breaks no public import.
- **Legacy `data` / `finished` flags:** defined in `IBKRApp.__init__`
  (ibkr_conn.py:54, 59) but **never read** anywhere in `ibkr_requests.py`. The only
  mention is a *stale comment* at ibkr_requests.py:819–820. The real helpers use
  per-request `threading.Event`s instead. → Drop both flags on merge; fix the stale
  comment.

---

## Steps

### 1. Imports at top of `ibkr_requests.py`
Add what `ibkr_conn.py` needs (ibkr_requests.py:18–29 block):
```python
from threading import Thread          # for connect_ib's daemon reader thread
from ibapi.client import EClient      # outgoing request side
from ibapi.wrapper import EWrapper    # incoming callback side
```
`time`, `itertools`, `threading`, `pandas`, `Contract`, `Order` are already imported.
**Remove** the line `from .ibkr_conn import IBKRApp, connect_ib, disconnect_ib`
(ibkr_requests.py:29).

### 2. Merge the module docstring
Fold the "why a base class / EClient vs EWrapper" architecture note from
`ibkr_conn.py:1–25` into the top docstring of `ibkr_requests.py:1–16`. Keep both
explanations (per the comment-retention rule) — the dual-class rationale plus the
existing "two separate concerns" note.

### 3. Paste connection lifecycle at the very top (after imports)
Move `connect_ib` (ibkr_conn.py:106–151) and `disconnect_ib` (ibkr_conn.py:154–164)
verbatim — **all comments intact** — into a new section in `ibkr_requests.py`, placed
*above* the existing "Helper functions" section so the connection stage reads first:
```
# ═══ Connection lifecycle (host/port/handshake) ═══
def connect_ib(...): ...
def disconnect_ib(...): ...
```
`IBApp.start()` (ibkr_requests.py:271) and `IBApp.close()` (:282) already call these by
bare name, so they keep working once the functions live in the same module.

### 4. Collapse `IBKRApp` into `IBApp`
- **Class header:** `class IBApp(IBKRApp):` → `class IBApp(EWrapper, EClient):`
  (ibkr_requests.py:175).
- **`__init__` first line:** replace `super().__init__()` (ibkr_requests.py:202) with
  `EClient.__init__(self, self)`. Keep the existing long comment block (it already
  explains the dual-inheritance / `EClient.__init__(self, self)` reasoning, kts-style).
- **Add the base flag** that used to come from `IBKRApp.__init__`:
  `self.connected = False` (near the other state containers). **Do NOT** re-add
  `self.data` / `self.finished` — they are dead (see pre-checks); also delete the
  stale comment at ibkr_requests.py:819–820 that references them.
  → For the **full attribute roster** (what stays, what drops, what arrives later) and
  the **per-attribute comment-source decisions**, see
  *"`IBApp.__init__` — complete attribute inventory + comment plan"* below, governed by
  the *"Comment-combination policy"* section.
- **`nextValidId`:** currently `IBApp.nextValidId` (ibkr_requests.py:363) sets
  `self.next_order_id` then calls `super().nextValidId(orderId)` to flip
  `connected=True`. With no `IBKRApp` parent, set the flag directly:
  ```python
  def nextValidId(self, orderId):
      # Set next_order_id BEFORE marking connected so the connect_ib poll loop
      # cannot observe connected=True while next_order_id is still None.
      self.next_order_id = orderId
      self.connected = True
  ```
- **Bring in `connectionClosed`** (from ibkr_conn.py:76–82) as an `IBApp` method —
  keep its comment about resetting `connected` so stale True is visible.
- **`error()` — resolve the forward-compat blocker here.** `IBKRApp.error`
  (ibkr_conn.py:86) is the OLD fixed 4-arg signature
  `(reqId, errorCode, errorString, advancedOrderRejectJson='')` and breaks on
  ibapi ≥10.47 (which passes an extra `errorTime`). Port the **widened `*args`
  handler from `orders/kts.py:343`** into `IBApp.error` instead, keeping the
  info-code filter (2104/2106/2158, plus kts's 2176) so routine farm pings stay
  quiet. This is the single canonical `error()` from now on.

### 5. Retire `ibkr_conn.py` (move to `sandbox/`, do not delete)
After step 4, nothing in `src/` references it. Rather than delete outright, **move it
to `sandbox/ibkr_conn.py`** (`git mv`) so the original is preserved as a fallback while
the merge beds in. It can be deleted for good once the merged module is trusted.

### 6. Verify
- `python -c "from portutils.ingestion import IBApp, OrderApp"` imports clean.
- `python -c "import portutils.ingestion.ibkr_requests"` — no `ibkr_conn` import error.
- Grep the repo for `ibkr_conn` / `IBKRApp` → expect hits only in: the plan docs,
  the preserved `sandbox/ibkr_conn.py`, and one descriptive comment in
  `ibkr_requests.py` (`# IBApp has no IBKRApp parent to delegate to.`). **No live
  import** of either name anywhere in `src/`.
- Smoke: `IBApp().start(...)` connects, `nextValidId` flips `connected`, `error()`
  accepts a 5-arg call without raising.

---

## Resulting file shape (`ibkr_requests.py`)
```
"""module docstring — merged: connection rationale + request concerns"""
imports (EClient, EWrapper, Thread, time, itertools, threading, pandas, Contract, Order)

# ═══ Connection lifecycle ═══
connect_ib(...)
disconnect_ib(...)

# ═══ Helper functions ═══
contract(), market_order(), limit_order(), _wait_for(), _coerce_account_values(),
connection_status(), reconnect(), _ensure_connected_app(), DEFAULT_* constants

# ═══ Single IBApp class (was IBKRApp + IBApp) ═══
class IBApp(EWrapper, EClient): ...   # connection flags + all request callbacks

# ═══ Request workflows ═══
get_equity_data(), get_account_data(), get_positions_data(), ... OrderApp
```

---

## Helper functions — what comes in, from where

### From `ibkr_conn.py`
The only module-level code in `ibkr_conn.py` besides the `IBKRApp` class is **two**
helpers — both move verbatim into the connection-lifecycle section at the top of
`ibkr_requests.py` (step 3):
- `connect_ib(app, host, port, client_id)` (ibkr_conn.py:106) — daemon-thread reader + handshake poll.
- `disconnect_ib(app)` (ibkr_conn.py:154).

There are **no data-processing helpers** in `ibkr_conn.py` to migrate. The three
`IBKRApp` *callbacks* (`nextValidId`, `connectionClosed`, `error`) are not standalone
helpers — they fold into `IBApp` in step 4.

### From `kts.py`
This conn→requests merge does **not** pull in kts's data helpers
(`estimate_ar1`, `noise_lever_to_scale`, `_maybe_emit_mid`, `tickPrice` delayed
tiers) — those belong to the later kts→library merge (`MERGE_INFRASTRUCTURE_PLAN.md`
§1). **One** kts item is needed here, and only because we are rewriting the callback
during the collapse:
- **The widened `*args` `error()` handler** (kts.py:343) becomes the single canonical
  `IBApp.error` (step 4), replacing the brittle fixed-arg `IBKRApp.error`.

No kts helper needs *amending* for this merge; they are untouched until the GUI merge.

---

## Comment-combination policy (best-practice-first)

When a thing — an attribute, an init call, a helper — exists in more than one of the
three files, **do not concatenate all the comments**. Instead:

1. **Pick the base.** Identify the file where that thing is in its *current
   best-practice form* (usually the most recent / most correct usage). Its comment is
   the **base** explanation and goes first, largely verbatim.
2. **Layer only genuinely new nuance.** Scan the *other* files' comments for the same
   thing. Add a clause **only** if it explains something the base comment does not
   (a different keying scheme, a thread-safety caveat, a reason-why the base omits).
   If another file says the same thing in different words, **drop it** — no duplication.
3. **Strip what no longer applies.** If the merge changes mechanics (e.g. we drop
   `super()`), delete the parts of the base comment that described the old mechanic,
   but **keep the conceptual rationale** that is still true.

**Worked example — `EClient.__init__(self, self)` / IBApp construction.**
The best explanation lives in **`ibkr_requests.py:187–211`** — use it as the base.
Because we are dropping `super().__init__()` in favour of a direct
`EClient.__init__(self, self)`:
- **Keep** the part explaining *why you pass `self` twice* (the object is both the
  EClient that sends and the EWrapper that receives — dual inheritance).
- **Keep** the sentences arguing that `super()` is "more robust to future changes in
  the class hierarchy" BUT caveat this with the fact that we are currently not using this,
  and could just in future choose to swap out with this — no longer too relevant once the base class is gone.
- **Layer in** nothing extra from kts.py:264–271, because it makes the *same* dual-self
  point (it would be pure duplication). The conn version (ibkr_conn.py:47–49) is the
  terser form of the same idea — also not added.

---

## `IBApp.__init__` — complete attribute inventory + comment plan

Legend: ✓ = present, — = absent. "Base" = which file's comment leads, per the policy
above. "Lands in this merge" = whether the attribute exists in the merged library
`IBApp` after the conn→requests collapse (vs. arriving later from the kts→library
merge, vs. being dropped).

| Attribute / init call | reqs | conn (base cls) | kts | Lands here? | Comment base + layering |
|---|:--:|:--:|:--:|---|---|
| `EClient.__init__(self, self)` | ✓ (202) | ✓ (50) | ✓ (271) | ✓ | **Base: reqs (187–202)**. Keep dual-self rationale; drop `super()`-robustness lines; no add from kts/conn (duplicate). |
| `self.connected = False` | — (inherited) | ✓ (63) | ✓ (283) | ✓ | **Base: conn (60–63)** — ties flag to `nextValidId`. Layer kts nuance (282): kept for quick local checks / gating startup. |
| `self.next_order_id = None` | ✓ (213) | — | ✓ (286) | ✓ | **Base: reqs (210–213)** — shared connection-safe sequence. Layer kts (284–286): seeded by `nextValidId`, required by `placeOrder`, incremented per order. |
| `self.on_tick = on_tick` | ✓ (204) | — | ✓ (287–314) | ✓ | **Base: kts (287–313)** — by far the clearest (callable type, who calls it, what it feeds). reqs comment is sparse → superseded. |
| `self.lock = threading.RLock()` | ✓ (207) | — | — | ✓ | **Base: reqs (205–207)** — sole source. |
| `self._req_id_source = itertools.count(1)` | ✓ (212) | — | — | ✓ | **Base: reqs (208–212)**; cross-ref the `next_req_id()` comment (286–299). |
| `self._hist_events / _account_events / _pnl_events` | ✓ (217–219) | — | — | ✓ | **Base: reqs (215–219)**. Per-request completion events — *supersede* kts's single `hist_done`. |
| `self.managed_accounts = []` | ✓ (222) | — | — | ✓ | **Base: reqs.** |
| `self.last_price = None` | ✓ (223) | — | ✓ (316) | ✓ | **Base: kts (315)** — written by `tickPrice` on LAST. reqs equivalent, no extra nuance. |
| `self.bid / self.ask` | ✓ (224–225) | — | ✓ (317–319) | ✓ | **Base: kts (317–319)** — adds the "used to synthesize mid" rationale reqs lacks. |
| `self.historical_data = {}` | ✓ (227–229) | — | ✓ (320–323) | ✓ | **Base: reqs (227–229)** ≈ kts (320–323); both say "keyed by reqId, avoid collisions". Combine into one, no duplication. |
| `self.account_summary_rows / account_summary_by_tag` | ✓ (233–234) | — | — | ✓ | **Base: reqs.** |
| `self.positions = {}` | ✓ (238) | — | ✓ (329) | ✓ | **Base: reqs (236–238)** — keyed by `(account, conId or symbol)`. kts keys by bare symbol → *superseded*; note the richer key in the comment. |
| `self.portfolio / open_orders / order_status / executions / account_pnl / contract_pnl` | ✓ (239–244) | — | — | ✓ | **Base: reqs.** |
| `self.positions_event / open_orders_event / account_updates_event` | ✓ (248–250) | — | — | ✓ | **Base: reqs.** |
| `self.data = []` | — | ✓ (54) | — | **DROP** | Dead (never read; pre-checks). Delete attr + the stale comment at reqs:819–820. |
| `self.finished = False` | — | ✓ (59) | — | **DROP** | Dead — same as above. |
| `self.hist_done = threading.Event()` | — | — | ✓ (326) | **DROP** | Single-event pattern superseded by per-req `_hist_events`. kts loses it when `refresh_30m` switches to `get_historical_bars` (see other plan §2). |
| `self._last_tick_source` / `self.sectype` / `self._last_emitted_mid` / `self.account_value` | — | — | ✓ (335/339/341/332) | **NOT here** → kts merge | Out of scope for this merge. These four kts-only attrs (market-data provenance/gating + GUI net-liq) are **owned by `MERGE_INFRASTRUCTURE_PLAN.md` §1b**, which holds the per-attribute roles and comment-base decisions. |

**Summary:** the merged library `IBApp.__init__` is essentially today's
`ibkr_requests.py` body (it already holds 20 of the ~23 live attributes), **plus**
`self.connected = False` brought down from the base, **minus** the two dead flags.
The four kts-only attrs (`_last_tick_source`, `sectype`, `_last_emitted_mid`,
`account_value`) are *not* part of this merge — they enter later from kts and are now
fully specified in `MERGE_INFRASTRUCTURE_PLAN.md` §1b. So most comments stay as the
strong `ibkr_requests.py` ones; only `connected`, `next_order_id`, `on_tick`,
`bid/ask`, and `last_price` get a kts/conn nuance layered on top per the table.

---

## Comment-retention requirement
Per project `CLAUDE.md`: move every comment with its code. The dual-class rationale,
the daemon-thread explanation in `connect_ib`, and the kts-style reasoning already in
`IBApp` must all survive the collapse. Update (don't delete) the `nextValidId` comment
to reflect that it now sets `connected` itself. Apply the **comment-combination
policy** above wherever an attribute or helper exists in more than one file.
