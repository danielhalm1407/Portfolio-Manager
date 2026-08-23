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
**Current focus:** Phase 4 — reopened 2026-08-03 for plan 04-05 (executions client-id scoping,
`ts` timezone semantics, terminal debug transparency).

## Current Position

Milestone: v0.2 Live execution against IBKR
Phase: 4 — IBKR read path — **REOPENED 2026-08-03** (third reopening)
Plan: 04-05 not yet created; CONTEXT.md written and ready for /paul:plan. 07-02 still open.
Status: Pre-PLAN — /paul:discuss complete
Last activity: 2026-08-22 — Reconciled STATE/ROADMAP (PAUL rule 6): Phase 4 status, Phase 10 row +
detail section, 04-05 listed, milestone progress recounted in whole phases. No code touched.
Prior: 2026-08-06 — Added Phase 10: Deployment — web app (dashboard + gated live rebalancer
control; hosting deliberately undecided). Prior: 2026-08-03 — diagnosed the empty-executions bug as
`ExecutionFilter.clientId`, not the midnight ceiling; found the `ts` timezone defect and the
callback-payload discard

Progress:
- Milestone v0.2: [█████░░░░░] 50%  (3 of 6 phases complete: 5, 6, 8. Phase 4 reopened and Phase 10
  added, so the denominator moved, not the work. Phases 4 and 7 are part-done and counted at zero —
  whole phases only, which is why this reads lower than the earlier 58%)
- Phase 4: [████████░░] 80% (4 of 5 plans complete — 04-05 scoped, not written)
- Phase 7: [█████░░░░░] 50% (1 of 2 plans complete — 07-02 planned, not applied)

## Loop Position

Current loop state:
```
PLAN ──▶ APPLY ──▶ UNIFY
  ○        ○        ○     [Pre-PLAN — discussion done, /paul:plan 4 not yet run]
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
| 2026-08-03 | **The 2026-08-02 row above is only half right — `ExecutionFilter.clientId` WAS a bug in code** | Five fills executed 2026-08-03 and cell 15 still returned nothing, so the midnight ceiling could not be the active cause: those fills were inside the served window. The filter asked as client 161 (the debug harness) while the orders had been placed by 151 (`pipelines/rebalance_live.py`). Setting the filter to 151 returned all five. The ceiling remains real and separately confirmed (every returned row was stamped today despite `days_back=7`) — it was simply not what was biting |
| 2026-08-06 | Added Phase 10: Deployment — web app, appended to v0.2 rather than numbered 9 | 9 was already claimed by the v0.3 research phase. Appending avoids renumbering an existing phase and its directory, at the cost of v0.2 no longer being a contiguous range (4-8, 10). The alternative — a new v0.4 milestone — was raised and the user chose the phase |
| 2026-08-06 | Web app scope is dashboard PLUS gated live-rebalancer control, not read-only | Chosen explicitly over a read-only dashboard and over a static GitHub Pages showcase. Consequence: the dry-run approval gate, written for a terminal, has to be re-derived for a browser — and static hosting is largely ruled out, since it can hold no secrets and cannot reach TWS |
| 2026-08-22 | State reconciled: ROADMAP's "4 of 5 / 80%" was phase-4 plan progress sitting in a milestone field | v0.2 spans SIX phases (4-8, 10). ROADMAP also carried Phase 4 as `Complete 2026-08-02` after its 2026-08-03 reopening, had no Phase 10 row or detail section despite Phase 10 being in the milestone range and having a directory, and omitted 04-05 from Phase 4's plan list. Milestone progress is now counted in whole phases (3 of 6 = 50%), replacing the 3.5/6 half-credit that no PAUL count supports |
| 2026-08-03 | The Master API client ID is NOT required to see another client's executions | The connection was still `CLIENT_ID = 161` when the five fills came back; only `ExecutionFilter.clientId` changed, to 151. So connection-level scoping was never the restriction and the filter alone was. An earlier connection-scoping theory is wrong. The prior observation that `client_id=0` "returned nothing" was taken on a Sunday with no fills, and tested nothing |

### Deferred Issues

- ~~**Live round trip (Phase 4, step 9)** — BLOCKED: TWS will not start on this machine.~~ **STALE**
  — the TWS blocker was resolved 2026-08-02, and orders placed via client 151 filled on 2026-08-03.
- **GUI smoke test of the kts.py migration** — the headless replay matches to 1e-9, but a live Tk
  smoke test and replay scrub were never done by hand.
- **`ts` carries no verified timezone, and the parse silently truncates** — REWRITTEN 2026-08-03,
  replacing the earlier "assumes space-separated execution times / risks NaT" framing. That framing
  was wrong: the parse does not fail. `ibkr_requests.py:2460` does
  `df['time'].str.split(' ').str[:2]`, discarding element `[2]` — where a timezone token would ride
  — and `format='%Y%m%d %H:%M:%S'` then SUCCEEDS on the truncated remainder, so nothing ever errors
  and nothing ever looks wrong. Evidence: returned `ts` disagrees with the TWS Trade Log by +1h on
  four European venues and −5h on NYSE, which no single timezone pair explains. Consequence: the
  sort at `ibkr_requests.py:2462` orders stamps that are not on a common clock. Mechanism still
  UNVERIFIED — dumping the raw `Execution.time` string is task one of 04-05.
- **The callback discards most of the payload** — `ibkr_requests.py:1159-1180` hand-picks 13 fields
  off the `Execution` object; the object is garbage immediately after. `clientId`, `acctNumber`,
  `exchange`, `lastLiquidity` and `orderRef` are all lost in-process, so no downstream flag or
  logging level can recover them. `execution.clientId` in particular would have answered the
  2026-08-03 bug outright. In scope for 04-05.
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

Last session: 2026-08-03
Stopped at: /paul:discuss 4 complete — CONTEXT.md written for plan 04-05. No code written.
Next action: Run **/paul:plan 4** to create 04-05 from
`.paul/phases/04-ibkr-read-path/CONTEXT.md`.
Resume file: .paul/HANDOFF-2026-08-03.md
Git strategy: feat/live-rebalancer-multicurrency (existing branch, no WIP commit taken)
Resume context:
- Phase 4 reopened. The 2026-08-02 "midnight ceiling" explanation was only half the story —
  `ExecutionFilter.clientId` was a genuine code bug. Orders were placed by client 151
  (`pipelines/rebalance_live.py`); cell 15 asked as 161 and saw nothing.
- The connection stayed at 161 throughout, so the Master API client ID is NOT needed. Do not
  carry the connection-scoping theory forward.
- 04-05 has three goals: client-id scoping made explicit, `ts` given verified timezone
  semantics, terminal debug path made transparent. Flex Web Service is parked as its own plan.
- **Task one of the plan must be dumping the raw `Execution.time` payload.** The timezone fix
  cannot be designed before that string has actually been seen.
- Uncommitted by hand: `orders/rebalance_live_debug.py` cell 15, `client_id` 161 → 151. That is
  the evidence for the diagnosis — keep it. It is a diagnostic value, not the final fix.

---
*STATE.md — Updated after every significant action*
