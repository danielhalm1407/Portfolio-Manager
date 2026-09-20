---
phase: 16-strategy-architecture
plan: 03
subsystem: strategies/rules
tags: [options, protective-put, put-spread, collar, simulator, synthetic-marks, plotly]

requires:
  - phase: 16-strategy-architecture
    provides: 16-02's OptionLeg, RollCalendar, black_scholes_call
provides:
  - "RebalanceRule.synthetic_marks hook + PortfolioSimulator merging marks after propose, before execute"
  - ProtectivePutRule / PutSpreadRule / RollingCollarRule (with redeploy and funding)
  - "fig_overlay_values: three-panel strategy figure, exported to docs/figures/overlay_values.html"
  - "Finding 7 in research/option_overlay_probe.md, plus the pricing call-chain section"
affects: [Phase 13 walk-forward, Phase 10 Track A report page, 02-02 rationale channel]

tech-stack:
  added: []
  patterns:
    - "Unit-native rules: propose() returns contract units; no weight form is fabricated"
    - "A rule prices the synthetic instruments it owns; the panel prices everything else"
    - "events log per rule as the interim rationale channel until 02-02 lands"

key-files:
  created:
    - src/portutils/strategies/rules/options.py
    - src/portutils/strategies/rules/__init__.py
    - tests/test_option_rules.py
  modified:
    - src/portutils/portfolio/simulator.py
    - src/portutils/portfolio/rules.py
    - src/pipelines/option_probe_figures.py
    - research/option_overlay_probe.py
    - research/option_overlay_probe.md

key-decisions:
  - "Collar net cash is SETTLEMENT ONLY, not settlement minus the replacement premium (12-01's AC-6 wins over its task text)"
  - "Simulator extended with an additive marks hook, gated on byte-identical existing pipeline output"

duration: ~2h across 2026-09-19/20
started: 2026-09-19
completed: 2026-09-20
description: "Three hedge structures run and roll through PortfolioSimulator via an additive synthetic-marks hook; measured over the probe window, hedges reached 1.97x mid-period and expired worthless"
type: Summary
about: "Portfolio-Manager"
---

# Phase 16 Plan 03: Option rules through the simulator Summary

**The protective put, put spread and rolling collar now run as rules inside the real
`PortfolioSimulator`, rolling every 63 bars, with their legs priced by the rule that owns them.
Measured over the probe window they cost ≈3.4%/yr, cut max drawdown 9.1% → 8.4%, reached 1.97x
premium mid-period and expired worthless — because no rule here monetises.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~2h across two sessions |
| Tasks | 3 auto (all PASS) + 1 human-verify checkpoint (approved 2026-09-20) |
| Files | 3 created, 5 modified |
| Suite | 157 → 167 passed (+10); the single known ARM_LIVE failure unchanged |

## Acceptance Criteria Results

| Criterion | Status | Evidence |
|-----------|--------|----------|
| AC-1: hook changes nothing for existing callers | Pass | `rebalance_study` + `drawdown_rotation_sim` re-run; all **16 output CSVs md5-identical** to the pre-change baseline. Suite pass/fail set unchanged. |
| AC-2: legs fill, mark and settle through the Book | Pass | Rolls at bars 1/64/127/190; one put per SPY unit in the blotter; a ledger subclass in the tests asserts **no open position is ever unmarked**; an expiring 0.90 put on a path to 80 settles at exactly 10.00. |
| AC-3: structure economics per 12-01 | Pass | Spread legs 90 long / 80 short with `ValueError` on inversion; collar redeploys on a −12% period (units strictly up), carries on +3%, funds a breached cap at −12.00 net (12/140 units, under the 99.9% cap). |
| AC-4: the probe window shows each strategy's value | Pass | `fig_overlay_values` (3 panels + roll hover) in cell 10, `build_all()` and `docs/figures/overlay_values.html`; Finding 7 written up. Checkpoint approved by the user. |

## Accomplishments

- **The simulator can now hold instruments the price panel cannot quote.** `RebalanceRule.synthetic_marks` defaults to `{}`; `run()` merges each rule's marks after propose and before execute, and raises if a model price would shadow a market one.
- **Three structures, rolling, accounted through the existing Book/Fill path** — no parallel accounting, no change to `Book`, `Fill` or `Position`.
- **Finding 7**, the first measured hedged-book result in the project: ≈3.4%/yr drag, 9.1% → 8.4% max drawdown, put at **1.97x** premium at the trough (spread 2.38x) and **zero** at expiry.
- **The pricing call-chain is documented** (added 2026-09-20 at the user's request), with the measured reason the collar ≈ the protective put: the 1.28 call is **3.76 sd** out, prices at 0.0083, and funds **0.16%** of the floor across four rolls.

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/portfolio/rules.py` | Modified | `synthetic_marks` on the base rule, defaulting to `{}` |
| `src/portutils/portfolio/simulator.py` | Modified | Step "1b) Synthetic marks" between propose and execute, with a collision guard |
| `src/portutils/strategies/rules/options.py` | Created | `OptionOverlayRule` + the three structures, the events log and the per-bar value history |
| `src/portutils/strategies/rules/__init__.py` | Created | Package docstring: a rule decides, it does not price |
| `tests/test_option_rules.py` | Created | 10 offline tests on synthetic paths (flat, −12%, +3%, +40%) |
| `src/pipelines/option_probe_figures.py` | Modified | `overlay_runs`, `fig_overlay_values`, `overlay_table`, stale-kernel guard, hover-box theming |
| `research/option_overlay_probe.py` | Modified | Cell 10 |
| `research/option_overlay_probe.md` | Modified | Finding 7 + the pricing call-chain section |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Collar net cash = settlement of the expiring pair ONLY | 12-01's task text also subtracts the replacement premium, which contradicts its own AC-6 ("+3% period → carry"): a 0.90 put costs far more than a 1.28 call earns, so every quiet quarter would read negative and sell SPY to fund the hedge | The replacement premium is still paid, as its own opening fill. AC-6 passes as written |
| Sizing: 1 option per unit of underlying held | User decision 2026-09-19 | Values stay in SPY price units; 12-01's notional/overhedge question remains open |
| Rationale stays in a per-rule `events` list | 02-02's Fill/Order channel has not landed | A later plan projects it into `Order.rationale`; the figure hover reads the events log meanwhile |

## Deviations from Plan

| Type | Count | Impact |
|------|-------|--------|
| Spec fix | 1 | The collar net-cash definition above — an internal contradiction in 12-01, resolved in favour of the AC |
| Scope additions | 3 | All user-requested during the checkpoint: a per-bar value `history` on the rule (feeds the middle panel), a stale-kernel guard in `overlay_runs`, and hover-box theming + `namelength=-1` in `_inline_theme` |
| Deferred | 2 | The `cap = 1.28` question and a monetisation-trigger rule — routed to Phase 13 (see ROADMAP) |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| `ImportError: cannot import name 'OVERLAY_ORDER'` in the interactive window | A stale cached module. Kernel restart. A guard now raises if option legs are proposed but never fill, which is what a stale (pre-hook) simulator would silently do |
| Hover box unreadable inline (light text on white) and names truncated | `_inline_theme` now sets `hoverlabel` explicitly — dark box, INK text, grid border, `namelength=-1` |

## Next Phase Readiness

**Ready:**
- Phase 13 can sweep these structures; the rules are parameterised and offline-testable.
- Phase 10 Track A has its content: the write-up plus five exported figures.

**Concerns:**
- Roll-only rules cannot monetise — any Phase 13 result on them measures carry plus expiry value, nothing else.
- v1's frozen vol level makes every payoff a floor, worst on the cheap OTM strikes.
- `cap = 1.28` funds ~0.16% of the floor on a 63-bar roll; the collar is currently a protective put in disguise.

**Blockers:** None.

---
*Phase: 16-strategy-architecture, Plan: 03*
*Completed: 2026-09-20*
