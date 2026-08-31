# Phase Context

**Phase:** 16 — Strategy architecture consolidation
**Generated:** 2026-08-26
**Status:** Scoping — ready for `/paul:plan`

## Why this phase exists

Three systems in this repo compute portfolio decisions. None of them import each other, all
three use overlapping vocabulary, and each one re-implements a step the other two already have.

| | `analysis/strategies.py` | `portfolio/rules.py` | `orders/kts.py` |
|---|---|---|---|
| Shape | vectorised, whole DataFrame | event-driven, one bar | live GUI, one tick |
| Currency | **weights** | **units** | **weights → units** |
| Entry point | `run(df) -> df` | `propose(ts, prices, book)` | `_compute_recommendation()` (kts.py:2590) |
| Output | a returns column | `{symbol: Δunits}` | a `ForecastView` (kts.py:295) |
| Executes? | no | yes, into a `Book` | yes, live to IBKR |
| Rationale? | none | none | rich, but trapped in the GUI |

Verified: `strategies.py` imports nothing from `portfolio/`; `rules.py` imports only numpy.
The sole shared code is the accounting layer (`Fill`, `Order`, `SimExecutionBackend`), which
kts.py imports at kts.py:65 and which the comment at kts.py:334-345 records as having been
lifted out of kts.py verbatim.

The cost of that split is concrete, not aesthetic:

- **Weight logic exists three times.** `QuantileRiskControlStrategy.calc_targ_weight`
  (strategies.py:616) + `calc_bounded_weights` (:642); `weights_to_units` (rules.py:44);
  and kts.py's `w_target → w_capped → w_rec` ladder (kts.py:2638-2681). Three cap
  implementations, three turnover conventions.
- **Units/turnover logic exists twice.** `calc_units` (strategies.py:665) computes units and
  `unit_change` purely to cost slippage; `weights_to_units` computes the same quantity for
  execution. `calc_slippage` (:696) and the simulator's turnover accumulation
  (simulator.py:70-83) measure the same thing differently.
- **Rationale reaches nothing.** `Order.rationale` exists (fills.py:79) but `Order` is never
  constructed anywhere in `src/portutils/` — the only `Order(` call sites are ibapi's
  unrelated class. Meanwhile kts.py has the richest rationale in the codebase
  (`ForecastView`, 20 fields) and it dies with the Tk window.
- **A strategy cannot be scored without being re-expressed.** `strategies.py` produces returns
  but no orders; `rules.py` produces orders but its returns come from a different path
  (`StateLedger` → `ledger.df`). The same policy cannot be run both ways and compared.

## The target shape

**Organising principle: group by STAGE, not by domain.**

The instinct to put everything option-flavoured in one `options.py` is the wrong cut. A
structure grouped by stage means a stack trace names the step that failed — you read the
frame and know whether the raw weight, the constraint, the sizing or the order build broke.
Grouped by domain, every option bug surfaces in the same 600-line file and you bisect by
hand. The debugging argument is the strongest argument for the refactor, not the tidiness one.

So: nouns get a stage, verbs get a stage, and a "rule" is nothing more than a named
composition of stages.

### The verbs, in order

| Stage | Module | Input | Output |
|---|---|---|---|
| 1. Observe | `strategies/observe.py` | panels, book, vol surface, forecasts | one `Observation` per timestamp |
| 2. Raw weight | `strategies/signals/` | observation | unconstrained target weight |
| 3. Constrain | `strategies/constraints.py` | raw weight, current weight | constrained weight + `binding_cap` |
| 4. Size | `strategies/sizing.py` | weight delta, prices, capital | units |
| 5. Order | `strategies/orders.py` | units, prices | orders, notional, turnover |
| 6. Execute | `execution/` (sim \| live) | orders | fills |
| 7. Reflect | `analysis/` | fills / weights | returns, P&L, reconciliation |

A rule that is unit-native (options) enters at stage 4 and skips 2-3. A rule that is
weight-native (constant mix) runs 2-5. Nothing else differs between them — which is the
whole reason the split works.

### The nouns, by stage

| Stage | Module | Objects |
|---|---|---|
| Instruments | `strategies/instruments/` | real tickers; `SyntheticContract` base; `OptionLeg` (+ its pricing, delta, theta) |
| Schedule | `strategies/schedule.py` | `RollCalendar` — reset cadence, not options-specific |
| Targets | `strategies/targets.py` | raw weight, constrained weight, unit target |
| Orders | `portfolio/fills.py` (exists) | `Order` — synthetic and real share one schema |
| Fills | `portfolio/fills.py` (exists) | `Fill` — simulated and live share one schema |
| State | `portfolio/book.py`, `ledger.py` (exist) | `Position`, `Book`, `StateLedger` |

Note what this does to Phase 12: **`OptionLeg` and the three option rules do NOT live in the
same file.** `OptionLeg` is an instrument — it belongs beside every other tradeable
descriptor, because a synthetic contract is a *kind of instrument*, not a *kind of option
strategy*. The rules that build legs live at the rules layer. The roll calendar is a
schedule, not an option concept — a quarterly rebalance uses the same object.

### Why this is not a new idea — `rules.py` is already halfway here

The stages already exist in the current code; they are just trapped inside classes:

| Current | Really is |
|---|---|
| `weights_to_units` (rules.py:44) | stage 4, already free-standing |
| `DrawdownRotationRule._sell_all_units` (rules.py:273) | stage 4, private to one rule |
| `DrawdownRotationRule._buy_with` (rules.py:285) | stage 4, private to one rule |
| `DrawdownRotationRule._merge` (rules.py:295) | rule composition, private to one rule |
| kts.py `w_target → w_capped → w_rec` (kts.py:2638-2681) | stages 2-3, inlined in a GUI method |
| `calc_targ_weight` (strategies.py:616) | stage 2, in the other system |
| `calc_bounded_weights` (strategies.py:642) | stage 3, in the other system |
| `calc_units` / `calc_slippage` (strategies.py:665/696) | stages 4-5, in the other system |

Every one of those is a stage function. `rules.py` grasped roughly half the concept —
it separated *policy* from *accounting*, but not *stage* from *stage*, so the sizing and
merging steps ended up as private methods on whichever rule needed them first.

### DECIDE — `strategies/rules/`

A rule is **one thing that can fire at one point in time**, and a single firing may motivate
several orders (a collar roll is four legs, even at 1:1 fills). It composes stages; it does
not implement them.

| Method | Returns | Notes |
|---|---|---|
| `propose_weights(obs)` | target weight + delta | `None` for unit-native rules — see friction 1 |
| `propose_units(obs)` | units per instrument | from the weight delta, OR native |
| `to_orders(...)` | orders, notional, turnover | delegates to stage 5, never reimplemented |
| `rationale` | optional payload | why it fired, and how it sized |

**The rule inventory this phase produces:**

| Rule | Currency | Source |
|---|---|---|
| `BuyAndHoldRule` | weights | rules.py:117 — existing |
| `ConstantMixRule` | weights | rules.py:141 — existing |
| `DrawdownRotationRule` | weights | rules.py:227 — existing |
| `TradeListRule` | units | rules.py:86 — existing, already unit-native |
| `ConstrainedWeightRule` | weights | NEW — extracted from the kts ladder (friction 3) |
| `ProtectivePutRule` / `PutSpreadRule` / `RollingCollarRule` | units | NEW — Phase 12, built here from the start |

### Strategies — `strategies/run_strategies.py`

A strategy calls a COMBINATION of rules at each timestamp over a defined asset scope (one
strategy may run over several scopes) and merges their outputs into one set of weights and
orders. The existing merge at simulator.py:62-64 is the seed.

### REFLECT

Returns/P&L for any strategy computed by feeding its weights and/or orders through the
existing utilities: `ReturnsCalculator` (analysis/returns.py:48), `PerformanceSummary`
(analysis/performance.py:11), and the `Fill`/`Book` path for order-level P&L. These must
accept both the full order/fill JSON and a compressed derivative carrying only what is
needed. `ibkr_sync.reconcile` (ibkr_sync.py:202) is the live-side counterpart.

The same order output goes to real IBKR execution — simulated fills are one backend, not a
separate code path.

## Frictions worth deciding at plan time

These are the places the design as stated meets something in the code that resists it. None
is a blocker; each needs a call.

1. **Not every rule has a weight representation.** An option overlay is natively units —
   there is no weight form of "long 3 SPX 4750 puts" that survives a strike change. Forcing
   `propose_weights` on every rule would make option rules fabricate a number nothing
   consumes. Suggest: `propose_weights` returns `None` for unit-native rules, and the
   contract states that exactly one of weights/units must be non-`None`.

2. **Vectorised and event-driven are genuinely different, not just stylistic.**
   `strategies.py` runs whole-column pandas; rules run bar-by-bar with a live `Book`.
   Unifying the *interface* is right; unifying the *execution model* would make the
   quantile sweep dramatically slower (`QuantileRiskControlGrid` sweeps a Cartesian grid).
   Suggest: keep both, with the rule as the unit of definition and a vectorised runner that
   can execute a rule across a whole panel where the rule declares itself stateless.

3. ~~**kts.py is live and load-bearing.**~~ **RESOLVED 2026-08-26 — the kts weight ladder
   becomes a rule.** It is not a GUI concern that gets migrated eventually; it is a rule in
   its own right, and one of the most reusable ones in the repo. Stated plainly:

   > A rule that, at each point in time, recommends a weight constrained by weight floors
   > and caps AND by turnover floors and caps.

   That is `ConstrainedWeightRule` (name TBC), and its logic already exists at
   kts.py:2626-2698:

   | Step | kts.py | Becomes |
   |---|---|---|
   | raw target | `w_target = REC_ALPHA * r_hat_h` (:2638) | input — a raw target weight from any signal source |
   | risk cap | `w_capped = max(-w_cap, min(w_cap, w_target))` (:2641) | the weight floor/cap constraint |
   | which cap bound | `binding_cap` (:2643) | rationale field |
   | turnover gate | `if abs(w_capped - w_current) < w_threshold: w_rec = w_current` (:2678) | the turnover floor constraint |
   | output | `w_rec`, `w_delta`, `qty` (:2681-2698) | `propose_weights` → `propose_units` |

   Two consequences. First, the raw target becomes an INPUT rather than being computed
   inside the rule, so the same constraint rule serves the OU forecast (kts.py), the
   quantile forecast (`calc_targ_weight`, strategies.py:616) and any future signal —
   this is the deduplication, not a side effect of it. Second, kts.py keeps its GUI and
   its live path but calls the rule instead of inlining the ladder; `ForecastView` becomes
   the rule's rationale payload rather than a Tk-local dataclass.

   Residual risk is unchanged and still real: kts.py is 4000 lines and `ARM_LIVE = True`
   is currently committed (STATE.md). So the rule is EXTRACTED and tested standalone in
   16-01; kts.py is rewired to call it in a separate, later plan, with golden-fixture
   parity against the current ladder as the gate. Extraction and rewiring must not land in
   the same plan.

4. **Phase 2's reopening (02-02) overlaps this directly.** 02-02 carries rationale from rule
   → `ctx` → `Order`. That IS this phase's rationale channel. Decide whether 02-02 ships
   first and this phase builds on it, or 02-02 is absorbed here.

5. ~~**`ForecastView` vs `Order.rationale` are two rationale schemas.**~~ **Largely resolved
   by friction 3.** Once the kts ladder is a rule, `ForecastView` (kts.py:295-325) is that
   rule's rationale payload — the flat, forecast-shaped record of what the rule saw and why
   it sized as it did. `Order.rationale` (fills.py:79) stays the nested `dummy_orders.json`
   schema at the ORDER level. The two are different altitudes, not competitors: a rule
   emits a `ForecastView`-shaped payload, and `to_orders` projects it into
   `Order.rationale`. What still needs deciding is the projection itself — which of the
   20 fields survive into the order record, and whether unit-native rules (options) emit a
   different payload shape or a sparse `ForecastView`.

## Sequencing — REVISED 2026-08-26

**The risk in this phase is entirely in MIGRATING existing code, not in building new code
into the new shape.** These were previously bundled and should not have been. Separated:

### Track A — new code, built into the structure from day one. Low risk, do it now.

Nothing here can regress anything, because none of it exists yet. There is no golden
fixture to break, no live path to disturb, no caller to update.

- **16-01: stage skeleton and the shared stage functions.** `strategies/` with
  `observe.py`, `constraints.py`, `sizing.py`, `orders.py`, `schedule.py`, `targets.py`,
  `instruments/`, `rules/`. Stage 4 and 5 seeded from `weights_to_units` (rules.py:44) by
  COPY, not by move — the original stays until Track B retires it.
- **16-02: option instruments.** `instruments/options.py` — `SyntheticContract` base and
  `OptionLeg` with European pricing, delta, theta. Plus `schedule.py`'s `RollCalendar`.
  This is Phase 12's AC-1 and AC-2, relocated by stage.
  **Partially landed 2026-08-31** — `instruments/vol.py` (`synthetic_iv_surface`) and
  `instruments/pricing.py` (`black_scholes_put`) already exist and are verified against
  12-01's four anchor numbers. See "Landed ahead of the plans" below. What 16-02 still owns:
  `SyntheticContract`, `OptionLeg` (wrapping the pricer, adding delta and theta) and
  `RollCalendar`.
- **16-03: option rules.** `rules/options.py` — `ProtectivePutRule`, `PutSpreadRule`,
  `RollingCollarRule` composing the stages. Phase 12's AC-3 through AC-6.
- **16-04: `ConstrainedWeightRule`.** Extracted from the kts ladder logic, written fresh
  against stages 2-3, tested standalone. kts.py itself is NOT touched.

Track A leaves the existing code running exactly as it does today. Two implementations
coexist. That duplication is temporary and deliberate — it is what buys the zero regression
risk.

### Track B — migrating existing code. Higher risk, separately gated, optional.

Each of these is independently valuable and independently skippable. None blocks v0.4.

- **16-05:** move `BuyAndHoldRule`, `ConstantMixRule`, `DrawdownRotationRule`,
  `TradeListRule` onto the stage functions; retire the duplicated stage 4 code. Gate: the
  126-test suite green, plus `rebalance_study.py` output byte-identical.
- **16-06:** rewire kts.py to call `ConstrainedWeightRule`. Gate: golden-fixture parity
  against the current ladder. `ARM_LIVE = True` is committed — this is the riskiest single
  plan in the repo and must not be bundled with anything else.
- **16-07:** the vectorised runner and the `analysis/strategies.py` disposition.

### What this means for Phase 12

**12-01 is superseded and should be rewritten, not patched.** Its Task 1 assumed one
`portfolio/options.py` holding pricing, calendar and rules together; the stage split puts
those in three places. Its option ECONOMICS — European pricing with the discounted
intrinsic floor, the bar-based roll, collar redeployment, strike-dependent IV, the
parameter set — all survive verbatim and must be carried across, not re-derived. What
changes is only which module each piece lands in.

The v0.4 delay is now bounded by 16-01 alone (the skeleton), since 16-02 and 16-03 ARE the
option work rather than a prerequisite to it.

## Landed ahead of the plans — the vol-surface probe (2026-08-31)

Two modules and one research script were written BEFORE 16-01, out of PAUL sequence and
deliberately. What landed:

| file | contents |
|---|---|
| `src/portutils/strategies/__init__.py` | the package anchor; documents that only `instruments/` is populated |
| `src/portutils/strategies/instruments/__init__.py` | ditto for the instruments subpackage |
| `src/portutils/strategies/instruments/vol.py` | `synthetic_iv_surface`, ported from `crash.py:2579` with every constant as a keyword argument |
| `src/portutils/strategies/instruments/pricing.py` | `black_scholes_put` — European, discounted-intrinsic floor, no clamp, no scipy |
| `research/option_overlay_probe.py` + `.md` | the `# %%` strike-ladder probe and its write-up |

**Why out of sequence.** The surface existed only as an inline code block inside
`12-01-PLAN.md`. Every consumer would therefore have retyped it, and a retyped model is a
different model wearing the same numbers. Landing it as one importable function with a
verified test print is strictly cheaper than carrying it in prose through three more plans.
The alternative — writing 16-01, applying it, then 16-02 — puts three plan cycles between the
question "do these economics work?" and any answer to it.

**Why this does not compromise Track A's zero-regression property.** Both modules are new,
pure, and imported by nothing except a research script. There is no golden fixture to break,
no live path to disturb, and no caller to update — which is the same argument that put the
whole of Track A ahead of Track B.

**Why the surface is NOT in `options.py`.** 12-01 is explicit that "the surface itself is a
pipeline concern, passed in; the leg never fetches it" — a rule calls a
`vol_fn(strike, tau) -> float` handed in by the pipeline. So the surface is a SIBLING of the
pricer, not a member of the leg. Keeping `options.py` unwritten also leaves 16-02 free to
write `SyntheticContract` and `OptionLeg` fresh rather than editing around existing code.

**What the probe established** (full numbers in `research/option_overlay_probe.md`):

1. The port is exact — all four of 12-01's quoted anchors reproduce to the printed precision
   (IV 0.2098 / 0.1760, put 3.612 / 3.206), and the put never breaches its discounted
   intrinsic floor across a swept grid.
2. An ATM three-month put costs **3.53% of spot**, ≈14% of notional a year rolled quarterly.
   That is the drag figure Phase 13 must beat, and it makes an at-the-money hedge implausible
   on its face. The 0.90x strike at 0.85% (≈3.4%/yr) is the realistic candidate.
3. The only window that exists (SPY, 250 bars, **+16.0%**, max drawdown **−9.1%**) cannot
   answer the payoff half of the question. Restates the Phase 11 constraint; does not weaken it.
4. The v1/v2 gap has the sign the 2026-08-29 decision predicts (current-spot reference is
   cheaper in the selloff) at 1–6% rather than 11%, because the probe strikes its ladder once
   and never rolls, so `K/S` at the trough was 0.81–1.01. The magnitude scales with distance
   travelled since the strike was set. **The decision stands unchanged.**

**What 16-02 must now do differently.** Nothing is re-derived: `OptionLeg.price()` is expected
to be a thin wrapper over `pricing.black_scholes_put`, not a second implementation of
Black-Scholes. Delta and theta are still 16-02's to write. `DIV_YIELD` is left at 0.0 in the
probe to match 12-01's worked examples — SPY's ~1.2% is a real parameter `OptionLeg` must
carry, and is listed as a gap in the write-up rather than silently ignored.

## Out of scope

- Rewriting `QuantileRiskControlStrategy`'s maths — it moves, it does not change
- Migrating kts.py's GUI wholesale (see friction 3)
- Any change to `Book` / `Fill` / `Position` accounting semantics
- New strategies. This phase moves and consolidates what exists

## Open questions for `/paul:plan`

1. Does `strategies/` live at `src/portutils/strategies/` (library) or `src/strategies/`
   (alongside `pipelines/`)? Repo convention (CLAUDE.md) says importable library code lives
   in `src/portutils/`, which argues for the former.
2. What happens to `portfolio/`? Does it keep the accounting primitives (`book`, `fills`,
   `ledger`, `execution`) while `rules` moves out — or does the whole thing merge?
3. Is `analysis/strategies.py` renamed on the move? Four of its five classes
   (`QuantileForecastsMerger`, `IntradayIndexLevelsCleaner`, `StrategyReturnsMerger`) are
   data prep and results merging, not strategies — they may belong elsewhere entirely.
4. ~~Which cap/turnover implementation survives?~~ **Answered: kts.py's ladder**, extracted
   as `ConstrainedWeightRule` (friction 3). `calc_bounded_weights` (strategies.py:642) is
   the narrower of the two — it bounds weights but has no turnover gate and no
   `binding_cap` reporting. What remains open is whether it folds in as a configuration of
   the same rule or is dropped once its caller uses the rule.

## Code style — non-negotiable

`CLAUDE.md` at the repo root governs, and its comment rules are load-bearing for this
phase specifically, because this is a MOVE: comments travel with their code verbatim, and
retention is mandatory. Read it before writing anything. It is not restated here — the
plans should point at it, not paraphrase it.
