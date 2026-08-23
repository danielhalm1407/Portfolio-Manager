---
phase: 04-ibkr-read-path
plan: 04
subsystem: ibkr
tags: [ibkr, ibapi, tws, executions, reqExecutions, ExecutionFilter, timezone, flex]

requires:
  - phase: 04-ibkr-read-path
    provides: get_executions_data and its execDetails / commissionReport / execDetailsEnd callbacks
provides:
  - ExecutionFilter.time in non-deprecated UTC dash notation
  - An accurate account of the executions window (midnight-today ceiling)
  - tests/test_ibkr_executions.py — the outbound half of the request, pinned
  - orders/run_cells_for_execution_rec.py — terminal cell runner with an order-sending guard
affects: [07-live-verification-runbook, 01-pnl-accounting]

tech-stack:
  added: []
  patterns:
    - "Probe the running TWS before citing any IB document — three written sources gave three different incomplete answers"
    - "Stub the app, not the network, extended to the OUTBOUND request: capture the filter before it is sent"

key-files:
  created:
    - tests/test_ibkr_executions.py
    - orders/run_cells_for_execution_rec.py
  modified:
    - src/portutils/ingestion/ibkr_requests.py

key-decisions:
  - "ExecutionFilter.time uses UTC dash notation yyyymmdd-HH:MM:SS with no suffix"
  - "The running TWS outranks every written IB source"
  - "0 fills is not a code defect — the API serves executions since midnight today"
  - "Genuine execution history must come from Flex Web Service, not reqExecutions"

patterns-established:
  - "Diagnose IB behaviour from a terminal, where reader-thread callbacks and IB Error lines are visible"

duration: ~150min
started: 2026-08-02T00:00:00Z
completed: 2026-08-02T02:30:00Z
description: "Executions filter moved to non-deprecated UTC dash notation; the empty-result mystery resolved as IB's midnight ceiling, not a bug"
type: Summary
about: "Portfolio-Manager"
---

# Phase 4 Plan 04: Executions window and filter format — Summary

**`get_executions_data` now sends the non-deprecated UTC dash time format, and the `0 fills`
report it produces is explained and documented rather than fixed — IB serves executions only since
midnight today, so there was never a defect to fix.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~150 min (including three live TWS probe runs) |
| Started | 2026-08-02T00:00:00Z |
| Completed | 2026-08-02T02:30:00Z |
| Tasks | 2 completed |
| Files modified | 3 (1 modified, 2 created) |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: Filter uses UTC dash notation | Pass | `test_execution_filter_time_uses_utc_dash_notation_with_no_suffix`; conversion is real (`astimezone`), pinned by a second test so relabelling local midnight as UTC would fail |
| AC-2: `days_back` is a floor, not a reach | Pass | `test_days_back_only_lowers_the_floor_it_does_not_widen_the_window` |
| AC-3: Live TWS accepts it, no deprecation warning | Pass | Live call against TWS: `2174 present: False`, `10314 present: False`, `'without explicit time zone' present: False` |
| AC-4: Docstring states the real ceiling | Pass | ⚠ block rewritten to midnight-today, Trade Log GUI setting, IB Gateway impossibility |
| AC-5: Terminal runner with a sending guard | Pass | `--list` labels every cell and flags 11/11c; requesting cell 11 refuses with exit 3 |

## Accomplishments

- **Resolved the actual question.** An empty `ExecutionFilter()` — no time, no clientId, no
  account — returns 0 fills with `execDetailsEnd` received, on a healthy connection, while the
  TWS Trade Log shows JUL 27–31. No filter setting produces rows. The clientId hypothesis is dead
  (`clientId=161` also returns 0), and so is the format hypothesis.
- **Got the time format right from the only authority that counts.** TWS's own error 10314 states
  the spec; error 2174 shows the previous form was deprecated. The code now sends UTC dash
  notation and draws neither.
- **Documented a real constraint on the accounting engine**, not just a cell-15 annoyance: genuine
  execution history needs Flex Web Service.
- **Built the tool that made the diagnosis possible** and kept it — the errors that settled the
  format question are invisible in a notebook.

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/ingestion/ibkr_requests.py` | Modified | `exec_filter.time` → `astimezone(timezone.utc).strftime('%Y%m%d-%H:%M:%S')`; `timezone` imported; ⚠ ceiling block and `days_back` doc rewritten; comment records the 10314/2174 evidence |
| `tests/test_ibkr_executions.py` | Created | 4 tests over the OUTBOUND request — `StubExecApp` captures the ExecutionFilter before it is sent |
| `orders/run_cells_for_execution_rec.py` | Created | Runs selected `# %%` cells in a terminal, default 1 2 3 15, refusing the order-sending cells |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| `ExecutionFilter.time` = UTC dash notation, no suffix | Error 10314 gives the spec: space form needs an explicit timezone, dash form IS UTC and rejects a suffix. Old space-without-timezone form draws 2174 and is being removed next API release | Warning-free and future-proof; pinned by test |
| The running TWS outranks every written IB source | The field reference, ib_insync and the ibapi method docstring each gave a different incomplete answer. One probe produced the real spec from TWS's error text | Probe before citing, on any IB question |
| `0 fills` is not a code defect | Proven by unfiltered request returning 0 with the end marker received | Stops future effort being spent "fixing" this function |
| Execution history comes from Flex Web Service | The current doc set states a flat current-day limit; the 7-day Trade Log workaround exists only in the legacy doc set | Real constraint on the P&L/accounting path |

## Deviations from Plan

### Summary

| Type | Count | Impact |
|------|-------|--------|
| Plan revisions mid-APPLY | 3 | Substantial — the plan's premise was wrong twice |
| Scope additions | 1 | The cell runner, built before it had a plan entry |
| Deferred | 2 | Logged to STATE.md |

**Total impact:** The code delivered is smaller than v1 of the plan promised (no behaviour change),
and the understanding delivered is larger. Both corrections came from the user, not from
self-review.

### Plan revisions

**1. v1 claimed a space-vs-dash format bug caused the empty result.** Wrong on both counts. Based
on one ibapi method docstring, without checking the field-level reference. Changed the format to a
dash and asserted it was the fix.

**2. v2 reverted that after the user read IB's documentation directly.** The ExecutionFilter field
reference and ib_insync both use the space form, so the revert was right on the evidence then
available — but v2 also preserved v1's unexamined premise that "0 fills means the request
completed", which the first probe disproved (on a session with broken server connectivity,
`execDetailsEnd` never arrives at all).

**3. v3/v4 grounded everything in live probes.** Both earlier versions were arguing from the wrong
class of evidence — documentation — when the only authority was the running application.

### Scope addition

**`orders/run_cells_for_execution_rec.py` was written and used before it had a plan entry.** PAUL
requires a plan before implementation; this was built mid-investigation as a diagnostic, then kept
at the user's instruction and folded into 04-04 retrospectively. Recorded as a deviation rather
than backdated to look planned.

### Deferred Items

- **`ts` parsing assumes space-separated execution times** — `ibkr_requests.py` parses inbound
  `Execution.time` with `format='%Y%m%d %H:%M:%S'` and `errors='coerce'`. Given TWS's stated
  preference for dash/UTC notation on the OUTBOUND side, the inbound stamps deserve the same
  scrutiny: a mismatch turns every `ts` into NaT silently and then sorts on an all-NaT column.
  Out of scope here, which touched only the outbound filter.
- **The Trade Log 7-day extension was never proven to apply.** The setting was changed on the
  **Trades** panel; the archived guidance describes `Account → Trade Log` with "all days checked",
  a different window. Untested.

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| First probe: every variant timed out, no `execDetailsEnd` | TWS had lost its server connection (2110, plus 2103/2105/2157 farm breaks). Run discarded as invalid evidence, not read as a result |
| Second probe: connection refused on client ids 171–173 | TWS was mid-reconnect. Retried once connectivity was confirmed via `netstat` (ESTABLISHED to 64.190.197.40:4001) |
| Probe variant F rejected with error 10314 | `" UTC"` appended to dash notation. The rejection was itself the most useful output of the run — it printed the full format spec |
| Sunday | `market_hours_note()` flagged the weekend. Latest fills JUL 31, probe on AUG 2 — a working 7-day extension and a dead one give identical results today |

## Next Phase Readiness

**Ready:**
- TWS is running and reachable; read-only API probes round-trip. **Phase 7's TWS-availability
  blocker is resolved**, which had stood since 2026-08-01.
- `orders/run_cells_for_execution_rec.py` gives Phase 7 a way to run staged cells from a terminal
  with the order-sending cells guarded.

**Concerns:**
- `ARM_LIVE = True` is still committed in `orders/rebalance_live_debug.py`, so
  `test_debug_cell_script_is_disarmed_and_gated` still fails by design (suite: 129 passed, 1
  failed). Set it back to `False` when live work finishes.
- If the current-day limit is real, the accounting engine's execution history depends on Flex Web
  Service, which is not built.

**Blockers:**
- None for Phase 7. The executions-history question is open but does not block the runbook.

**The decisive test remains untaken:** on the next trading day, once anything fills, cells 1 2 3 15
should show it within the same session. If they do, `get_executions_data` is sound and only history
is limited.

---
*Built with PAUL Framework v1.4 · https://chrisai.cv/skool · https://youtube.com/@chris-ai-systems*
*Phase: 04-ibkr-read-path, Plan: 04*
*Completed: 2026-08-02*
