---
description: "13-01 implementation handoff — what landed, what is proven, and the measured quadratic simulator cost that blocks 13-02's grid"
phase: 13-walk-forward-validation
plan: 01
type: Context
about: "Portfolio-Manager"
---

# 13-01 — Implementation Context and Handoff

Written 2026-09-20, mid-APPLY. Tasks 1 and 2 are complete and verified; Task 3's code exists but
its full-history run has **not** been completed. This file records what landed, the evidence for
it, and one measured finding that matters more than the run itself.

---

## 1. Status

| task | state | evidence |
|---|---|---|
| Task 1 — v2 pricing in the rule | **COMPLETE** | AC-1 hashes unchanged; AC-2 tests pass |
| Task 2 — monetise / wait / reopen state machine | **COMPLETE** | 23 new tests, AC-3/AC-3b/AC-4 covered |
| Task 3 — full-history batch run | **CODE WRITTEN, RUN NOT COMPLETED** | stopped deliberately; see §5 |
| Checkpoint | not reached | — |

Nothing is running in the background. Nothing was written under `outputs/`.

---

## 2. What was already in the repo, and what genuinely had to be built

This was checked rather than assumed, because the working assumption at the start of the session
was that most of 13-01 already existed.

**Already present, and reused rather than rewritten:**

- `load_real_iv` (`src/pipelines/option_probe_figures.py:236`) and `align_real_iv` (`:247`) — real
  IV loading, with a loud raise when the window is not covered.
- `iv_paths_market` (`:266`) — the v2 surface call, at LADDER level:
  `synthetic_iv_surface(k, tenor, spot_path, base=real_iv)`. Both terms live, with the
  "never term B alone" reasoning already written down.
- `overlay_runs` (`:618`) — the three rules already running through `PortfolioSimulator`, with the
  "legs proposed but never filled" guard. The simulator wiring was indeed already migrated.
- `synthetic_iv_surface` (`instruments/vol.py`) — needed **no change at all**. 12-01 predicted this
  exactly: "`base` is already a keyword argument — v2 is a CALLER change." `pricing.py` likewise
  untouched.

**Genuinely missing, and the reason the rule had to be edited:**

`rules/options.py` had `_v1_vol` and nothing else — no `vol_mode`, no `iv_series`, no monetisation.
`vol_fn` and `_price` live in this same module; the hook existed, but `_price` called it as
`self.vol_fn(leg.strike, tau, entry["spot_ref"])` — the strike-time spot. The CURRENT spot was in
scope (it is `_price`'s own `spot` argument, handed to `leg.price` as the Black-Scholes
underlying) but was never handed to the vol calculation. So vol saw a stale moneyness reference
while the pricer saw the live one.

**Could a caller have worked around it without editing the rule? Yes — and the first draft of this
file overstated the case by saying otherwise.** A closure capturing the rule and reading
`rule._bar` / `rule._ts` could look up the current spot from the full series and ignore the
`spot_ref` it was passed. It would compute a correct v2 number.

Three reasons the hook was still not sufficient, the third of which is binding:

1. `vol_fn(strike, tau, spot_ref)` receives no timestamp and no leg, so such a closure must reach
   into the rule's private state to discover which bar it is on — a callback inspecting the object
   that is calling it.
2. It would have to silently discard the argument it was handed, which reads as a defect to anyone
   maintaining it later.
3. **A generic callback cannot enforce the v1/v2 coupling.** Any caller could supply a function
   that uses the current spot while leaving `base` at 0.16 — term B alone, which the 2026-08-29
   decision forbids because a falling spot then slides a fixed strike down the smirk and makes a
   crashing market look like cheaper insurance. 13-01's AC-2 requires in terms that there be "no
   setting that switches current-spot moneyness on with the vol level frozen, enforced in code",
   and a hook cannot provide that. A `vol_mode` flag validated at construction can.

So the accurate statement is not "v2 was impossible from outside" but "v2 could not be made the
*only* reachable v2, and a missing IV bar could not be made to raise rather than silently price at
0.16". Those two guarantees are what required editing the rule. That is also why the probe could
demonstrate v2 at ladder level while no *rule* had ever been priced with it.

### 2.1 The probe ran on TWO tracks, and they never met

This is the point most easily got wrong when judging how much of 13-01 already existed, so it is
worth stating exactly.

| | Track A — ladder maths | Track B — `overlay_runs` |
|---|---|---|
| functions | `iv_paths`, `put_paths`, `iv_paths_market`, `episode_paths` | `_overlay_rules`, `overlay_runs` |
| uses the rules? | **no** | **yes** — `ProtectivePutRule`, `PutSpreadRule`, `RollingCollarRule` |
| uses `PortfolioSimulator`? | no | **yes** |
| produces fills / marks the Book? | no | **yes** — the guard at `:643` inspects `sim.fills` for option symbols |
| vol used | **real IV** (`load_real_iv` → `align_real_iv` → `iv_paths_market`) | **v1 only** |
| cost profile | vectorised numpy over the ladder; flat in bars | per-bar loop through the Book and Ledger; quadratic in bars (§5) |

So the probe **did** route through the simulator and the rules, with real fills — that part of the
recollection is correct. What it never did was route REAL IV through them. `_overlay_rules`
(`option_probe_figures.py:615`) constructs every rule with:

```python
kw = dict(underlying=UNDERLYING, rate=RATE, div_yield=DIV_YIELD, base_vol=BASE_VOL)
```

— no `vol_fn`, no IV series. Real IV lived entirely in Track A
(`option_probe_figures.py:845-846`, `research/option_overlay_probe.py:266,290`), which has no Book,
no fills and no rules.

**Consequence.** Two things were new in the rule, not one: the monetisation state machine, AND v2
pricing inside the rule. The v2 *maths* existed and was reused verbatim — `_vol_for` makes the same
`synthetic_iv_surface(strike, tau, spot_now, base=iv_t)` call `iv_paths_market` makes — but it had
never been reachable from a rule, and therefore had never been through a fill, a roll or a Book.

---

## 3. What landed, and where

Three files. Nothing else in `src/` was touched.

### `src/portutils/strategies/rules/options.py` (modified)

Constructor gains `vol_mode="v1"`, `iv_series=None`, `vol_kwargs=None`, plus the policy
parameters `monetise_drawdown`, `monetise_multiple`, `reentry_iv`, `reentry_iv_pctile`,
`pctile_window=252`, `max_flat_bars`, `gate_rolls=False`. Every one defaults to off.

New methods on `OptionOverlayRule`:

| method | purpose |
|---|---|
| `_iv_now()` | this bar's real IV, keyed by timestamp; raises naming the bar if missing or NaN — never forward-fills |
| `_vol_for(leg, tau, entry, spot)` | the single v1/v2 branch point, so no path can mix them |
| `_drawdown_now(spot)` | depth below the running peak |
| `_long_value(spot)` | value of LONG legs only |
| `_multiple_now(spot)` | long value over long premium; `None` when nothing was paid |
| `_monetise_trigger(spot)` | returns `"drawdown"`, `"multiple"` or `None` |
| `_gate_open()` | absolute or trailing-percentile re-entry gate |
| `_strike_legs(spot, units)` | build and price a period's legs — shared by roll and reopen |
| `_monetise(ts, spot, book, fired)` | close every leg, run the collar branch with `opened_premium=0.0`, go FLAT |
| `_maybe_reopen(ts, spot, held)` | gate or `max_flat_bars`, then strike fresh legs off THIS bar's spot |

New state in `reset()`: `_ts`, `_state` (`"HEDGED"`/`"FLAT"`), `_flat_since_bar`, `_peak`,
`_long_premium`. `synthetic_marks` additionally records `state`, `drawdown`, `multiple` and `iv`
per bar so a figure can draw the trigger paths.

`propose()` order each bar: update the running peak, then **monetise** (tested before the roll
gate, so a trigger firing on a roll bar closes rather than rolling into fresh, expensive
insurance), then **reopen** if FLAT, then the existing roll logic, with `gate_rolls` able to delay
a due roll.

### `src/pipelines/option_monetisation_batch.py` (new)

The runnable batch. Side effects belong in `pipelines/`, per the standing convention. Imports its
data loading from `option_probe_figures` rather than re-deriving it.

### `tests/test_option_monetisation.py` (new)

23 tests on hand-built price paths — no panel, no parquet, no TWS.

---

## 4. Verification evidence

**AC-1 (defaults change nothing).** `overlay_runs()` was hashed component-by-component before and
after the change, by stashing the modified file and re-running. Equity, events and the pre-existing
history fields are **byte-identical**:

| run | equity | events | history_core |
|---|---|---|---|
| SPY only | `24bb7cf880197b4c` | — | — |
| + protective put | `f9a52f979939f4cb` | `b268ca2a5abba8d6` | `acbeac52df1da533` |
| + put spread | `01ede1ed0d9f1adf` | `00c9e869399d38b9` | `053ad77a0f62c1f9` |
| + collar | `cdf6bddb5a69134b` | `1cc6bece50bfb79c` | `22a13534155e0b86` |

The only difference is the four additive read-out keys in `history`, which no pre-13-01 caller
reads.

**Tests.** `tests/test_option_monetisation.py` + `tests/test_option_rules.py` = **33 passed**.
Baseline for the full suite, captured before any edit: **182 passed, 1 failed**
(`test_debug_cell_script_is_disarmed_and_gated`, the deliberate `ARM_LIVE=True` reminder recorded
in STATE.md Blockers).

**What is proven, and what is not.** The mechanisms are proven on synthetic paths where the exact
case can be constructed: v2 marks equal a direct `synthetic_iv_surface` call at bar *t*'s IV and
spot; the drawdown trigger fires on the first bar at or past the threshold and not the one before;
the gate holds through a vol spike and reopens after; `max_flat_bars` forces a reopen; an
unreachable threshold reproduces the blind-roll rule to 1e-12; the collar's redeploy branch runs on
a monetise. **Not proven:** that 5,201 real bars with real IV run start to finish. That is the only
thing the long run buys, besides the timing number and the persisted series.

### One test fixture that taught something

`test_percentile_gate_holds_while_vol_is_high_relative_to_its_own_history` failed twice before it
tested anything, and both failures are properties of the gate worth keeping:

1. A **constant** IV series sits at every one of its own percentiles, so the gate opens on the
   first bar. Correct behaviour — vol is not elevated against its own history — but a fixture that
   tests nothing.
2. With a **short** calm history, the spike comes to dominate the trailing window and the low
   percentile rises to meet it, so the gate opens while vol is still objectively high. With a
   252-bar window and a handful of calm bars behind it, the percentile gate defeats itself within a
   month.

Point 2 is a real limitation of the percentile form and the reason `reentry_iv` (an absolute level)
exists as the alternative. Both are now written into the test's comments.

---

## 5. THE FINDING THAT MATTERS — simulator cost is quadratic in bars

### Measurement

One `ProtectivePutRule` configuration, v2, over leading slices of the real window:

| bars | runtime | ms/bar |
|---|---|---|
| 250 | 1.50s | 5.99 |
| 500 | 5.39s | 10.78 |
| 1,000 | 17.37s | 17.37 |
| 2,000 | 57.26s | 28.63 |

ms/bar doubles as bars double. Extrapolated to the full 5,201-bar window: **~6.5 min per
configuration**, so ~45 min for the seven-configuration batch.

### It is NOT the per-bar row append, and it is NOT option-specific in the way it first looks

An isolating run, unhedged (one symbol, no new columns ever) against hedged, same window slices:

| bars | unhedged | ms/bar | hedged | ms/bar | hedged columns |
|---|---|---|---|---|---|
| 500 | 1.36s | 2.72 | 3.19s | 6.38 | 96 |
| 1,000 | 2.65s | 2.65 | 9.73s | 9.73 | 168 |
| 2,000 | 5.59s | 2.80 | 30.81s | 15.41 | 312 |

**Unhedged is flat at ~2.7 ms/bar — linear.** So `_put_row`'s `self.df.loc[ts] = row`, which every
simulation does on every bar, is not the problem at these sizes. The entire quadratic term is the
growth in COLUMN COUNT: 96 → 168 → 312 as rolls accumulate.

### Mechanism, exactly

`Book.position()` (`portfolio/book.py:195`) auto-creates a `Position` on demand and **nothing ever
removes one**. `Book.snapshot(prices)` (`:264`) then iterates *every* position the book has ever
held:

```python
rows = {sym: p.snapshot(_resolve_price(sym, prices))
        for sym, p in self._positions.items()}
```

and `Ledger.record_book` flattens all of them into one wide row per bar, `"<SYMBOL>_<field>"`. An
option leg that expired in 2009 is still a `Position` with qty 0 in 2026, still snapshotted every
bar, still occupying ~8 columns of every row.

A 63-bar roll over 5,201 bars is ~82 rolls, so ~82 dead legs accumulate. Per-bar work grows
linearly with rolls, giving O(bars²/63) overall. The measured 312 columns at 2,000 bars is ~31
rolls × ~10 columns, which matches.

A second, smaller contributor: `_put_row` widens the frame with `self.df[k] = np.nan` once per new
symbol (`ledger.py:90`), each insert copying the frame. That is ~82 copies of an ever-wider frame,
and it is what the `PerformanceWarning: DataFrame is highly fragmented` is pointing at.

### Why the probe never hit this

Two independent reasons, one per track (§2.1).

**Track B (the simulator path) never ran a long window.** `overlay_runs()` → `load_spot_path()`
reads `data/processed/prices_spy_kmlm.parquet`: **250 bars**, 2025-07-28 to 2026-07-24. That is 3
rolls, so a handful of dead-leg columns and no visible cost — four configurations in about six
seconds. The batch reads `prices_long_history.parquet` intersected with the IV history: **5,201
bars**, ~82 rolls. 20.8× the bars, but ~430× the work.

**Track A (the ladder maths) is immune at any length.** It never constructs a Book, never records a
row and never mints a symbol — it is vectorised numpy over a fixed strike ladder, so its cost is
flat in bars. That is why real IV over a long window was never a problem there, and why the
existence of working v2 ladder figures said nothing about whether a v2 *simulation* would be
affordable.

**Nothing regressed, and the monetisation rules are not the cause.** The new policy code adds a
subtraction and a comparison per bar; v2 adds one dict lookup and one surface call per leg per bar.
The isolating table above settles it: the *unhedged* configuration, which carries no option rule at
all, is flat at 2.7 ms/bar, while the hedged one degrades in step with the column count. The cost
is the recorder, not the rule.

The probe could not have run the long window in any case — real IV only landed in 11-01, the day
before.

---

## 6. The architecture question: was the synthetic-marks design supposed to prevent this?

**Yes, and on the input side it does exactly what it was designed to do.** The price panel handed
to the simulator is SPY-only:

```python
prices = spot.to_frame(UNDERLYING)          # one column, ever
```

The rule prices its own legs each bar and returns them through `synthetic_marks`, which the
simulator merges after `propose` and before `execute` (the 2026-09-19 hook decision). No option
ever becomes a column of the input DataFrame, and the panel does not grow as legs are struck. That
half of the design is sound and is working.

**The leak is on the OUTPUT side, which the design never addressed.** `Ledger.record_book` writes a
wide state frame with a column per symbol per field, and `Book` retains every leg forever, so the
*output* frame accumulates precisely the per-option columns the *input* frame was designed to
avoid. The two halves were designed to different standards: the input path was made option-aware,
the output path was left as the generic multi-asset recorder it was before options existed.

So the instinct behind the original architecture is right, and the fix is its natural completion:
apply the same "options are marked, not columns" discipline to the ledger.

### Candidate fixes, cheapest first

1. **Drop flat positions from the snapshot.** In `Book.snapshot`, skip positions with `qty == 0`
   *and* no activity this bar. Turns the per-bar row back into roughly constant width. **Risk:** the
   state frame loses the historical columns for closed legs, which any consumer reading
   `state["SPY 2009-03-20 P450_position"]` would notice. Needs a check of who reads what.
2. **Retire dead legs from the Book.** Remove a `Position` once it is flat and its leg has expired.
   Cleaner conceptually, but changes `book.symbols` semantics and therefore anything iterating it.
3. **Collect rows, build the frame once.** Accumulate `row` dicts in a list and construct the
   DataFrame in one `pd.DataFrame(rows)` at the end of `run()`. Removes both the per-row `.loc`
   assignment and the per-column widening. Largest change, biggest win, and it also removes the
   fragmentation warning that every simulation currently emits.
4. **A narrow ledger mode for sweeps.** An opt-in flag that records only `equity` and the totals,
   since that is all 13-02 and 13-03 slice. Smallest blast radius, but it leaves the general cost
   in place for everyone else.

**All four touch `Book` / `Ledger` / `PortfolioSimulator`, which 13-01's `<boundaries>` lists under
DO NOT CHANGE.** None of them may be done inside this plan without an explicit amendment. The
recommendation is (1) or (3) as its own small plan before 13-02, with a byte-identity guarantee
against the existing `rebalance_study` / `drawdown_rotation_sim` output — the same evidence
standard 11-01 Task 4 used for the ragged-history fix.

### Why this blocks 13-02 rather than being a nuisance

13-02's whole affordability argument is "simulate each combination once over the full history, then
score every window by slicing the stored series". At ~6.5 min per combination a 10×10 grid is
**~11 hours**. The slicing design is still correct and still saves orders of magnitude against
re-running per origin — but the per-run constant has to come down before a grid is something that
can be iterated on rather than run once overnight and never revisited.

13-02's AC-1 already requires printing the projected runtime before the sweep starts. That check
would now fire on any reasonable grid, which is the correct behaviour and the reason it was
written in.

---

## 7. Is there friction in the fills simulation?

Checked, and **no** — fills themselves are cheap.

- `Book.apply_fill` dispatches to one `Position` — O(1).
- `sim.fills` is a plain list, appended per fill — linear in fills, and a 63-bar roll produces one
  or two fills per roll, so ~164 fills over 20 years. Negligible.
- `synthetic_marks` prices only the legs being closed, opened, or currently open — never the dead
  ones. The rule's own per-bar work is constant.

The friction is entirely in **recording**, not in executing. The simulator's accounting is fine; it
is the wide state frame that carries the cost. That distinction matters for choosing a fix: nothing
about the fill model, the marks hook or the rule needs to change.

One related note for 13-02: `outputs/option_monetisation/series/{config}.parquet` only needs
`equity`, `spot` and the policy read-outs. If fix (4) above is ever taken, the narrow ledger is
already sufficient for everything the later plans slice.

---

## 8. The seven configurations, and which should be cut

Each is one full day-by-day simulator pass with every parameter fixed. **This is not a grid** —
nothing is searched over. The grid is 13-02.

| # | name | rule | pricing | policy armed |
|---|---|---|---|---|
| 1 | `unhedged` | buy-and-hold SPY | — | — |
| 2 | `blind_roll_v1` | `ProtectivePutRule(floor=0.90)` | v1 (frozen `base=0.16`) | none |
| 3 | `blind_roll_v2` | `ProtectivePutRule(floor=0.90)` | v2 | none |
| 4 | `monetise_drawdown` | `ProtectivePutRule(floor=0.90)` | v2 | `monetise_drawdown=0.10`, `reentry_iv=0.18`, `max_flat_bars=126` |
| 5 | `monetise_multiple` | `ProtectivePutRule(floor=0.90)` | v2 | `monetise_multiple=2.0`, same gate |
| 6 | `put_spread_v2_ref` | `PutSpreadRule(0.90/0.80)` | v2 | none |
| 7 | `collar_v2_ref` | `RollingCollarRule(0.90/1.28)` | v2 | none |

What each comparison buys: 1 vs 2, does hedging cost more than it saves on a long history; 2 vs 3,
what real vol changes against the frozen `base=0.16` (the question 16-03 could not answer); 3 vs 4,
does monetising beat blindly rolling; 4 vs 5, do the two triggers coincide (AC-3b).

**6 and 7 should be cut.** They were carried across from the pre-trim plan text and answer nothing
13-01 asks. 13-02 is long-put-only, so no later plan needs them either. They are ~13 of the ~45
minutes for no information.

---

## 9. Open decisions

1. **What should Task 3's run cover?** Five long-put configurations over the full window (~32 min
   unattended), or a short window such as 2006-2012 for the engine proof (~3 min, GFC included,
   both triggers exercised) with the full-history run left to 13-02 which needs it anyway.
2. **Does the ledger fix happen before 13-02?** See §6. It is the difference between an iterable
   grid and an overnight one, and it needs an amendment because of the DO NOT CHANGE boundary.
3. **Process note.** 13-01's Task 3 says "MEASURE ONE RUN'S RUNTIME first, size from that number,
   not from a guess." The seven-configuration script was written first and measured second, which
   is how a 45-minute run got started before anyone knew it was 45 minutes. The measurement should
   lead in 13-02, where the projected-runtime check is already an acceptance criterion.
