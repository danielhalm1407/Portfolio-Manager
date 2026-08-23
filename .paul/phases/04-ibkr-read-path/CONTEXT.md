# Phase Context

**Phase:** 04 — IBKR read path (reopened for 04-05)
**Generated:** 2026-08-03
**Status:** Ready for planning

## Why the phase is reopening

Phase 4 closed on 2026-08-02 with `get_executions_data` believed correct and its empty results
explained by the midnight ceiling. On 2026-08-03 a live run disproved half of that. Five fills
executed today — visible in the TWS Trade Log — and cell 15 still returned nothing. The ceiling was
never the active cause; **client-id scoping** was.

Changing the client id to the one that actually placed the orders returned all five fills, which
both confirms the diagnosis and re-confirms the ceiling (`days_back=7`, every row stamped
2026-08-03).
f
That success then exposed a second, independent defect in the same path: the `ts` column does not
agree with what TWS displays, and not by a constant offset.

| Symbol | Venue | TWS Trade Log | `ts` | Delta |
|--------|-------|---------------|------|-------|
| BARC | LSE | 08:00:10 | 09:00:10 | +1h |
| AINF | LSEETF | 08:00:12 | 09:00:12 | +1h |
| HSBA | LSE | 08:00:19 | 09:00:19 | +1h |
| 5MVL | IBIS2 | 08:04:45 | 09:04:45 | +1h |
| LNG | NYSE | 14:30:25 | 09:30:25 | −5h |

Four rows shift one way and one the other, so no single timezone pair explains the table. `ts`
currently has no verified meaning, and the sort at `ibkr_requests.py:2462` is ordering stamps that
are not on a common clock. LNG landing last is luck, not ordering.

## Goals

- **Goal 1 — Client-id scoping becomes explicit and self-diagnosing.** `reqExecutions` returns only
  executions for orders placed by the requesting connection's own client id, unless that connection
  uses the Master API client ID configured in TWS. Nothing in this repo has ever acknowledged that.
  The read path must stop returning a silent empty frame when the real answer is "you asked as the
  wrong client". Includes capturing `execution.clientId`, which is currently discarded, so a fill
  can say for itself who placed it.

- **Goal 2 — `ts` gains verified timezone semantics.** Preserve whatever zone information TWS sends,
  parse to a real tz-aware instant, and make cross-venue sorting meaningful. Driven by evidence from
  the raw payload, not by inference from the table above.

- **Goal 3 — The terminal debug path becomes genuinely transparent.** Make it possible to see IB's
  raw callback payload rather than this repo's summary of it, and make the runner safe and readable
  to use repeatedly. This is what makes goals 1 and 2 provable rather than argued.

## Approach

### The core finding that shapes all three goals

Callbacks do not print anything on their own. A callback is a Python method on the `EWrapper`
subclass invoked on the reader thread; `error()` reaches the terminal only because
`ibkr_requests.py:780` is written to print it — which is exactly why errors 2174 and 10314 were
catchable and nothing else has been. `execDetails` prints nothing.

The payload is then destroyed at the callback boundary. `ibkr_requests.py:1159-1180` hand-picks 13
fields off the `Execution` object into a dict; the object is garbage immediately after. Everything
else IB sent is gone in-process, long before `get_executions_data` builds the frame. Discarded
fields include `clientId`, `acctNumber`, `exchange`, `lastLiquidity`, `orderRef` — and any timezone
token riding on `time`.

So no logging level, terminal flag or downstream change can recover it. It must be captured at the
callback, and `repr(execution)` dumps every field `ibapi` holds in one line.

### Direction

- Capture before parse. A debug switch that emits `repr(execution)` and `repr(contract_obj)` from
  `execDetails` gives the raw `time` string exactly as TWS sent it, settling goal 2 empirically in
  one run.
- Widen the captured dict to include the fields that answer operational questions — `clientId`
  first, since it directly serves goal 1.
- Keep the raw `time` string in the frame alongside the parsed `ts`, so a future parse change can
  always be checked against the original.
- Runner changes to `orders/run_cells_for_execution_rec.py`:
  - **Settle before printing.** Callbacks land on the reader thread while the main thread sits in
    `_wait_for`; the two print streams interleave, which is the "not transparent enough" complaint.
    A short settle after `execDetailsEnd` separates raw traffic from rendered results.
  - **Auto-disconnect.** A watchdog tearing the connection down after ~30s, plus a `finally` so a
    traceback cannot leak it either. The runner must never exit still connected — a loose client-161
    connection is what makes the *next* run behave strangely.

### Boundaries

- Flex Web Service is **parked** as its own plan. It is an integration against a different service,
  not a fix to this one, and folding it in would make 04-05 unshippable.
- The midnight ceiling is settled and documented; 04-05 does not revisit it.
- Comment retention is mandatory. The docstring at `ibkr_requests.py:2336-2356` and the error-parsing
  block at `ibkr_requests.py:789-804` carry hard-won rationale and must survive verbatim.

## Constraints

- TWS must be running for every verification leg. It is running and answering as of 2026-08-02.
- Live-order cells stay behind the runner's `--allow-sending` guard; this plan touches only the read
  path and cell 15.
- `ARM_LIVE = True` is still committed in `orders/rebalance_live_debug.py:67` and
  `test_debug_cell_script_is_disarmed_and_gated` fails by design as the reminder. Set it back to
  `False` when live work is finished.
- Library code in `src/portutils/` stays import-safe; side effects belong in `pipelines/` or the
  cell scripts.

## Open Questions

- ~~**Which client id placed the orders?**~~ **ANSWERED — 151**, the pipeline's client id. Cell 15
  was asking as 161, the debug harness's own. The orders went out through
  `pipelines/rebalance_live.py`.

  **This refines the diagnosis in an important way.** The connection was still `CLIENT_ID = 161`
  when the fills came back — only `ExecutionFilter.clientId` was changed, to 151. So the
  *connection* was never the restriction; the **filter alone** was. The Master API client ID is
  therefore NOT required to see another client's executions on this setup, and the earlier
  "connection scoping" theory is wrong. The prior note that `client_id=0` "returned nothing" was
  taken on a Sunday with no fills at all, so it tested nothing.

  Remaining decision for planning: default `client_id=0` (now the likely-sufficient fix), versus
  keeping scoping deliberate and merely making it loud. `0` is convenient and probably correct;
  explicit scoping is safer for auditing "what did *this* harness do". Cheap to settle — one run
  at `client_id=0` against today's five fills either returns them or does not.
- **What does TWS actually put in `Execution.time`?** The +1h / −5h split suggests a zone token is
  present and being sliced off at `ibkr_requests.py:2460` by `.str.split(' ').str[:2]`. Unverified —
  the raw-payload capture is the first task, and its output decides the parse.
- **Does the deferred NaT concern survive?** The old deferred issue said the parse might silently
  coerce to NaT. The evidence says otherwise: the parse *succeeds* on a truncated string, so the
  failure is a dropped zone, not a NaT. That deferred issue should be rewritten, not just closed.
- Should the raw-payload dump be a permanent debug flag or a throwaway probe? Leaning permanent —
  this class of question has now cost two sessions.

## Additional Context

Standing decision reinforced again today: **the running TWS outranks every written IB source.** The
midnight ceiling was documented confidently on 2026-08-02 and was still not the reason cell 15 was
empty. One live run with a changed client id produced the real answer. Probe before concluding.

STATE.md items this plan should update on UNIFY:
- Deferred issue "`ts` parsing assumes space-separated execution times" — rewrite; the assumption is
  correct, the truncation is the defect.
- Deferred issue "Live round trip (Phase 4, step 9) — BLOCKED: TWS will not start" — stale, the
  blocker was resolved 2026-08-02.
- Add: client-id scoping of `reqExecutions` as an accumulated decision once the direction is chosen.

---

*This file is temporary. It informs planning but is not required.*
*Created by /paul:discuss, consumed by /paul:plan.*
