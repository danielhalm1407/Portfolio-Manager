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

One directory, one lifecycle, four stages. Every loop in the repo is an instance of it.

    OBSERVE  ->  DECIDE  ->  EXECUTE  ->  REFLECT
    (windows)   (rules)     (sim|live)   (returns / P&L / reconcile)

### OBSERVE
At each timestamp, assemble the relevant recent windows: current positions (weights AND/OR
units), asset prices to date, implied vol, and where applicable rates, earnings, FCF, and
sell-side or own forecasts. One assembled observation object per timestamp, so a rule
receives data rather than fetching it.

### DECIDE — `strategies/rules/`
One class per rule. A rule is **one thing that can fire at one point in time**, and a single
firing may motivate several orders (a collar roll is four legs, even at 1:1 fills).

Each rule exposes, in this order:

| Method | Returns | Notes |
|---|---|---|
| `propose_weights(obs)` | target weights + weight delta | May return `None` — see friction 1 |
| `propose_units(obs)` | units per instrument | Derived from weight delta, OR native |
| `to_orders(...)` | orders, notional, turnover | Delegates to the SHARED utility |
| `rationale` | optional payload | Why it fired, and how it sized |

`to_orders` must NOT be reimplemented per rule — it calls one common utility (see below).

**The rule inventory this phase produces:**

| Rule | Currency | Source |
|---|---|---|
| `BuyAndHoldRule` | weights | rules.py:117 — moves as-is |
| `ConstantMixRule` | weights | rules.py:141 — moves as-is |
| `DrawdownRotationRule` | weights | rules.py:227 — moves as-is |
| `TradeListRule` | units | rules.py:86 — moves as-is; already unit-native |
| `ConstrainedWeightRule` | weights | NEW — extracted from the kts ladder (friction 3) |
| `ProtectivePutRule` / `PutSpreadRule` / `RollingCollarRule` | units | Phase 12, written here directly |

`ConstrainedWeightRule` is the one that matters most for deduplication: it takes a raw
target weight from ANY signal source and applies weight floors/caps plus turnover
floors/caps. See friction 3.

### The shared sizing utility
Weight delta → orders → notional → turnover, in one place, used by every rule and by kts.py.
`weights_to_units` (rules.py:44) is the seed; `calc_units` / `calc_slippage`
(strategies.py:665/696) are the other half that must fold into it. Lives in `portutils`.

### Synthetic instruments
Option legs are NOT rules and NOT strategies. They are instrument descriptors — a separate
utility the rules call. Phase 12's `OptionLeg` is the first instance; the synthetic-ticker
encoding (how a leg becomes a key in a `{symbol: units}` dict and a column in a price panel)
belongs here, not inside each option rule.

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

## Sequencing — DECIDED 2026-08-26

**Document all of it; implement the clean subset now; defer only what is genuinely risky.**

The refactor is fully specified in this document regardless of what gets built when — that
is the point of writing it down. Implementation splits by how cleanly a piece migrates:

**16-01 — foundation and clean migrations.** Everything that moves without behaviour change:

- The `strategies/` skeleton and the rule interface (`propose_weights` / `propose_units` /
  `to_orders` / `rationale`)
- The shared weight-delta → orders → notional → turnover utility, absorbing
  `weights_to_units` (rules.py:44), `calc_units` (strategies.py:665) and `calc_slippage`
  (strategies.py:696)
- The four existing rules — `BuyAndHoldRule`, `ConstantMixRule`, `DrawdownRotationRule`,
  `TradeListRule` — moved and re-expressed against the interface. These are the clean
  ones: self-contained, already bar-driven, already tested
- `ConstrainedWeightRule` EXTRACTED from the kts ladder (friction 3) and tested standalone
  against golden fixtures. Not yet wired into kts.py
- The synthetic-instrument descriptor utility, so option legs have a home before they exist

**16-02 — option rules.** Phase 12's three structures written DIRECTLY into
`strategies/rules/options.py` against the finished interface. No `portfolio/options.py` is
ever created, so nothing is written twice. This absorbs 12-01's Task 1.

**Deferred to later plans, deliberately:**

- Rewiring kts.py to call `ConstrainedWeightRule`. `ARM_LIVE = True` is committed and the
  file is 4000 lines; extraction and rewiring must not land together (friction 3)
- The vectorised runner and the `analysis/strategies.py` move (friction 2, open question 3)
- Full `run_strategies` multi-scope composition — 16-01 needs only the merge the simulator
  already does (simulator.py:62-64)

**What this costs Phase 12.** 12-01 no longer creates `portfolio/options.py`; its Task 1
becomes 16-02, and it gains a dependency on 16-01. The pricing, roll, redeployment and vol
decisions already amended into 12-01 are unaffected — they are about option economics, not
about where the class lives. The v0.4 hedging answer is delayed by the length of 16-01,
which is the price of not writing the same module twice.

**Why the option rules are worth waiting for rather than rushing.** They are the case that
most stresses the interface (friction 1: no weight representation). An interface designed
with its hardest case in hand is better than one designed around three rules that all
happen to be weight-native.

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
