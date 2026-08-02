---
phase: 06-order-verification
plan: 02
subsystem: ingestion
tags: [smart-routing, primary-exchange, verdicts, priips, order-status]
requires:
  - phase: 06-order-verification
    provides: acknowledgement waiting and message capture
provides:
  - SMART routing via primaryExchange
  - per-order verdict vocabulary (REJECTED / PENDING_OPEN / NO_ANSWER)
  - untradeable-universe handling
affects: [07-live-verification-runbook]
tech-stack:
  added: []
  patterns: ["classification believes the status over the code"]
key-files:
  modified: [src/portutils/ingestion/ibkr_requests.py, src/pipelines/rebalance_live.py, EXECUTION_STACK.md, orders/live_trading_notes.md]
key-decisions:
  - "Listing venue goes in primaryExchange, never exchange"
  - "SPY and KMLM leave the live book — PRIIPs/KID client-eligibility rejection"
  - "Advisory messages are recorded, not discarded"
  - "An untransmitted order is not an order"
patterns-established:
  - "The code set is a fast path; the status check is the guarantee"
duration: unrecorded
started: 2026-07-26
completed: 2026-07-26
description: "SMART routing, per-order verdicts, and an explicit untradeable universe"
type: Summary
about: "Portfolio-Manager"
---

# Phase 6 Plan 02: SMART Routing and Tradeable Universe Summary

**All seven orders reached TWS; the three distinct outcomes that came back are now each named and
reported correctly, and the routing bug that held every foreign leg was fixed.**

> Migrated from [`.claude/plans/smart-routing-and-tradeable-universe.md`](../../../.claude/plans/smart-routing-and-tradeable-universe.md)
> (archive). Reconstructed at PAUL adoption.

## Accomplishments

- Every foreign leg stopped requiring a manual confirmation hold — the listing venue was being put in
  `exchange`, which requests direct routing, instead of `primaryExchange`
- A verdict vocabulary that distinguishes rejection, pending-open and no-answer rather than
  collapsing them
- Five orders that looked lost turned out to be held with advisory messages that were being discarded

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/ingestion/ibkr_requests.py` | Modified | Listing venue to primaryExchange; status believed over code |
| `src/pipelines/rebalance_live.py` | Modified | Per-order verdicts and full advisory-message capture |
| `EXECUTION_STACK.md` | Modified | Documents the verdict vocabulary |
| `orders/live_trading_notes.md` | Modified | Records what the live run taught |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Listing venue in `primaryExchange` | Direct routing was never intended — an accident of copying the account snapshot's field across | Foreign legs route SMART |
| SPY and KMLM leave the live book | Client-eligibility rule (PRIIPs/KID); retrying in any form is pointless | UCITS equivalents (e.g. CSPX) are a user decision, not an automatic swap |
| Advisory messages recorded, not discarded | "Not terminal" and "not worth keeping" are different claims | Held orders stopped looking lost |
| An untransmitted order is not an order | Exists only in the TWS client, invisible to `reqAllOpenOrders`, uncancellable via the API | Reporting can state this rather than implying the API's silence means the order is gone |
| Classification believes the status over the code | A code list is a maintenance burden that fails silently as IB adds warnings; `PreSubmitted` is unambiguous | Code set kept as a fast path, status check as the guarantee |
| Cancellation behind `--cancel` in its own pipeline | The recovery tool must not be reachable by accident from the tool being recovered from | |

## Deferred Items

- **Five stale untransmitted rows in the TWS Pending panel.** They cannot fill on their own, but
  clicking Transmit later would duplicate the live orders. Clear them via the ✕ in the Cancel column.

## Next Phase Readiness

**Ready:** Order reporting is trustworthy, which is the precondition for Phase 7's live legs.
**Concerns:** The stale pending rows are a live-account hazard until cleared.
**Blockers:** None.

---
*Phase: 06-order-verification, Plan: 02*
*Completed: 2026-07-26*
