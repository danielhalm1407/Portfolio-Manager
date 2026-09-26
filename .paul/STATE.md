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
**Current focus:** Phase 13 — the parameter phase of milestone v0.4 (*does hedging actually work?*).
Phase 11's data foundation is in place (11-01 complete 2026-09-20; 11-02 still scoped, not written),
so the long price and implied-vol history the milestone rests on now exists.
Also open: Phase 2 — reopened 2026-08-23 for 02-02/02-03 (trade rationale through the
rules into the order ledger). Phase 4 — reopened 2026-08-03 for plan 04-05 (executions client-id scoping,
`ts` timezone semantics, terminal debug transparency).

## Current Position

Milestone: v0.4 Does hedging actually work? (added 2026-08-24) — v0.2 still in progress alongside
Phase: 13 (Walk-forward validation harness) — 13-01 COMPLETE 2026-09-26, loop CLOSED
(SUMMARY: `13-01-SUMMARY.md`). 13-01.1 complete (2026-09-20). 13-02 planned, not yet applied, so
Phase 13 is 2 of 3+ and NOT transitioned. Phase 11's 11-01, Phase 10's 10-01 and Phase 16's
16-02/16-03 also complete.
Plan: 16-02 and 16-03 created 2026-09-19, awaiting approval (16-03 depends on 16-02). Option
instruments, then the three option rules run through PortfolioSimulator via an additive
synthetic-marks hook, shown over the probe's SPY window. Needs no Phase 11 data (v1 surface).
Plan: 11-01 created 2026-08-24, awaiting approval — now also carries the ragged-history fix at BOTH
sites (`cache_prices.py::_tidy` and `PanelBuilder._load`), and has a phase-level `CONTEXT.md`
written above it. Also open: 02-02 and 02-03 (created 2026-08-23, awaiting approval), 04-05 (CONTEXT.md written,
not yet planned), 07-02 (planned, not applied), and three scoped-but-unwritten plans: 10-02, 11-02,
plus 13-02/13-03 planned but not applied.
Status: **13-01 loop CLOSED 2026-09-26 (UNIFY run).** Full suite **227 passed, 1 pre-existing
unrelated failure** (`test_debug_cell_script_is_disarmed_and_gated` — an armed `orders/` debug cell,
not touched by this plan). The GFC monetisation policy is published as a two-section site
(`docs/index.html` hub → `docs/probing/` + `docs/validation/`), and `option_monetisation_batch.py`
— which had SILENTLY NEVER COMPLETED A RUN against the current engine, missing
`ledger=NarrowLedger()` — now runs the full 5-config named set in seconds, with AC-3b's trigger
comparison (70.6% agreement within 5 bars) landing as a side effect of the fix. A second stale
constant (`GITHUB_BRANCH`, still the feature branch after this session pushed straight to
`master`) fixed the same pass. 13-02's plan updated with the corroborated runtime constant, an
explicit `monetise_multiple`-out-of-scope note, and a confirmed incremental build order.
Last activity: 2026-09-26 — UNIFY on 13-01.
Prior, 2026-09-20 — UNIFY on 13-01.1. `NarrowLedger` (a constant-width `StateLedger`
SUBCLASS, handed in through the `ledger=` argument `PortfolioSimulator` already accepts, so nothing
on 13-01's DO NOT CHANGE list moved) takes one full-history run from ~6.5 min to **6.30 s / 5,201
bars / 1.21 ms/bar**, with equity **bit-identical** to the wide ledger (max gap 0.0, not merely
within 1e-12). Column count constant at 15 where `StateLedger` reproduces 13-01's measured
96 / 168 / 312 growth. Also shipped `classify_option_events` (in the new
`portutils/strategies/scoring.py`) and `research/validation/option_ladder_probe.py` — one
configuration drawn as equity-and-spot with every fill marked OPEN / ROLL / MONETISE and hoverable
with that bar's state / drawdown / multiple / iv. **On its first real run the probe found that
13-01's re-entry gate thrashes**: 281 monetisations against 48 rolls over twenty years, because
`_gate_open()` tests implied vol alone and never tests whether the monetise trigger is still firing.
Four findings routed into `13-01-PLAN.md`; none fixed here, all on this plan's DO NOT CHANGE list.
Prior, same day: UNIFY on 11-01: ROADMAP marked 11-01 complete, phase 11 set to
"In progress (1 of 2)", STATE reconciled, paul.toml and ledger synced. Phase transition NOT
run, deliberately — 11-02 is scoped and unwritten, so the plan/summary file counts (1 and 1)
would have triggered a false phase completion. Same session, outside the loop: wrote
`.paul/phases/13-walk-forward-validation/CONTEXT.md` recording Phase 13's parameter-search and
validation design (metric, grid, CPCV at N=6/k=2, surrogate staging), with the methodology note
it cites in the `obsidian_notes` vault at
`Knowledge/Finance/Quant finance/Out-of-sample validation of strategy parameters.md`
Prior: 2026-09-19 — created 16-02-PLAN.md and 16-03-PLAN.md. Same day, outside PAUL:
fixed `option_probe_figures.py` (trough `add_vline` Timestamp TypeError; inline figures now
get theme INK/GRID via `_inline_theme`, previously Plotly's default dark-blue text), and
deleted the untracked `research/option_overlay_probe_dan_qs.*` byte-identical copies.
Prior: 2026-08-31 (second pass) — the probe CORRECTED and its figures published.
Cell 8 added: a ladder struck at the drawdown's PEAK rather than the window start, carrying one
option through its full 63-day life. Result: **0.90x +107%, 0.95x +161%, 1.00x +156%** on the
window's −9.1% dip, so the monetisation mechanic is demonstrated, not merely plausible. The 0.80x
strike LOSES 46.9% on the same fall under a frozen vol level and returns +317% with an
illustrative vol-level response — deep-OTM protection is almost entirely vega, which `base = 0.16`
cannot price. Figure builders extracted to `src/pipelines/option_probe_figures.py`, shared between
the research cells and a `main()` that writes four interactive HTML figures plus a generated
`index.html` to `docs/` — the first working instance of Phase 10 Track A's static-export pattern.
Prior, same day: the option-overlay probe. FIRST CODE of the strategy
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
- Milestone v0.4: [░░░░░░░░░░] 0% (0 of 5 phases complete — Phase 11 is 1 of 2 plans done, so it
  counts at zero under the whole-phases rule. 11-02 scoped, not written)
- Phase 11: [█████░░░░░] 50% (1 of 2 plans complete — 11-02 scoped, not written)

## Loop Position

Current loop state:
```
PLAN ──▶ APPLY ──▶ UNIFY
  ✓        ✓        ✓     [13-01 loop CLOSED 2026-09-26 — SUMMARY: 13-01-SUMMARY.md. 13-01.1
                            loop CLOSED 2026-09-20. Phase 13 NOT transitioned: 13-02 planned
                            but not applied (unblocked now that 13-01's engine and full-history
                            batch are proven — no thrash finding blocking it any longer).
                            11-01, 10-01, 16-02, 16-03 loops CLOSED.]
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
| 2026-08-31 | The probe's first-pass claim that "the window cannot answer the payoff half" is WITHDRAWN | The −9.1% drawdown ran 695.49 → 631.97, but the ladder was struck at the WINDOW START (636.94) after a +9.2% rally, so the fall happened entirely ABOVE every strike and the ladder finished within 1% of where it began. That is a defect in the probe, not a property of the data: it measured carry and would have been read as payoff. Restruck at the peak — where a rolled hedge stands — the same window gives +107% / +161% / +156% at 0.90x / 0.95x / 1.00x. What genuinely does not survive is that ONE episode is one observation: 14 bars below −5%, 2 below −8%, none below −20% |
| 2026-08-31 | `RollCalendar` (16-02) is reclassified from convenience to correctness | Restriking is what puts the next drawdown in front of the strike. A harness built on a never-restruck ladder measures carry and reports it as a result — the exact error above — however long the history behind it. This raises 16-02's priority relative to the rest of Track A |
| 2026-08-31 | The case for Phase 11 is now the vol LEVEL, not the window length | Measured: the same put on the same path returns −46.9% with `base` frozen at 0.16 and +317.0% under an illustrative level response (0.16 → 0.34 across the drawdown). Deep-OTM protection is almost entirely vega, so v1 cannot price what the option is bought for, and the understatement is WORST on the cheapest strikes — the ones a retail hedge actually buys. Every payoff figure the probe reports is therefore a floor, not an estimate |
| 2026-08-31 | Published figures go to `docs/`, not `outputs/`, and carry a vendored `plotly.min.js` | `outputs/` is gitignored (`.gitignore:37`) and a published page must be committed to be served. `docs/` is also the one folder GitHub Pages serves off the default branch with no workflow, and `.github/` has none. `include_plotlyjs="directory"` (10-01's stated choice) costs **4.1 MB committed once**, shared by every figure at 24-107 KB each; `"cdn"` is a one-word fallback taking `docs/` to ~270 KB if the vendored copy is judged too heavy |
| 2026-08-31 | Figure builders live in `pipelines/option_probe_figures.py` and are imported by the research script | One implementation serves the inline `# %%` figure and the published page, so they cannot drift. This is the arrangement `viz/theme.py` already documents for `rebalance_study.py` / `rebalance_realisation.py`, and the reason `apply_export_theme` is a post-hoc stamp rather than a `theme=` argument threaded through every signature |
| 2026-09-19 | `PortfolioSimulator` gains an additive synthetic-marks hook (16-03), overriding 12-01's DO-NOT-CHANGE on `simulator.py` | User's choice over a standalone runner. `run()` marks and fills only from panel columns (`marks.get(sym)`, simulator.py:75), so a synthetic option leg could never fill. `RebalanceRule.synthetic_marks` defaults to `{}` and marks merge after propose, before execute; gated on byte-identical `rebalance_study` / `drawdown_rotation_sim` output. Option Fills carry no rationale until 02-02 lands; rules keep an `events` log meanwhile |
| 2026-09-19 | 16-02/16-03 proceed WITHOUT 16-01's full stage skeleton; showcase sizing is 1 option per underlying unit | The option work needs only `instruments/` and `schedule.py`; the rest of the skeleton serves the weight-native rules (16-04/16-05). Per-unit sizing keeps values in SPY price units, comparable to the probe; 12-01's notional/overhedge question stays open for Phase 13 |
| 2026-09-20 | The collar's net cash is the EXPIRING pair's settlement ONLY, not settlement minus the replacement premium | 12-01's Task 1 text and its own AC-6 contradict each other: a 0.90 put costs far more than a 1.28 call earns, so subtracting the new pair's premium makes every quiet up-quarter read negative and sell underlying to "fund" the hedge — while AC-6 requires a +3% period to CARRY. The AC is the testable contract and wins. The replacement premium is still paid, as its own opening fill in the Book |
| 2026-09-20 | `cap = 1.28` and a monetisation trigger move to PHASE 13; the cap default is NOT changed unilaterally | Both are parameter/policy questions and Phase 13 is the parameter phase. Measured: the 1.28x call is 3.76 sd out on a 63-bar tenor, prices at 0.0083 and funds 0.16% of the floor over four rolls, so the collar equals the protective put to three digits. 12-01's amendment record explains why 1.28 was chosen over 1.05, so changing it silently would discard a recorded decision; it becomes a swept parameter instead |
| 2026-09-20 | The report page renders `option_overlay_probe.md` and never retypes it; a mapped heading that stops matching RAISES | One implementation, one copy of the prose — the same rule the figure builders follow. Publishing a figure under the wrong finding silently is worse than failing the build. `markdown-it-py` is DECLARED in `pyproject.toml` rather than used transitively, per the objection `pricing.py` records about scipy; tables are exactly where a hand-rolled converter breaks |
| 2026-09-20 | Figure spacing is the ONLY thing that differs between the interactive window and the published page, and it lives in `theme.py` | Everything else (ink, grid, hover box) is stamped on inside the builder, so the two cannot drift. Spacing must differ because `legend.x` is a fraction of the PLOT AREA's width while the margin is pixels, and the page renders much wider — hence inline 1.04 and export 1.10, swapped by `theme.apply_export_spacing` on the export paths only. `apply_export_theme` does NOT do the swap, because `_inline_theme` calls it too |
| 2026-08-23 | Trade rationale originates at the RULE, not derived post-hoc from the blotter | A post-hoc deriver would be guessing. `ConstantMixRule.propose` already computes equity, drift (`actual_w`, discarded outside the tolerance branch) and holds the `book`, so it can state at proposal time whether a sale crystallises a gain or a loss. A leg sold because it FELL LESS than the book can book a realised loss — only the rule knows that |
| 2026-08-23 | `propose()`'s signature and return type stay unchanged; rationale rides a same-bar `last_rationale` attribute | Changing the return type would touch kts.py and both parity suites for zero accounting benefit |
| 2026-08-23 | The JSON order ledger is reused, not reinvented | `Order.to_dict()` and the `orders/dummy_orders.json` schema already exist and are complete; the offline path simply never wired them up. The wide DataFrame became the de-facto record by omission, not by decision |
| 2026-08-23 | Split into two plans rather than one | The drafted work is four workstreams; the PLAN template caps a plan at 2-3 tasks. 02-02 is the engine, 02-03 the rendering and docs, with `depends_on: ["02-02"]` |
| 2026-08-22 | State reconciled: ROADMAP's "4 of 5 / 80%" was phase-4 plan progress sitting in a milestone field | v0.2 spans SIX phases (4-8, 10). ROADMAP also carried Phase 4 as `Complete 2026-08-02` after its 2026-08-03 reopening, had no Phase 10 row or detail section despite Phase 10 being in the milestone range and having a directory, and omitted 04-05 from Phase 4's plan list. Milestone progress is now counted in whole phases (3 of 6 = 50%), replacing the 3.5/6 half-credit that no PAUL count supports |
| 2026-09-20 | 11-01 Task 1 probe: IBKR is primary source, yfinance kept only as documented fallback | Live TWS probe (port 7497) found NO duration ceiling for daily bars: SPY TRADES returns 1993-02-01→today in one request (60 Y duration tested, no error/truncation), QQQ returns 1999-03-11→today. yfinance matches to within a day. `OPTION_IMPLIED_VOLATILITY` whatToShow gives genuine (non-synthetic) IV history back to 2006-01-09 for both symbols; `HISTORICAL_VOLATILITY` (realised) to 2005-02-16. 5 rapid sequential requests hit no pacing error. Since IBKR already reaches full ETF-inception depth with zero seam, `free-primary` and `both-parallel` were rejected as adding work for no new information |
| 2026-09-20 | Phase 13 runs across THREE plans, and 13-01 is the engine only | The sweep 13-01 carried was 13-02's work in the wrong file: a plan that both builds the trigger and searches over it cannot say which of the two failed. 13-01 = engine (v2 pricing, both triggers, per-bar persistence, one named configuration set, a measured runtime); 13-02 = in-sample grid and response surface; 13-03 = CPCV. Everything before 13-03 is in-sample by construction and may claim nothing validated |
| 2026-09-20 | The monetisation trigger is DRAWDOWN-primary; the value multiple is the comparator | Insurance should pay on the loss event, so the natural trigger is the underlying falling X% below its running peak, not the option's own value spiking. Both are built and 13-01's AC-3b reports how often they fire within 5 bars of each other — the coincidence is a hypothesis under test, not an assumption |
| 2026-09-20 | Every configuration is simulated ONCE over the full history and scored by SLICING the persisted per-bar series | Max drawdown is not recoverable from an aggregate, so the stored primitive must be the per-bar equity series, not summary statistics. This collapses 13-02's and 13-03's cost from (grid x origins) simulations to (grid), which is what makes daily-stepped origins and CPCV affordable at all. Consequence accepted and stated: a sliced window scores the overlay as if it had already been running, not starting flat |
| 2026-09-20 | The ranking metric is Calmar with a FLOORED denominator (default 0.02), not raw Calmar | Return over max drawdown explodes when a calm window's drawdown is 3%, and such a row would dominate any surface fit. The floor is arbitrary, so the share of floor-BOUND rows is a mandatory output — a score set by the floor is not a Calmar. Window peak resets at the slice start, for comparability across windows |
| 2026-09-20 | Indicative windows must include CALM regimes, and ranks are reported per set | Every drawdown window is one in which more protection wins, because premium drag never gets a chance to hurt. A crisis-only set selects mechanically for the most expensive hedge available. The headline table sorts by the WORSE of (drawdown-set rank, calm-set rank) |
| 2026-09-20 | Surfaces are fitted per window AND pooled, staged 1-D before 2-D | Four indicative windows are four REGIMES, not four replicates: the spread between them is signal, and pooling alone would average it away. A moving optimum is a finding. Lasso/ridge rejected (no variable-selection problem, not high-dimensional); trees rejected (piecewise-constant, produce the spiky surface the exercise argues against); Gaussian process noted as the later option if uncertainty bands are wanted |
| 2026-09-20 | A CPCV path measures the PROCEDURE, not a fixed parameter set | Each block along a path comes from a different split with parameters re-selected on that split's training groups, so the path traces "fit, deploy, repeat" and the 5-path distribution is the pipeline's out-of-sample sampling distribution. Consequences: a good CPCV result certifies the procedure and does NOT name parameters to deploy; and scoring every combination on every block with no training step is neither CPCV nor out-of-sample, so it stays in the plan as a descriptive in-sample display only |
| 2026-09-20 | A speed fix to a DO-NOT-CHANGE class goes in as a SUBCLASS through an existing injection point, never as an edit | `13-01-IMPLEMENTATION-CONTEXT.md` section 6 listed four candidate fixes for the quadratic simulator cost, found that all four touch `Book`/`Ledger`/`PortfolioSimulator`, and concluded an explicit boundary amendment was required. A fifth option touches none of them: `PortfolioSimulator.__init__` already accepts `ledger=` (`simulator.py:26,37`), so `NarrowLedger(StateLedger)` is handed in at construction. Byte-identity for `rebalance_study` / `drawdown_rotation_sim` / `overlay_runs` then comes for free rather than by hashing — they pass no `ledger=`, so their code path does not change at all. Measured: 6.5 min -> 6.3 s over 5,201 bars, equity bit-identical |
| 2026-09-20 | The narrow ledger drops per-leg columns, and that OBLIGES every caller to persist the blotter and the rule history | The entire quadratic term was one column per symbol per field while `Book` never retires a `Position` — ~82 dead legs over 20 years. Dropping those columns means the state frame CANNOT EXPLAIN ITS OWN EQUITY PATH, which is acceptable only because `sim.blotter()` (~164 fills, O(1) each) and `rule.history` carry the per-leg story and are already cheap. Written into the module header, 13-01's amended AC-6 and 13-02's AC-2, because a future caller that persists only equity gets a fast run it cannot debug |
| 2026-09-20 | `Book` and `StateLedger` are different objects, and the RESIDUAL timing term is in `Book` | Easy to conflate and worth pinning: `Book` is live state (a dict of `Position`s, signed qty, VWAP entry, realised P&L); `StateLedger` is the per-bar recording written from it. 13-01.1 narrowed the recording only. What is left — 0.36 ms/bar at 2,000 bars against 1.21 over the full 5,201 — is `Book.unrealised()` and `Book.gross_exposure()` each walking ~82 dead legs on every bar, because `Book.position()` auto-creates and nothing removes. That is candidate fix 2 in 13-01's context section 6; declined here because it changes `book.symbols` semantics and 6.3 s is already affordable. Routed to 13-01 |
| 2026-09-20 | Option lifecycle is classified from the RUNNING POSITION, never from the fill's `side` | A long put is bought to open and sold to close; a collar's short call is sold to open and bought to close — so `side` classifies the two structures in opposite directions off the same rule. `classify_option_events` tracks signed qty per symbol through the blotter in bar order and calls a fill opening when it moves |position| away from flat. One definition, in `strategies/scoring.py`, shared by 13-01's Task 3 tables and 13-02's path panels, because two definitions of "what is a roll" stay invisible until a figure contradicts a table |
| 2026-08-03 | The Master API client ID is NOT required to see another client's executions | The connection was still `CLIENT_ID = 161` when the five fills came back; only `ExecutionFilter.clientId` changed, to 151. So connection-level scoping was never the restriction and the filter alone was. An earlier connection-scoping theory is wrong. The prior observation that `client_id=0` "returned nothing" was taken on a Sunday with no fills, and tested nothing |
| 2026-09-26 | AC-3b re-opened and satisfied the same day it was descoped | The descoping's premise — `monetise_multiple` has no full-history run — was itself a bug (`option_monetisation_batch.run_one` missing `ledger=NarrowLedger()`), not a genuine gap. Fixing the one argument let the batch complete and its own `trigger_comparison()` print the answer for free: 70.6% of drawdown firings within 5 bars of a multiple firing |
| 2026-09-26 | `docs/index.html` is a HUB, not a report; each published section lives under its own directory beside its own figures | 10-01's probe report used to BE `docs/index.html`, so 13-01's validation report was reachable only by typing `/validation/` and nothing linked back. `build_site_index.py` now owns the hub; `build_report.py` was generalised (8 new keyword arguments) to serve two reports off one implementation rather than two |
| 2026-09-26 | The batch script's own blotter (`rule.events_frame()`) and AC-6's defined blotter (`sim.blotter()`) are DIFFERENT artefacts, not reconciled | Found while persisting the GFC-window blotter to match AC-6's literal spec. `option_monetisation_batch.py` persists the richer rule-reasoned log instead. Flagged, not fixed — whoever next reads both directories should expect different columns |

### Deferred Issues

- **Plotting is fragmented across three surfaces, and the Dash time-series axis is not a
  real date axis** — found 2026-09-20 while building `research/data_inspection/`.
  `panel.py` (factor/sector/PCA grid), `dash_timeseries_app.py` (generic time series +
  histogram, Dash-serving), and `pipelines/option_probe_figures.py` (calls
  `plotly.graph_objects` directly, routes through neither viz module) are three separate
  plotting owners. Separately: `_build_level_figure`'s x-axis (`dash_timeseries_app.py:914-918`)
  is an INTEGER row-position axis with hand-rendered `tickvals`/`ticktext` from
  `_format_time_label()` (`:260-274`, default `%y-%m-%d %H:%M` — always shows a `00:00`
  time-of-day even on pure daily bars), not a native Plotly datetime axis. That is WHY it
  looks and behaves differently from `option_probe_figures.py` and ad hoc scripts that pass
  a real `DatetimeIndex` straight to `go.Scatter` and let Plotly's own adaptive date-axis
  formatting run — those aren't using a nicer custom format, they're using no custom format
  at all. Fixing the mismatch is more than a tickformat tweak: it means either giving
  `_build_level_figure` a real datetime x-axis, or accepting the integer-axis design and
  making `option_probe_figures.py` (and any future option-probe pipeline) call into
  `viz/` rather than plotting standalone. User's suggestion: `x_tick_label_mode` should
  default to something daily-appropriate with an explicit "this mode is for intraday"
  note on the current `"full"` default, and the day-by-day vs intraday distinction should
  be stated in the parameter docs. No code changed — flagged for a Phase 11 (or later
  viz-consolidation) follow-up, not fixed inline.

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
- ~~**13-01's monetise/reopen THRASH blocks 13-02.**~~ **RESOLVED 2026-09-21, re-confirmed on the
  real GFC window 2026-09-22, and again on the full-history batch 2026-09-26.** The drawdown
  reference now resets on REOPEN (never on a roll, never on the monetise itself) — full reasoning
  in `13-01-PLAN.md` under "FINDINGS CARRIED IN FROM 13-01.1". Full-history count after the fix:
  17 `monetise_drawdown` monetisations (not 281) against `monetise_multiple`'s 44, with the two
  triggers agreeing within 5 bars 70.6% of the time (`option_monetisation_batch.py`, 2026-09-26).
  **13-02 is no longer blocked.**
- **`ARM_LIVE = True` is committed** in `orders/rebalance_live_debug.py:67`. The live-submit gate is
  open, and `test_debug_cell_script_is_disarmed_and_gated` fails by design as the reminder. Set it
  back to `False` when live work is finished; the suite is green at 126/126 with it disarmed.

## Session Continuity

Last session: 2026-09-26
Stopped at: **13-01 loop CLOSED.** SUMMARY: `.paul/phases/13-walk-forward-validation/13-01-SUMMARY.md`.
Everything Task 4 (publishing) needed is done except step 6 (following a handful of stale doc/
cell-comment mentions of the old `docs/figures/` path — cosmetic, non-gating). Full suite 227
passed, 1 pre-existing unrelated failure (`test_debug_cell_script_is_disarmed_and_gated`, see
Blockers).

**Two bugs found and fixed while closing the checkpoint, neither planned:**
1. `option_monetisation_batch.run_one` never passed `ledger=NarrowLedger()` — the batch script had
   SILENTLY NEVER COMPLETED A RUN against the current engine since the file was created, defaulting
   to the pre-13-01.1 O(dead-legs) ledger. Fixed; the full 5-config named set now runs in seconds
   (0.45s single-run measurement), and AC-3b's trigger-agreement comparison (70.6% within 5 bars)
   came out as a side effect — re-opening and satisfying an AC that had been descoped hours earlier
   for exactly the reason this bug caused.
2. `GITHUB_BRANCH` stale at `"feat/16-option-instruments"` in both page builders, after this
   session pushed straight to `master` rather than merging a PR. Fixed to `"master"` in both;
   all three published pages rebuilt.

**Published**: `docs/index.html` is now a hub linking `docs/probing/` (10-01's probe, moved) and
`docs/validation/` (13-01's GFC report, rendered from `research/validation/option_monetisation_gfc.md`
with generated premium-financing stats), each section linking back up. One vendored
`plotly.min.js` at `docs/assets/`, zero pages depending on a CDN, 22 local links site-wide with 0
missing.

**13-02's plan was updated in the same session** with everything from 13-01 that bears on it: the
runtime constant corroborated on the actual structure it sweeps (`protective_put`/`monetise_drawdown`,
1.0-1.8s per full-history run — NOT `put_spread`'s 1.78s or `collar`'s 2.34s, which are out of
scope), an explicit note that `monetise_multiple` is not part of 13-02 at any step (stays on the
rule, stays tested via 13-01's own batch, just not swept), the 2026-09-21 reopen-reset fix's effect
on runtime attributed, and a confirmed incremental build order — one window / four moneyness
strikes / no monetisation trigger first, then add `monetise_drawdown`, then the rest, one step at a
time, cell by cell.

Next action: **13-02** (in-sample grid and response surface: moneyness x monetisation-drawdown,
floored Calmar, 1-D then 2-D surfaces) is UNBLOCKED — the thrash finding that blocked it is
resolved and re-confirmed twice more since. Build incrementally per the note above, not the whole
grid at once. Then 13-03 (CPCV procedure validation). Design for both in
`.paul/phases/13-walk-forward-validation/CONTEXT.md`, subject to 13-02's own override table.
Alternatives already scoped: 02-02/02-03 (trade rationale), 07-02, 04-05, 11-02, 10-02.
Resume file: `.paul/phases/13-walk-forward-validation/13-02-PLAN.md`

**Branch state, 2026-09-20.** Three loops closed on `feat/16-option-instruments` (16-02, 16-03,
10-01), committed and pushed. The USER opens the PR to master. TWO things must happen around that
merge, neither of them caught by any test:
1. `GITHUB_BRANCH` in `src/pipelines/build_report.py` is `feat/16-option-instruments`. On merge it
   becomes `master`, or every source link on the published page 404s.
2. GitHub Pages must be ENABLED in repository settings, serving `docs/` off the default branch.
   Until then the report exists but is not served.

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
