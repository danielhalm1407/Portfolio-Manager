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

3. **kts.py is live and load-bearing.** `ARM_LIVE = True` is currently committed
   (STATE.md), and kts.py is 4000 lines. Migrating it wholesale in one plan is high risk.
   Suggest: kts.py becomes a CONSUMER of the shared sizing utility first (its `w_target →
   w_capped → w_rec` ladder is the richest cap/gate implementation and should arguably be
   the one that survives), and its `ForecastView` becomes the model for the rationale
   payload. Full migration is a later plan.

4. **Phase 2's reopening (02-02) overlaps this directly.** 02-02 carries rationale from rule
   → `ctx` → `Order`. That IS this phase's rationale channel. Decide whether 02-02 ships
   first and this phase builds on it, or 02-02 is absorbed here.

5. **`ForecastView` vs `Order.rationale` are two rationale schemas.** kts.py's is flat and
   forecast-shaped (20 fields, kts.py:295-325); `Order.rationale` is a nested dict keyed to
   `dummy_orders.json`. One must win, or one must be a documented projection of the other.

## Sequencing — the decision this phase forces

**Phase 12 has no code yet.** `src/portutils/portfolio/options.py` does not exist; 12-01 is
a plan only. That makes right now the cheapest moment this refactor will ever have: every
later moment means writing option rules into the old shape and moving them.

Three options:

- **(a) Phase 16 before 12-01 is applied.** Option rules are written directly into
  `strategies/rules/`. No rework. Cost: delays the v0.4 hedging answer by the length of
  this phase.
- **(b) 12-01 first, migrate after.** v0.4 keeps moving. Cost: `options.py` is written
  twice, and the option rules are the ones that most stress the new interface (friction 1),
  so the interface gets designed without its hardest case in hand.
- **(c) 12-01 first, but written against the target interface.** Options land in
  `portfolio/options.py` with `propose_weights` / `propose_units` / `to_orders` shaped as
  above, then move directory later. Compromise: no interface rework, only a file move.

**Recommendation: (c).** It keeps v0.4 unblocked, and the option overlay is precisely the
case that proves whether the weights/units split works — so designing 16's interface with
12 already written against it is better evidence than designing it in the abstract. (a) is
the cleanest if v0.4 can wait; (b) is the one to avoid.

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
4. Which cap/turnover implementation survives: kts.py's `w_capped`/`w_threshold` ladder or
   `calc_bounded_weights`?
