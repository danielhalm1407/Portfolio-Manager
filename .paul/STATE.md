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
**Current focus:** Phase 11 — long-history data foundation, the first phase of milestone v0.4
(*does hedging actually work?*), which is the substantive question the project exists to answer.
Also open: Phase 2 — reopened 2026-08-23 for 02-02/02-03 (trade rationale through the
rules into the order ledger). Phase 4 — reopened 2026-08-03 for plan 04-05 (executions client-id scoping,
`ts` timezone semantics, terminal debug transparency).

## Current Position

Milestone: v0.4 Does hedging actually work? (added 2026-08-24) — v0.2 still in progress alongside
Phase: 11 of 15 (Long-history data foundation) — Planning
Plan: 11-01 created 2026-08-24, awaiting approval — now also carries the ragged-history fix at BOTH
sites (`cache_prices.py::_tidy` and `PanelBuilder._load`), and has a phase-level `CONTEXT.md`
written above it. Also open: 02-02 and 02-03 (created 2026-08-23, awaiting approval), 04-05 (CONTEXT.md written,
not yet planned), 07-02 (planned, not applied), and three scoped-but-unwritten plans: 10-01, 10-02,
11-02.
Status: PLAN created and amended, awaiting approval
Last activity: 2026-08-31 (latest) — the option-overlay probe. FIRST CODE of the strategy
package: `src/portutils/strategies/` with `instruments/vol.py` (`synthetic_iv_surface`, ported from
`crash.py:2579`) and `instruments/pricing.py` (`black_scholes_put` — European, discounted-intrinsic
floor, no clamp, no scipy), plus `research/option_overlay_probe.py` + `.md`, a `# %%` strike-ladder
pass on the existing SPY panel. Written AHEAD of 16-01, out of PAUL sequence and deliberately: the
surface existed only as an inline code block inside `12-01-PLAN.md`, so every consumer would have
retyped it. All four of 12-01's quoted anchor numbers reproduce exactly (IV 0.2098 / 0.1760, put
3.612 / 3.206). First hard cost figure for v0.4: an ATM three-month put is 3.53% of spot, ≈14% of
notional a year rolled quarterly, which makes an ATM hedge implausible on its face; 0.90x is 0.85%
(≈3.4%/yr). Documented in phase 16's CONTEXT ("Landed ahead of the plans") and the ROADMAP.
Prior: 2026-08-31 (later) — `/paul:discuss phase 11-01`. Wrote
`.paul/phases/11-long-history-data/CONTEXT.md` (data inventory, the max-history request recipe, the
new finding) and `src/portutils/ingestion/ibkr_requests.md` (historical + market data reference,
line-linked; README.md now points at it). Measured starting position: NO SPX series exists — S&P
exposure is SPY only, earliest bar anywhere 2025-04-14 — and NO NDX/QQQ series at all.
Found a SECOND ragged-truncation site in `cache_prices.py::_tidy`, which runs BEFORE
`PanelBuilder`; folded into 11-01 Task 4 as Step 0 with AC-6 extended. No code touched.
Prior: 2026-08-31 — Roadmap amended, no code touched. Phase 10 split into Track A (10-01,
static showcase) and Track B (10-02, gated live control). Phase 11 gained 11-02 (IBKR message
reference) and a blocker: `PanelBuilder._load` truncates ragged history. 12-01's vol surface staged
into v1 (frozen, no data dependency) and v2 (both dynamic terms together).
Prior: 2026-08-24 — Added milestone v0.4 (phases 11-15) and created 11-01-PLAN.md.
Survey found the binding constraint: every cached panel is 250 rows over one regime, and the
codebase has no option support at all.
Prior: 2026-08-23 — Phase 2 reopened; 02-02 (rationale at the rule, into Order, out as
JSON) and 02-03 (hoverable trade plot + call-chain docs) written from a drafted plan.
Prior: 2026-08-22 — Reconciled STATE/ROADMAP (PAUL rule 6): Phase 4 status, Phase 10 row +
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
- Phase 2: [███░░░░░░░] 33% (1 of 3 plans complete — 02-02 and 02-03 planned, not applied)
- Milestone v0.4: [░░░░░░░░░░] 0% (0 of 5 phases — 11-01 planned, not applied)

## Loop Position

Current loop state:
```
PLAN ──▶ APPLY ──▶ UNIFY
  ✓        ○        ○     [11-01 created, awaiting approval]
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
| 2026-08-06 | Web app scope is dashboard PLUS gated live-rebalancer control, not read-only | Chosen explicitly over a read-only dashboard and over a static GitHub Pages showcase. Consequence: the dry-run approval gate, written for a terminal, has to be re-derived for a browser — and static hosting is largely ruled out, since it can hold no secrets and cannot reach TWS *comment: my understanding was that in the fully fledged, ideal version indeed yes, but just to get something out there and showcased, that typically people indeed do this through a github pages that can still have static pre-calculated plotly figures that appear interactive, and that this would be implemented through a plotly.js compiled once if i recall correctly? and no need for flask, but a flask framework process delivered through the dash framework wrapped on top of that can also be used to do this? Like my undertsanding is dash is plotly's way of allowing figures from its python library to be diplayed and reloaded on a persistent process, but surely they have a way to do this for a static process, and/or dash can be used for a static github pages with plotly figures? |
| 2026-08-24 | v0.4 is a new milestone of five phases, not an extension of Phase 2 | The claim under test — hedge structures improve risk-adjusted returns across regimes, and in-sample optima survive out-of-sample — needs long-history data, an option overlay engine, a walk-forward harness and regime labelling. Four subsystems is a milestone. Phase 2 remains what it is: the one-year, one-regime demonstration of the mechanic |
| 2026-08-24 | Phase 11 is the data foundation, and it comes first | Every cached panel is 250 rows spanning 2025-07-28 to 2026-07-24. On one regime the v0.4 claim is not weakly supported, it is untestable. Phases 12-15 are all blocked behind it |
| 2026-08-24 | The option overlay is a NEW subsystem, not an extension of the weight-based rules | `strike`, `secType="OPT"`, `right` and `expiry` appear nowhere in `src/portutils/`. The rolling collar of the Obsidian hedge catalogue needs strike selection, pricing, theta and a roll calendar — none of which the `propose() -> {symbol: units}` contract currently expresses |
| 2026-08-24 | Probe the data ceiling before designing pagination | Phase 4 spent two plans on an executions limit taken from documentation that proved wrong; the standing decision is that the running TWS outranks written sources. Task 1 of 11-01 is a probe, and the source-split decision is a blocking checkpoint on its findings |
| 2026-08-31 | Phase 10 splits into Track A (10-01, static showcase) and Track B (10-02, gated live control) | The two halves have opposite hosting constraints and opposite dependencies. Static hosting is ruled out only for Track B — Dash has no static export (Flask; callbacks POST to `/_dash-update-component`), but `fig.write_html(include_plotlyjs="directory")` publishes fully interactive Plotly with no runtime, emitting `plotly.min.js` once. `_build_level_figure` (`dash_timeseries_app.py:637`) already returns a plain figure that `LevelDashApp` (`:1041`) only wraps, so Track A needs no rewrite of the figure layer. Bundling had gated a zero-risk showcase behind the Phase 7 live-trading runbook, which Track A does not depend on. Adds a plan, not a phase — the v0.2 denominator is unchanged |
| 2026-08-31 | `PanelBuilder` ragged-history loading is fixed inside 11-01, not deferred to a viz phase | `_load` (`viz/panel.py:165-167`) does outer join → `ffill` → `dropna`, truncating the whole panel to the latest first-bar across tickers. A 1990 VIX series beside a 2006 KMLM series silently loses 1990-2006 for every symbol, with no warning — the only `print` fires on a load exception. That would let `COVERAGE.md` claim 1990 while every plot starts in 2006, defeating 11-01's own AC-3 |
| 2026-08-31 | The ragged-truncation defect has TWO sites, and `cache_prices.py` is the one that runs first | `_tidy` (`cache_prices.py:45`) ends at `:55` with `out.astype(float).ffill().dropna(how="any")` — identical to `PanelBuilder._load`, but one layer earlier, in the code that WRITES the parquet. SPY-from-1993 cached beside QQQ-from-1999 truncates to 1999 in the file itself, before `PanelBuilder` exists and before `COVERAGE.md` is generated, so fixing only the viz loader bakes the loss into the very file the coverage report describes — AC-3 and AC-6 fail together. The opt-in default does NOT apply here: `cache_prices.py` writes new files under new tags, so a long-history tag has no existing reader to regress, and the three existing parquets are on the DO-NOT-CHANGE list anyway. Folded into 11-01 Task 4 as Step 0, with AC-6 extended |
| 2026-08-31 | Long-history index legs are SPY/QQQ proxies unless a probe justifies `secType='IND'` | `contract()` defaults to STK/SMART/USD, which SPY and QQQ match exactly and the indices do not. SPX/NDX need `{'sec_type': 'IND', 'exchange': 'CBOE'/'NASDAQ'}`, sit behind separate market-data entitlements, are not tradeable, and `what_to_show='TRADES'` is the wrong basis for a quoted-not-traded instrument. ETF inception (SPY 1993, QQQ 1999) still clears 11-01's stated bar of 2008/2020/2022; only 2000 and earlier need the index or a free source. The choice joins the existing blocking checkpoint rather than being settled here |
| 2026-08-31 | Current `ffill` + `dropna` stays the DEFAULT **in `panel.py`**; ragged loading is opt-in, for prices and returns alike | Keeping the default preserves every existing caller and figure unchanged, so the fix cannot regress current output. The opt-in mode must cover the derived-frame wrappers (`panel.py:187-211`) too: a rebase or first-difference on a ragged frame anchors to each series' own first observation, not the panel's |
| 2026-08-31 | IBKR message reference becomes 11-02, keyed by code and carrying BOTH taxonomies | No such document exists in the repo. The code already classifies on an advisory-versus-terminal axis (`_ADVISORY_ORDER_CODES = {399}` at `ibkr_requests.py:445`, noise filter `(2104, 2106, 2158, 2176)` at `:820`, `errorCode >= 2100` at `:859`, the `req_messages`/`req_notices`/`req_errors` split). The requested grouping — permissions incl. geography, IBKR/subscription limits, instrument-level limits, orders versus data — is a cause axis. Orthogonal, both useful, so neither replaces the other. Separable from 11-01, hence its own plan |
| 2026-08-31 | 12-01 vol surface ships v1 (frozen `spot_ref`, `base = 0.16`) before v2 (time-varying `base` AND current-spot `spot_ref`, together) | The 2026-08-26 amendment mandating current-spot moneyness justified it as fixing an understated crash payoff and does the reverse: skew is a function of `log(K/S)`, so a falling spot slides a fixed strike DOWN the smirk. Measured on the ported function (`K=90`, `t=0.125`, `r=0.02`): at `S=88`, current-spot gives iv 0.176 / price 3.206 against 0.210 / 3.612 frozen. v1 also carries no Phase 11 dependency, so 16-02/16-03 are unblocked, and its bias is conservative for the v0.4 claim |
| 2026-08-31 | 11-01 is a v2-only data dependency for Phase 12, not a build gate | v1 prices with `base = 0.16` and no vol history. 11-01 gates the MAGNITUDE of the hedge payoff, and therefore Phase 13's theta-drag-versus-benefit verdict, not the existence of the pricing code |
| 2026-08-31 | The vol surface and the European put land in `strategies/instruments/` BEFORE 16-01, out of PAUL sequence | The surface lived only as an inline code block inside `12-01-PLAN.md`. Every consumer would have retyped it, and a retyped model is a different model wearing the same numbers. Both modules are new, pure and imported by nothing but a research script, so Track A's zero-regression property is untouched — there is no fixture to break, no live path to disturb, no caller to update. The alternative put three plan cycles between "do these economics work?" and any answer |
| 2026-08-31 | The surface goes in `instruments/vol.py`, NOT in 16-02's `options.py` | 12-01 is explicit that "the surface itself is a pipeline concern, passed in; the leg never fetches it" — a rule calls a `vol_fn(strike, tau) -> float` supplied by the pipeline. The surface is a SIBLING of the pricer, not a member of the leg. It also leaves `options.py` unwritten, so 16-02 writes `SyntheticContract` and `OptionLeg` fresh instead of editing around existing code |
| 2026-08-31 | The 2026-08-29 v1/v2 decision is CONFIRMED, and the probe's smaller gap is not evidence against it | Measured at the trough, the current-spot reference is 1-6% cheaper, not the 11% the plan quotes. The sign is right and the magnitude is a property of the probe: it strikes its ladder once at inception and never rolls, so by the trough `K/S` was 0.81-1.01 — spot had round-tripped almost back to `spot_ref`. The gap scales with distance travelled since the strike was set, and a rolling ladder (16-02's `RollCalendar`) would restrike and see the full effect |
| 2026-08-23 | Trade rationale originates at the RULE, not derived post-hoc from the blotter | A post-hoc deriver would be guessing. `ConstantMixRule.propose` already computes equity, drift (`actual_w`, discarded outside the tolerance branch) and holds the `book`, so it can state at proposal time whether a sale crystallises a gain or a loss. A leg sold because it FELL LESS than the book can book a realised loss — only the rule knows that |
| 2026-08-23 | `propose()`'s signature and return type stay unchanged; rationale rides a same-bar `last_rationale` attribute | Changing the return type would touch kts.py and both parity suites for zero accounting benefit |
| 2026-08-23 | The JSON order ledger is reused, not reinvented | `Order.to_dict()` and the `orders/dummy_orders.json` schema already exist and are complete; the offline path simply never wired them up. The wide DataFrame became the de-facto record by omission, not by decision |
| 2026-08-23 | Split into two plans rather than one | The drafted work is four workstreams; the PLAN template caps a plan at 2-3 tasks. 02-02 is the engine, 02-03 the rendering and docs, with `depends_on: ["02-02"]` |
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

Last session: 2026-08-24
Stopped at: Plan 11-01 created (milestone v0.4 opened).
Next action: Review and approve, then run
**/paul:apply .paul/phases/11-long-history-data/11-01-PLAN.md**
Resume file: .paul/phases/11-long-history-data/11-01-PLAN.md

**v0.4 context (2026-08-24).** The milestone asks whether portfolio insurance actually pays and how
much of it a retail investor needs. The hedge structures are catalogued in the Obsidian vault at
`../obsidian_notes/Knowledge/Finance/Hedging/` — the rolling collar is the one structure documented
as actually rolling (63-day reset), and `How to roll a put hedge` holds the roll mechanics. 11-01 is
autonomous: false because the source-split decision is a blocking checkpoint on probe evidence.

**Older Phase 4 context (2026-08-03), still open:**
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
