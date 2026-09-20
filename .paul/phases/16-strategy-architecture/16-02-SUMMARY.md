---
phase: 16-strategy-architecture
plan: 02
subsystem: strategies/instruments
tags: [options, black-scholes, european, synthetic-contract, roll-calendar]

requires:
  - phase: 16-strategy-architecture (landed ahead, 2026-08-31)
    provides: instruments/vol.py (synthetic_iv_surface), instruments/pricing.py (black_scholes_put)
provides:
  - black_scholes_call and black_scholes_delta (put and call) in pricing.py
  - SyntheticContract base and the OptionLeg dataclass in instruments/options.py
  - RollCalendar (bar-counted reset cadence) in strategies/schedule.py
  - 28 offline tests in tests/test_option_instruments.py
affects: [16-03 option rules, Phase 12 completion, Phase 13 walk-forward]

tech-stack:
  added: []
  patterns:
    - "Leg as a frozen contract: a roll closes one leg and opens another, never edits a strike"
    - "Quantity lives on the Book's Position, not on the instrument"
    - "Deterministic leg symbol '<UND> <P|C> <strike:.2f> @b<expiry_bar>' as the Book key"

key-files:
  created:
    - src/portutils/strategies/instruments/options.py
    - src/portutils/strategies/schedule.py
    - tests/test_option_instruments.py
  modified:
    - src/portutils/strategies/instruments/pricing.py
    - src/portutils/strategies/instruments/__init__.py
    - src/portutils/strategies/__init__.py

key-decisions:
  - "black_scholes_put left byte-identical; the call and delta share a private _bsm_terms helper instead"
  - "theta is defined as the one-bar price difference, not the analytic dV/dt"

duration: ~25min
started: 2026-09-19
completed: 2026-09-19
description: "European call and delta beside the verified put, OptionLeg/SyntheticContract as Book-holdable instruments, and a bar-counted RollCalendar — 28 offline tests, no regressions"
type: Summary
about: "Portfolio-Manager"
---

# Phase 16 Plan 02: Option instruments Summary

**The option instrument layer is complete. There is a European call and a delta next to the
verified put. `OptionLeg` prices, gives its Greeks and names itself as a Book symbol, and
`RollCalendar` counts bars rather than dates. 28 offline tests pass, and the rest of the suite
is unchanged.**

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~25 min |
| Tasks | 2 of 2 completed, both qualified PASS |
| Files | 3 created, 3 modified |
| Suite | 129 passed → 157 passed (+28); the one known failure is unchanged |

## Acceptance Criteria Results

| Criterion | Status | Notes |
|-----------|--------|-------|
| AC-1: Call pricer exact, put-call parity | Pass | The deep-ITM put is 38.0624. Parity holds to 1e-10 at q=0 and at q=0.012. Nothing is clamped. All four 12-01 anchors reproduce. |
| AC-2: OptionLeg prices; Greeks match finite differences | Pass | `price` equals the pricer's output exactly. Delta matches a central finite difference to 1e-4 for puts and calls at 3 strikes, and stays within its bounds. Symbols are deterministic and unique. |
| AC-3: RollCalendar counts bars | Pass | `next_roll(0)=63`; `is_roll_day` is True at 63 and False at 62; `year_fraction(63)=0.25`. A test enforces that schedule.py code contains no `timedelta` and no date or calendar import. |
| AC-4: Offline and additive | Pass | The full suite has the same pass/fail set as the baseline, plus the new tests. The only failure is `test_debug_cell_script_is_disarmed_and_gated`, which fails because ARM_LIVE is True. |

## Verification Results

```
pytest tests/test_option_instruments.py -q   -> 28 passed
pytest -q (full)                              -> 157 passed, 1 failed (baseline: 129 passed, same 1 failed)
git diff pricing.py                           -> 83 insertions, 0 deletions (put untouched)
grep scipy / exercise-value clamp / timedelta -> none in code (one hit is the pricing.py
                                                 docstring's existing prose about max(K - S, 0))
```

## Files Created/Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/portutils/strategies/instruments/pricing.py` | Modified (append-only) | `_bsm_terms`, `black_scholes_call`, `black_scholes_delta` |
| `src/portutils/strategies/instruments/options.py` | Created | `SyntheticContract`, `OptionLeg` (`tau`, `is_expired`, `price`/`mark`, `intrinsic`, `delta`, `theta`, `symbol`), `BARS_PER_YEAR` |
| `src/portutils/strategies/schedule.py` | Created | `RollCalendar` carrying 12-01 AC-2's bars-not-dates rationale |
| `src/portutils/strategies/instruments/__init__.py` | Modified | Docstring now lists what 16-02 added |
| `src/portutils/strategies/__init__.py` | Modified | Docstring records 16-02 and that 16-01's skeleton was skipped |
| `tests/test_option_instruments.py` | Created | 28 offline tests using real arithmetic, with no mocks |

## Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| The put was not refactored onto `_bsm_terms` | It is the function the probe verified; refactoring it would only add risk | d1/d2 are written twice, but the plan explicitly allows this duplication |
| `theta` = the price after one bar minus the price now | The bar is the unit the simulator steps in, and this captures the discrete jump into expiry | The theta test holds by construction; parity and the finite-difference delta test are what catch a wrong formula |
| `intrinsic()` is the UNDISCOUNTED exercise value | It is the settlement price on the expiry bar (τ=0, where the discounted and undiscounted values agree) | 16-03 closes an expiring leg at `intrinsic(spot)` |

## Deviations from Plan

| Type | Count | Impact |
|------|-------|--------|
| Auto-fixed | 0 | — |
| Scope additions | 1 | Minor. `OptionLeg` gained `is_expired()` and `BARS_PER_YEAR` for 16-03's roll logic. Neither is in the task spec, but both are consistent with it. |
| Deferred | 0 | — |

The planned deviation also holds: 16-01's stage skeleton was not built, as recorded in STATE.md
on 2026-09-19.

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| `docs/figures/*.html` show as modified in git | This was not done by this plan. The figures were most likely regenerated when the user ran probe cell 9 after the 2026-09-19 theme fix. The user should review them before committing. |

## Next Phase Readiness

**Ready:**
- 16-03 can build `OptionOverlayRule` and the three structures directly on `OptionLeg`,
  `RollCalendar` and `black_scholes_call`.
- The leg symbol format is stable, so it can serve as a Book position key and a ledger column.

**Concerns:**
- `OptionLeg.symbol` rounds the strike to 2 decimal places, so two strikes within half a cent on
  the same expiry would share a symbol. No rule produces that; the limitation is documented in
  code.
- `div_yield` defaults to 0.0, which matches 12-01 and the probe. SPY's real yield is about 1.2%,
  and 16-03 should decide whether to pass it.

**Blockers:** None.

---
*Phase: 16-strategy-architecture, Plan: 02*
*Completed: 2026-09-19*
