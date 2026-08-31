# `ibkr_requests.py` — function reference

This is the long-form companion to [README.md](README.md). The README says what the module *is* and
lists the traps; this file walks the individual functions — what they take, how they run, what they
return, and where the sharp edges are, with line links into
[`ibkr_requests.py`](ibkr_requests.py).

**Scope of this first pass: historical data and market data only.** Account, position, order,
execution and P&L requests are deliberately not covered yet; they follow the same handshake and can
be added below as separate sections.

---

## 0. The one pattern everything follows

Every request helper in this module is the same three-step dance, and understanding it once
explains all of them. It exists because `ibapi` is asynchronous and callback-driven, while the
calling code — a notebook cell, a pipeline, a GUI handler — wants a value back.

```
  caller thread                      TWS / IB Gateway            reader thread
  ─────────────                      ────────────────            ─────────────
  1. req_id = app.next_req_id()
     event  = threading.Event()
     app.historical_data[req_id] = []
     app._hist_events[req_id] = event

  2. app.reqHistoricalData(...)  ──────────►
                                            ──── bar ────────►   historicalData()
                                            ──── bar ────────►     appends under
                                            ──── bar ────────►     app.lock
                                            ──── end  ───────►   historicalDataEnd()
                                                                   event.set()
  3. _wait_for(event, timeout)   ◄───────── unblocked
     read app.historical_data[req_id]
     clean up
```

The pieces, and why each exists:

| Piece | Where | Why |
|---|---|---|
| `app.next_req_id()` | [`:682`](ibkr_requests.py#L682) | Request ids and **order** ids share one numeric space in IB and do collide. A shared counter under the lock is the only safe source. |
| `app.lock` (an `RLock`) | [`:521`](ibkr_requests.py#L521) | Callbacks arrive on a background reader thread, not yours. Every shared mutation is guarded. See §0.1. |
| Per-request `threading.Event` | `_hist_events` | Completion is signalled **per reqId**, not by one global flag, so several symbols in flight cannot unblock each other. |
| `_wait_for(event, timeout, label)` | [`:337`](ibkr_requests.py#L337) | Blocks, then **raises `TimeoutError` with a label** rather than hanging forever. |
| `app.req_errors[req_id]` | — | TWS sends **no terminating callback for a request it refuses.** `error()` therefore records the failure *and sets the waiting event itself* — otherwise a 1-second "no such contract" becomes a 30-second stall. This is why callers must check `req_errors` after the wait returns: a refusal and a legitimately-empty result look identical otherwise. |

`connect_ib` ([`:59`](ibkr_requests.py#L59)) starts `app.run()` on a **daemon** thread, so a
forgotten `disconnect_ib` cannot keep the interpreter alive.

### 0.1 `app.lock` — what it actually does

[`ibkr_requests.py:521`](ibkr_requests.py#L521), with the full rationale in the comment block above
it at [`:499-520`](ibkr_requests.py#L499-L520).

```python
self.lock = threading.RLock()
```

**First, what it does NOT do.** It does not decide which thread receives callbacks, and it does not
keep other threads out of the callback path. Callback dispatch is `ibapi`'s business, not the
lock's: the socket connection to TWS runs on a background reader thread, that thread listens for
incoming messages and dispatches them to the matching `EWrapper` methods, and it is the only thread
that ever calls `historicalData`, `tickPrice`, `error` and the rest. That is true with or without a
lock.

**What it does do** is arbitrate access to the *shared state those callbacks write into*, because
at least two threads reach it:

- **the reader thread**, writing — `historicalData` appending bars into
  `app.historical_data[reqId]`, `tickPrice` setting `last_price` / `bid` / `ask`, `error`
  recording into `req_errors`;
- **your thread** (main, notebook cell, pipeline, Tk handler), reading and clearing — a request
  helper seeding `app.historical_data[req_id] = []` before firing, reading the bars back out after
  `_wait_for` returns, popping `_hist_events`.

Without the guard those interleave. A `list(app.historical_data[req_id])` copy taken while the
reader thread is mid-`append` is the classic case, and it fails intermittently rather than loudly —
the worst possible failure mode for a data pull that "worked yesterday". Same for the clear-then-fire
sequence: `app.open_orders = {}` at [`:2480`](ibkr_requests.py#L2480) carries exactly this comment,
that the lock is what stops the reader thread and the main thread touching that dict at the same
time.

So the rule the module follows is: **every read or write of shared app state sits inside
`with self.lock:`, on both sides.** A lock only helps if every participant takes it — one unguarded
reader defeats the guarded writer.

**Why `RLock` and not `Lock`.** An `RLock` is *reentrant*: the same thread may acquire it more than
once without deadlocking itself. A plain `Lock` would deadlock the instant one guarded block called
a helper that took the lock again — which is easy to do by accident when callbacks, request helpers
and cleanup paths all share the same state. `RLock` makes that nesting safe, at no cost to the
cross-thread guarantee, since a *different* thread still blocks normally.

**What the lock is not responsible for.** Completion signalling is a separate mechanism entirely —
that is the per-request `threading.Event` in `_hist_events`. The lock answers *"is it safe to touch
this dict right now?"*; the Event answers *"has the data finished arriving?"*. Conflating them is
the usual source of confusion, since both appear in the same four lines of every helper.

---

## 1. Historical data

### 1.1 `contract()` — the instrument descriptor

[`ibkr_requests.py:124`](ibkr_requests.py#L124)

A pure builder, no I/O, testable offline. Every historical and market-data request needs one.

```python
contract(symbol, sec_type='STK', exchange='SMART', currency='USD',
         primary_exchange=None, last_trade_date_or_contract_month=None, con_id=None)
```

| Argument | Default | Notes |
|---|---|---|
| `sec_type` | `'STK'` | `'IND'` for a cash index, `'CASH'` for FX, `'CRYPTO'`, `'FUT'`, `'OPT'`. |
| `exchange` | `'SMART'` | IBKR's smart-routing engine. |
| `primary_exchange` | `None` | **The listing venue goes here, never in `exchange`.** Putting it in `exchange` means direct routing, warning 10311, and TWS holding the order for manual confirmation. |
| `con_id` | `None` | IB's own primary key for a specific listing. A London-listed ETF and a US ETF can share a ticker, and a bare symbol re-resolve gets error 200; the `conId` from the account snapshot resolves first time. `contract_specs_from_portfolio()` ([`:156`](ibkr_requests.py#L156)) builds these straight from a portfolio frame. |

The defaults describe **a US-listed, USD-denominated, smart-routed stock or ETF.** SPY, QQQ, KMLM
and every other US ETF in this repo need no overrides at all. Anything else does.

### 1.2 `get_equity_data()` — the batch fetcher

[`ibkr_requests.py:1247`](ibkr_requests.py#L1247)

The workhorse: OHLCV for one or many symbols, optionally persisted, in one of two shapes. It owns
its own connection if you do not give it one.

#### Input

| Argument | Default | What it does |
|---|---|---|
| `symbols` | `DEFAULT_SYMBOLS` ([`:400`](ibkr_requests.py#L400)) | `str` or `list[str]`. A bare string is accepted and wrapped. |
| `duration` | `'1 Y'` | Passed through **unvalidated** as `durationStr`. See §1.4. |
| `bar_size` | `'1 day'` | `'1 day'`, `'1 hour'`, `'5 mins'`, … |
| `end_date` | `''` | End of the window. `''` becomes now, in **UTC dash notation** `%Y%m%d-%H:%M:%S` ([`:1361`](ibkr_requests.py#L1361)). The dash form carries no timezone suffix and IS UTC; the old space-without-timezone form is deprecated and draws error 2174. |
| `what_to_show` | `'TRADES'` | Price basis. **Deliberately not auto-retried** — some non-US listings carry no trade-data entitlement and return nothing for `TRADES` while `MIDPOINT` works, but a silent basis switch would change what the numbers mean. |
| `contract_specs` | `None` | `dict[symbol, kwargs]` forwarded to `contract()` ([`:1390`](ibkr_requests.py#L1390)). Symbols absent from the map keep the STK/SMART/USD defaults, so existing US-only callers are unaffected. |
| `output_format` | `'dict'` | `'dict'` or `'combined'` — see below. |
| `output_dir` | `None` | **`None` means nothing is written to disk at all** — no `mkdir`, no CSV. |
| `skip_existing` | `False` | Skip tickers whose CSV already exists in `output_dir`. Ignored when `output_dir is None` (nothing to check against). |
| `merged_filename` | `'portfolio_prices.csv'` | Only used by `'combined'`. |
| `app` | `None` | An already-connected `IBApp`. If omitted, a temporary connection is created **and closed on exit** ([`:1356`](ibkr_requests.py#L1356), `finally` at [`:1563`](ibkr_requests.py#L1563)). |
| `host` / `port` / `client_id` | `127.0.0.1` / `7497` / `123` | Only consulted when `app` is None. |

#### How it runs

1. **Normalise and skip** ([`:1316-1347`](ibkr_requests.py#L1316-L1347)) — wrap a bare string,
   resolve the save directory only if `output_dir` was given, drop already-cached tickers, and
   return an empty `{}` / empty frame early if nothing is left to fetch.
2. **Fire every request first, wait afterwards** ([`:1374-1418`](ibkr_requests.py#L1374-L1418)) —
   one `reqId` and one `Event` per symbol, `req_errors` cleared for the recycled id, then
   `reqHistoricalData` ([`:1395`](ibkr_requests.py#L1395)) with `useRTH=1`, `formatDate=1`,
   `keepUpToDate=0` (one-shot, not streaming). A `time.sleep(0.25)` between symbols respects IB
   pacing.
3. **Wait per symbol, 30s each** ([`:1421-1442`](ibkr_requests.py#L1421-L1442)) — and crucially,
   **a failed symbol is a warning, not an abort.** One unresolvable ticker used to raise
   `TimeoutError` out of the loop and discard the bars of every symbol that had already arrived.
   Failures are collected and named at the end instead. After each wait, `req_errors` is checked:
   that is what distinguishes *refused* from *legitimately empty*.
4. **Assemble one frame per symbol** ([`:1444-1490`](ibkr_requests.py#L1444-L1490)) — a `return`
   column from `close.pct_change()` ([`:1472`](ibkr_requests.py#L1472)), then `dropna` on it, which
   **discards the first bar of every series.** Columns are pinned to
   `datetime,open,high,low,close,volume,return`.
5. **Say plainly what came back** ([`:1505-1512`](ibkr_requests.py#L1505-L1512)) —
   `Fetched N/M symbols.` plus a `Missing: …` line. Partial data that looks complete is the
   dangerous outcome: a backcast silently drops the missing names and still prints a confident
   number.

Note there is **no `cancelHistoricalData`** on the success path
([`:1494-1499`](ibkr_requests.py#L1494-L1499)). The request already ended, and cancelling a
finished request makes TWS answer *"No historical data query found for ticker id:N"* through the
error callback — harmless, but it prints an alarming IB Error in the middle of an order printout.
Cancel is only correct for a still-streaming request (`keepUpToDate=1`).

#### Output

**`output_format='dict'`** (default) → `dict[str, DataFrame]`, one entry per symbol, columns
`datetime, open, high, low, close, volume, return`. A symbol that returned nothing gets an **empty
DataFrame**, not a missing key. With `output_dir` set, each symbol is written to
`stock_data_{sym}.csv` ([`:1480`](ibkr_requests.py#L1480)).

**`output_format='combined'`** ([`:1521-1557`](ibkr_requests.py#L1521-L1557)) → one wide
`DataFrame`: a `Date` column plus one close-price column per symbol. Empty symbols are skipped
rather than merged, the join is an **outer** join ([`:1532`](ibkr_requests.py#L1532)) so a
later-listing ETF carries NaN on earlier dates, and `datetime` is renamed to `Date` to match the
`portfolio_prices.csv` convention and `PanelBuilder`'s expected index name. With `output_dir` set,
one merged CSV is written ([`:1553`](ibkr_requests.py#L1553)). Returns an **empty DataFrame** if
every symbol was empty.

### 1.3 `get_historical_bars()` — the single-contract sibling

[`ibkr_requests.py:1567`](ibkr_requests.py#L1567)

```python
get_historical_bars(app, contract_obj, duration, bar_size,
                    what_to_show='TRADES', use_rth=1, end='', timeout=20)
```

Same handshake, three deliberate differences:

- **Takes a `Contract` object, not a symbol** — the caller has already built it.
- **Requires an already-connected `app`** — the caller owns the connection lifecycle. There is no
  `_ensure_connected_app` here.
- **Returns `list[dict]`, oldest first** — no DataFrame, no CSV, no `return` column. This is the
  intraday-friendly path: a live UI pulls a short calibration window and feeds the bars straight
  into a model. `orders/kts.py` is the consumer.

`use_rth` matters more here than in `get_equity_data`, which hardcodes `1`: `0` includes
extended/24h sessions, which is what crypto, FX and out-of-hours futures need. Likewise
`what_to_show='MIDPOINT'` or `'BID_ASK'` for FX and crypto, which have no trade prints at all, so
`TRADES` returns nothing.

Unlike `get_equity_data`, this one **does** call `cancelHistoricalData` in its `finally`
([`:1622`](ibkr_requests.py#L1622)), wrapped in a bare `except` — belt-and-braces cleanup for a
helper that may be called repeatedly from a GUI loop, at the cost of the occasional spurious
"no such query" line.

### 1.4 The `duration` ceiling — unverified, and treat it that way

`duration` is forwarded to TWS untouched. **Nothing in this repo validates, caps or paginates it,
and no caller here has ever asked for more than `'1 Y'`** — the default at
[`:1251`](ibkr_requests.py#L1251), and `--duration "1 Y"` at
[`cache_prices.py:102`](../../pipelines/cache_prices.py#L102).

Whether TWS answers `'30 Y'` for a daily bar, refuses it, or silently returns a shorter series is
**not established for this account.** The standing project decision is that on IB questions the
running TWS outranks every written source — Phase 4 spent two plans on an executions limit taken
from documentation that proved wrong. Probe before citing.

Three outcomes to distinguish when you do probe, because they surface differently:

| Outcome | How it shows |
|---|---|
| Refusal | `error()` records it, sets the event, and the loop prints `Skipped SPY: IB error <code> - <text>` ([`:1438-1442`](ibkr_requests.py#L1438-L1442)). Capture the text verbatim. |
| **Silent truncation** | Bars arrive, `historicalDataEnd` fires, nothing prints, and the series is simply shorter than asked. The only detection is comparing the returned first bar against the requested start. |
| Timeout | 30s per symbol ([`:1421`](ibkr_requests.py#L1421)); that symbol is skipped, the rest still return. |

### 1.5 The callbacks

**`historicalData(reqId, bar)`** — [`:955`](ibkr_requests.py#L955). Appends
`{datetime, open, high, low, close, volume}` into `app.historical_data[reqId]` under `app.lock`.
Keyed by `reqId` because one app may have several historical requests in flight and each symbol's
bars must stay separate until the outer helper assembles them.

**`historicalDataEnd(reqId, start, end)`** — [`:977`](ibkr_requests.py#L977). Sets that reqId's
event. Four lines, and the entire completion signal.

---

## 2. Market data (streaming quotes)

There is **no `get_market_data()` helper.** Streaming is a subscription, not a request-response
round trip, so there is nothing for a blocking helper to return. The subscription is opened
directly against the app by the consumer — in this repo, `orders/kts.py`
([`kts.py:1746`](../../../orders/kts.py#L1746) and [`:1769`](../../../orders/kts.py#L1769)) — and
this module supplies the **callbacks** that turn ticks into state.

### 2.1 Opening a subscription

```python
app.reqMarketDataType(code)   # MUST come first — see below
app.reqMktData(req_id, contract_obj, "", False, False, [])
```

`reqMarketDataType` selects the tier and **must be called before `reqMktData`**, otherwise the
request goes out against whatever tier was last set. Tier codes: `1` live, `2` frozen, `3` delayed,
`4` delayed-frozen.

### 2.2 `tickPrice()` — why it listens on six tick types, not one

[`ibkr_requests.py:899`](ibkr_requests.py#L899)

IB delivers **different `tickType` IDs depending on the data tier**:

| | BID | ASK | LAST |
|---|---|---|---|
| Live (paid entitlement) | 1 | 2 | 4 |
| Delayed (free, ~15 min lag) | 66 | 67 | 68 |

Frozen and delayed-frozen reuse their non-frozen counterparts' IDs. **Listening only on type 4
would miss every delayed feed entirely** — crypto on a Sunday arrives as type 68, gets ignored,
`on_tick` never fires, and any downstream model sits frozen looking healthy.

Behaviour, in order:

- `price <= 0` → return immediately. Bad ticks must never reach a model.
- `4` or `68` → set `self.last_price`, call `on_tick(price, datetime.now())`.
- `1` or `66` → set `self.bid`, then try `_maybe_emit_mid()`.
- `2` or `67` → set `self.ask`, then try `_maybe_emit_mid()`.

`on_tick` is guarded for `None` throughout: `IBApp` is routinely used with no consumer at all
(historical-only pulls, REPL sessions, account scripts), and calling `None(...)` would raise
`TypeError` **on the reader thread**, which can destabilise the `EReader` loop.

### 2.3 `_maybe_emit_mid()` — synthesising a last price

[`ibkr_requests.py:930`](ibkr_requests.py#L930)

FX, and frequently crypto on PAXOS, **never send LAST ticks** — only BID/ASK. Without this,
`on_tick` would never fire for those instruments. The method synthesises a mid from the most recent
bid/ask pair and routes it through `on_tick`.

The gating is asymmetric on purpose:

- **Equities (`STK`) prefer real prints.** If a `last_price` has arrived, mid synthesis is
  suppressed, so `on_tick` is driven by genuine trades.
- **`CASH` and `CRYPTO` keep emitting mids regardless**, because bid/ask *is* the continuous signal
  there and a stale cached LAST would otherwise wedge the stream. `self.sectype`
  ([`:602`](ibkr_requests.py#L602)) is set by the GUI immediately before `reqMktData` for exactly
  this decision.

Also skipped: a crossed or locked book (`ask <= bid`), and a duplicate mid where neither side moved.

---

## 3. CSV vs parquet — where each is written, and why

### What each layer actually does

| Layer | Writes | Where |
|---|---|---|
| `get_equity_data` | **CSV only** — `stock_data_{sym}.csv` ([`:1480`](ibkr_requests.py#L1480)) or one merged CSV ([`:1553`](ibkr_requests.py#L1553)) | `output_dir`, in practice `data/raw/market/` |
| `get_historical_bars` | nothing | — |
| `cache_prices.py` | **parquet** ([`cache_prices.py:121`](../../pipelines/cache_prices.py#L121)) | `data/processed/prices_<tag>.parquet` |

**There is no argument to `get_equity_data` that produces a parquet.** Asking for one is asking for
the pipeline:

```bash
# needs TWS/Gateway running
python src/pipelines/cache_prices.py --portfolio spy_kmlm --duration "1 Y" --bar-size "1 day"

# offline: re-normalise the CSV a previous pull already wrote
python src/pipelines/cache_prices.py --portfolio spy_kmlm --from-csv
```

`cache_prices.py` calls `get_equity_data(output_format='combined', output_dir=data/raw/market)`
([`cache_prices.py:75`](../../pipelines/cache_prices.py#L75)) — so the raw CSV stays in sync and a
later `--from-csv` run has something to replay — then `_tidy`
([`cache_prices.py:45`](../../pipelines/cache_prices.py#L45)) converts to a `DatetimeIndex` named
`ts` with float columns, and writes the parquet.

The universe is **never hardcoded in the pipeline**: `--portfolio` names a weight vector in
`config/asset_universe.yaml` and its tickers become the fetch list. Adding an instrument to a study
is a YAML edit, not a code edit, and no two files can disagree about what counts as a hedge.

### Why parquet for the processed layer

- **Types survive the round trip.** The raw CSVs store dates as bare integers (`20250414`) and
  every column as text; every reader has to re-guess. A parquet carries its schema, so a
  `DatetimeIndex` named `ts` with `float64` columns reads back as exactly that.
- **Float precision is exact**, not reformatted through a decimal string.
- **Columnar and compressed.** A wide multi-decade panel can have one symbol read out of it without
  parsing the other columns — which is the difference between a fast study and a slow one once the
  panel stops being 250 rows.
- **It is the contract the accounting stack expects.** `PortfolioSimulator` takes a wide price frame
  and does not care where it came from; the parquet is what makes a run reproducible with the
  gateway closed.

### Why CSV keeps its place in `data/raw/`

`data/raw/` is **append-only** by repo convention — transformations happen in code and land in
`data/processed/`. CSV is human-diffable, greppable, and survives a pandas version change without a
compatibility question. It is the audit trail; the parquet is the working copy.

### One caveat that matters for long history

`_tidy` ends with `out.astype(float).ffill().dropna(how="any")`
([`cache_prices.py:55`](../../pipelines/cache_prices.py#L55)). Its own comment gives the rationale —
*"drop any leading rows still missing a quote — the sim must never mark a leg at a made-up price"* —
which is correct for a simulator and wrong for a coverage report. On a **ragged** panel (series with
different inception dates) it truncates every column to the latest first bar. Cache SPY-from-1993
beside QQQ-from-1999 and the parquet starts in 1999, silently.

This is the same defect as `PanelBuilder._load`
([`viz/panel.py:165-167`](../viz/panel.py#L165-L167)), one layer earlier. Both are in scope for
Phase 11 — see
[`.paul/phases/11-long-history-data/CONTEXT.md`](../../../.paul/phases/11-long-history-data/CONTEXT.md).

---

## 4. Sharp edges worth re-reading before you debug

Repeated from [README.md](README.md) because they cost real time:

- **`error()` is `*args`.** ibapi ≥10.47 passes `error(reqId, errorTime, errorCode, errorString)`;
  taking "the first int" reads an epoch-ms timestamp as the error code. Fields are located by
  magnitude instead, since no IB error code exceeds five digits.
- **A refusal is the only callback you get.** No terminating callback follows a rejected request.
- **Request ids and order ids share one numeric space** and do collide. `req_errors[15]` is safe to
  read only for an id you just submitted.
- **`exchange` vs `primaryExchange`.** Listing venue in `primaryExchange`, always.
- **Every message TWS sends about an order is retained** in `req_error_log` / `req_notice_log` /
  `req_messages` for the life of the connection — so a print lost to a finished notebook cell can
  still be recovered by re-reading.

---

*Covers historical and market data as of 2026-08-31. Account, position, order, execution and P&L
requests follow the same handshake and are not documented here yet.*
