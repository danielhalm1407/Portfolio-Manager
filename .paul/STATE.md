---
description: "Portfolio-Manager — current position and accumulated context"
type: ProjectState
about: "Portfolio-Manager"
---

# Project State

## Project Reference

See: .paul/PROJECT.md (updated 2026-08-01)

**Core value:** A market narrative becomes a defensible target weight, and that target weight becomes
a real position — with the accounting, attribution and order verification needed to trust each step.
**Current focus:** Phase 7 — the TWS-dependent legs of the staged live verification runbook.

## Current Position

Milestone: v0.2 Live execution against IBKR
Phase: 4 — IBKR read path — Complete (reopened and reclosed 2026-08-02)
Plan: 04-04 complete. 07-02 still open; its TWS blocker is now lifted.
Status: Loop closed, ready for next PLAN
Last activity: 2026-08-02 — Closed 04-04: UTC dash filter format, executions-ceiling docs, 4 tests, terminal cell runner

Progress:
- Milestone v0.2: [████████░░] 80%  (4 of 5 phases complete)
- Phase 4: [██████████] 100% (4 of 4 plans complete)

## Loop Position

Current loop state:
```
PLAN ──▶ APPLY ──▶ UNIFY
  ✓        ✓        ✓     [Loop complete — ready for next PLAN]
```

Phases 1-6 and 8 were completed **before** PAUL was adopted. Their SUMMARY files are
reconstructions written at migration time from the original plans, the git history and the current
codebase — not the output of a PAUL APPLY/UNIFY cycle. Treat them as an accurate record of what
shipped, not as evidence the loop was followed.

## Accumulated Context

### Decisions

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-26 | Listing venue goes in `primaryExchange`, never `exchange` | Direct routing was an accident of copying the account snapshot; it cost a manual-confirmation hold on every foreign leg |
| 2026-07-26 | SPY and KMLM leave the live book | Client-eligibility rejection (PRIIPs/KID); retrying in any form is pointless. UCITS equivalents are a user decision |
| 2026-07-26 | Advisory messages are recorded, not discarded | "Not terminal" and "not worth keeping" are different claims |
| 2026-07-26 | An untransmitted order is not an order | Invisible to `reqAllOpenOrders`, uncancellable through the API |
| 2026-07-26 | Order classification believes the status over the code | A code list fails silently as IB adds warnings |
| 2026-07-26 | Cancellation lives in its own pipeline behind `--cancel` | The recovery tool must not be reachable by accident from the tool being recovered from |
| 2026-08-01 | Adopt PAUL; `.claude/plans/` becomes a read-only archive | See CARL decisions tooling-001 … tooling-006 |
| 2026-08-02 | `ExecutionFilter.time` uses UTC dash notation `yyyymmdd-HH:MM:SS`, no suffix | Settled by live TWS, after two wrong answers taken from documentation. Error 10314 gives the spec: space form takes an explicit timezone (`20031126 15:59:00 US/Eastern`), dash form IS UTC and must carry no suffix. The old space-without-timezone form is a deprecated third case that draws error 2174 — "implied time zone functionality will be removed in the next API release" |
| 2026-08-02 | On IB questions, the running TWS outranks every written source | The ExecutionFilter field reference, ib_insync and the ibapi method docstring each gave a different, incomplete answer on the time format. One probe against a live session produced the actual spec from TWS's own error text. Probe before citing |
| 2026-08-02 | `get_executions_data` returning nothing is not a bug to fix in code | Proven, not inferred: an empty `ExecutionFilter()` — no time, no clientId — returns 0 fills with `execDetailsEnd` received, while the TWS Trade Log shows a week of them. IB serves executions since MIDNIGHT TODAY; `ExecutionFilter.time` only narrows an already-capped set, so `days_back` is a floor, not a reach |

### Deferred Issues

- **Live round trip (Phase 4, step 9)** — BLOCKED: TWS will not start on this machine.
- **GUI smoke test of the kts.py migration** — the headless replay matches to 1e-9, but a live Tk
  smoke test and replay scrub were never done by hand.
- **`ts` parsing assumes space-separated execution times** — `ibkr_requests.py` parses inbound
  `Execution.time` with `format='%Y%m%d %H:%M:%S'` and `errors='coerce'`. Given TWS's stated
  preference for dash/UTC notation on the OUTBOUND side, the inbound stamps deserve the same
  scrutiny: a mismatch turns every `ts` into NaT silently and then sorts on an all-NaT column.
  Deliberately out of scope for 04-04, which touched only the outbound filter.
- **Execution history has no source yet.** If IB's current-day limit is real, `reqExecutions`
  cannot supply P&L history and Flex Web Service (query + token in Account Management) is the
  only route. Not built.
- **Stale TWS pending rows** — five untransmitted rows were left in the TWS Pending panel. They
  cannot fill on their own, but clicking Transmit later would duplicate the live orders.

### Blockers/Concerns

- ~~**TWS will not start on the dev machine.**~~ **RESOLVED 2026-08-02** — TWS is running and
  reachable on port 7497, and read-only API probes round-trip. Phase 7's TWS-dependent legs are
  no longer blocked on availability.
- **The Trade Log's "Show trades for: Last 7 Days" setting does not lift the API window.** Set and
  displayed, showing JUL 27–31 fills, yet an unfiltered `reqExecutions` returns 0 with
  `execDetailsEnd` received. Two unresolved possibilities: (a) the setting was changed on the
  **Trades** panel, while the archived guidance describes `Account → Trade Log` with "all days
  checked" — a different window, never tested; (b) the workaround no longer exists, since it
  appears only in the legacy v9.72+ doc set and the current docs state a flat "only the current
  day's executions can be retrieved". TWS-side, not code-side.
- **`ARM_LIVE = True` is committed** in `orders/rebalance_live_debug.py:67`. The live-submit gate is
  open, and `test_debug_cell_script_is_disarmed_and_gated` fails by design as the reminder. Set it
  back to `False` when live work is finished; the suite is green at 126/126 with it disarmed.

## Session Continuity

Last session: 2026-08-02
Stopped at: Plan 04-04 unified; loop closed
Next action: Run /paul:plan 7 — the TWS-availability blocker is lifted, so the staged live
verification runbook (07-02) can now execute. Alternatively /paul:plan 9 for the research half.
Resume file: .paul/phases/04-ibkr-read-path/04-04-SUMMARY.md

---
*STATE.md — Updated after every significant action*
