# Extract PnL accounting out of kts.py into `portutils.portfolio`

> **Living document.** Update the Progress Log at the bottom as each step lands.

## Context

The realized/unrealized P&L engine currently lives **entirely inside** [orders/kts.py](../../orders/kts.py) as methods on the Tk `KalmanTradingApp` class. `orders/plan.md` §7.3 designed for *backend* modularity (sim vs live fill source) but never for *module* modularity — there is no importable accounting package. `src/portutils/portfolio/__init__.py` is an empty stub.

Two concrete problems:

1. **Unimportable.** `apply_fill` ([kts.py:2916](../../orders/kts.py#L2916)) and `_sim_unrealised` ([kts.py:2962](../../orders/kts.py#L2962)) are algorithmically pure — no Tk var, no IBKR object — but they are App methods, so using them means instantiating a Tk window. 30-odd call sites bind the book to `self.sim_*` scalars.
2. **Single-symbol only.** The book is three scalars (`sim_position`, `sim_avg_entry`, `sim_realised`). It cannot represent a hedge sleeve *and* a high-beta basket at once.

Goal: a reusable, multi-symbol, GUI-free accounting engine in `src/portutils/portfolio/`, with kts.py migrated to delegate to it (single source of truth for the §4b rules), plus a scenario harness that answers the motivating question — *how do realized/unrealized P&L evolve if I cut hedge-sleeve exposure during a drawdown and rotate into high-beta names to ride the recovery?*

Decisions taken with the user: **extract + migrate kts.py**; scenario fills driven by **both** scripted rules and hand-specified trade lists; prices come from **cached parquet in `data/processed/`** (pulled once via `get_equity_data`).

---

## Target structure

```
src/portutils/portfolio/
  __init__.py      # re-export Fill, Order, Position, Book, SimExecutionBackend, PortfolioSimulator
  fills.py         # Fill, Order, _iso            <- moved VERBATIM from kts.py:320-431
  book.py          # Position, Book               <- the §4b rules, generalized to N symbols
  execution.py     # SimExecutionBackend          <- moved from kts.py:434-493, de-app-ified
  ledger.py        # StateLedger                  <- accounting half of _record_state_row
  rules.py         # RebalanceRule ABC, DrawdownRotationRule, TradeListRule
  simulator.py     # PortfolioSimulator           <- price-series -> rules -> fills -> state
src/pipelines/
  cache_prices.py            # get_equity_data -> data/processed/prices_<tag>.parquet
  drawdown_rotation_sim.py   # the hedge-sleeve vs high-beta scenario run
tests/
  test_book_parity.py        # golden test vs orders/replay_state.json
```

---

## 1. `book.py` — the core (do this first)

**`Position`** — one symbol's book. Fields `symbol`, `qty` (signed), `avg_entry`, `realised`, `closed_this_bar`.

- `apply_fill(fill) -> float` — the **exact** grow/reduce/flip logic from [kts.py:2920-2960](../../orders/kts.py#L2920-L2960), moved with **every comment verbatim** (VWAP-on-grow block, the `sign(pos)*(price-avg)*closing` rationale, the flip-split explanation). Returns `realised_delta` so `SimExecutionBackend` no longer needs the before/after snapshot dance.
- `unrealised(price) -> float` — verbatim from [kts.py:2962-2968](../../orders/kts.py#L2962-L2968), including the NaN/flat guard comment.
- `snapshot(price) -> dict` — `position`, `avg_entry_price`, `entry_cost`, `mark_value`, `unrealised_pnl`, `realised_pnl`, `total_pnl`, `closed_units`. Same key names as today's `state_df`, so nothing downstream renames.
- `restore(qty, avg_entry, realised)` — for replay scrub ([kts.py:3636-3638](../../orders/kts.py#L3636-L3638)).

**`Book`** — `dict[str, Position]` + `base_equity: float`.

- `apply_fill(fill)` — routes on `fill.symbol` (**new required field on `Fill`**, defaulted so existing sim callers keep working), auto-creating the `Position`.
- `unrealised(prices: Mapping[str, float])`, `realised`, `total_pnl(prices)`, `equity(prices) = base_equity + total_pnl`.
- `snapshot(prices) -> dict` — per-symbol rows plus a `TOTAL` aggregate.
- `reset()`, `position(symbol)`, `symbols`.
- Single-symbol convenience: `Book(default_symbol="BTC")` so `book.position()` with no arg works — this is what makes the kts migration near-zero-diff.

Explicitly **out of scope** (document as limitations in the module header): FX conversion, contract multipliers, financing/borrow, commissions. Slippage is a hook (`fill_price = mark * (1 + k*side)`) left at `k=0` to match today's behaviour — cross-reference `calc_slippage` in [strategies.py:696](../../src/portutils/analysis/strategies.py#L696) rather than reimplementing a model.

## 2. `fills.py` / `execution.py` — moves, not rewrites

- `Fill`, `Order`, `_iso` move out of kts.py **unchanged** except `Fill` gains `symbol: str = ""`. All the header comments (the "superset of IBKR execDetails" rationale, the `Order`-above-`Fill` block, the `to_dict()` nesting note) travel verbatim.
- `SimExecutionBackend.__init__(self, book, on_order=None)` replaces `__init__(self, app)`. `execute(side, qty, price, ts, symbol=None, ctx=None)` uses the `realised_delta` returned by `Book.apply_fill` instead of reading `app.sim_realised` before/after; `on_order(oid, fill, ctx, role, realised_delta)` is the callback that kts.py wires to its `_register_sim_order`. The OPEN/CLOSE classification comment block is retained as-is.

## 3. `ledger.py` — `StateLedger`

Wraps a growing `state_df`. `record(ts, book, prices, extra: dict | None)` writes `book.snapshot()` fields plus any caller-supplied `extra` columns. This is the split that decouples accounting from strategy: kts.py passes its whole `ForecastView` block ([kts.py:2993-3026](../../orders/kts.py#L2993-L3026)) as `extra`; the scenario sim passes nothing. `_record_state_row`'s comments split with their code — the accounting-field comments go to `ledger.py`, the recommendation-block comments stay in kts.py.

## 4. Migrate kts.py (the delicate part)

Strategy: **keep the attribute names, change what backs them.**

- `KalmanTradingApp.__init__` gains `self.book = Book(default_symbol=<current symbol>, base_equity=...)`; the existing declaration comments at [kts.py:694-704](../../orders/kts.py#L694-L704) stay, retargeted to describe the `Book`.
- `sim_position`, `sim_avg_entry`, `sim_realised`, `_sim_closed_this_bar` become **`@property` getter/setter pairs** delegating to `self.book.position()`. Every one of the ~30 read/write sites ([kts.py:2388](../../orders/kts.py#L2388), [2463](../../orders/kts.py#L2463), [2557-2581](../../orders/kts.py#L2557-L2581), [2793-2800](../../orders/kts.py#L2793-L2800), [3097-3133](../../orders/kts.py#L3097-L3133), [3493](../../orders/kts.py#L3493), [3636-3638](../../orders/kts.py#L3636-L3638)) then compiles unchanged. This is what keeps the diff auditable.
- `apply_fill` / `_sim_unrealised` / `_reset_accounting` become one-line delegations, each keeping its existing header comment plus a line saying the rules now live in `portutils.portfolio.book`.
- `_record_state_row` keeps the `fv`/portfolio block, delegates the accounting half to `StateLedger`.
- `SimExecutionBackend(self)` construction sites become `SimExecutionBackend(self.book, on_order=self._register_sim_order)`.
- Symbol change (`clear_chart` / new contract) must call `self.book.reset()` **and** repoint `default_symbol`.

## 5. Scenario harness — the actual question

`rules.py`:
- `RebalanceRule` ABC: `propose(ts, prices, book) -> list[(symbol, target_units | target_weight)]`.
- `TradeListRule(trades_df)` — hand-specified dated trades (`ts, symbol, side, qty`). Covers the manual half.
- `DrawdownRotationRule(sleeve, growth, dd_trigger, cut_frac, recovery_trigger, ...)` — tracks equity peak; when drawdown from peak exceeds `dd_trigger`, sells `cut_frac` of the sleeve and buys the proceeds in the `growth` basket (weighted by supplied weights, e.g. beta-weighted); optionally reverses on recovery. This is the "cut hedge, ride the beta" policy.
- Both rules feed the same simulator, and a rule list can mix them ("both" per your answer).

`simulator.py` — `PortfolioSimulator(prices_df, book, rules, ledger)`, `run()`:
for each date → mark book at that row's prices → ask each rule for targets → diff vs current units → `SimExecutionBackend.execute` per symbol → `ledger.record`. Returns the per-bar state frame. Deliberately mirrors `_build_replay` ([kts.py:3363](../../orders/kts.py#L3363)) but with no Tk, no `messagebox`, no IBKR dependency.

`src/pipelines/cache_prices.py` — one-shot: `get_equity_data` ([ibkr_requests.py](../../src/portutils/ingestion/ibkr_requests.py)) for the sleeve + high-beta tickers → `data/processed/prices_<tag>.parquet`. Side-effecting, so it lives in pipelines per the repo rule.

`src/pipelines/drawdown_rotation_sim.py` — loads the parquet, builds the book with `starting_capital`, runs baseline (hold) vs rotation, and writes a comparison frame to `outputs/`. Reuse `PerformanceSummary` / `PerformanceCompare` ([performance.py:11](../../src/portutils/analysis/performance.py#L11), [:649](../../src/portutils/analysis/performance.py#L649)) for the metrics and `PanelBuilder` ([portutils/viz/panel.py](../../src/portutils/viz/panel.py)) for plotting — the same stack [research/hedge_sleeves/hedge_sleeves.py](../../research/hedge_sleeves/hedge_sleeves.py) already uses. Do **not** write new metric or plotting code.

Note the honest limitation up front: this engine is **fill/cost-basis accounting**, not a return-compounding backtester. It answers "what does my realized vs unrealized split look like under this trade sequence". `QuantileRiskControlStrategy` ([strategies.py:559](../../src/portutils/analysis/strategies.py#L559)) remains the right tool for weight-based return backtests; the two are complementary, not a merge.

---

## Verification

1. **Parity golden test** (`tests/test_book_parity.py`, currently `tests/` is empty): replay `orders/replay_orders.json` fills through the new `Book` and assert `position` / `avg_entry_price` / `realised_pnl` / `unrealised_pnl` match `orders/replay_state.json` row-for-row to float tolerance. This is the proof that the extraction changed nothing.
2. **Unit tests on the §4b rules**: grow (VWAP), pure reduce (avg entry unchanged), full close (avg → 0), long→short flip (two-leg split), short-side sign symmetry.
3. **Multi-symbol test**: two symbols with interleaved fills — per-symbol realized must be independent, `TOTAL` must sum.
4. **kts.py smoke**: launch the app, connect, run a replay on the same symbol/window as `replay_state.json`, confirm the P&L panel and the P&L / positions / entry-vs-market plots ([_draw_pnl:3986](../../orders/kts.py#L3986), [_draw_positions:4018](../../orders/kts.py#L4018), [_draw_entry_vs_market:4068](../../orders/kts.py#L4068)) are unchanged, and scrub the replay slider to exercise `restore`.
5. **Scenario run**: `python src/pipelines/drawdown_rotation_sim.py` on cached prices — sanity-check that during the drawdown realized P&L jumps negative (crystallized sleeve losses) while unrealized recovers on the high-beta leg, and that `base_equity + total_pnl` reconciles against a naive mark-to-market of units × price.

## Order of work

1. `fills.py` + `book.py` + unit tests (2, 3).
2. Parity test (1) against the existing replay exports — **must pass before touching kts.py**.
3. `execution.py`, `ledger.py`.
4. kts.py migration via properties; smoke test (4).
5. `rules.py`, `simulator.py`, the two pipelines; scenario run (5).

---

## Progress Log

Status key: `TODO` / `WIP` / `DONE` (with verification evidence) / `BLOCKED`.

| # | Step | Status | Notes / evidence |
|---|------|--------|------------------|
| 1 | `fills.py` + `book.py` + unit tests | DONE | `tests/test_book.py` — 12 tests: grow/reduce/close/flip/short-symmetry, multi-symbol independence, TOTAL aggregate, default-symbol compat |
| 2 | Parity test vs `orders/replay_state.json` | DONE | `tests/test_book_parity.py` — 535 bars, 10 orders, fill-by-fill + bar-by-bar, matches to 1e-9. Fixture has both long and short legs and 222 bars with non-zero realised, so coverage is real |
| 3 | `execution.py`, `ledger.py` | DONE | `SimExecutionBackend(book, on_order=…, slippage_k=0.0)`; `StateLedger` splits accounting fields from strategy `extra` |
| 4 | kts.py migration (properties → `Book`) | DONE (GUI smoke pending) | `tests/test_kts_migration.py` drives a **headless** `KalmanTradingApp` through the full replay and matches the original export to 1e-9, incl. the §7.5 order ledger. Live Tk smoke + replay scrub still worth doing by hand |
| 5 | `rules.py`, `simulator.py`, pipelines, scenario run | DONE | `tests/test_simulator.py` (6 tests) + real run: `python src/pipelines/drawdown_rotation_sim.py` |

### Result of the first real scenario run (2026-07-25)
Cached panel: 250 daily bars, 2025-07-28 → 2026-07-24. Sleeve = FLSP/KMLM/MNA, growth = FTXL/JPM/SPY, 3% drawdown trigger, cut 50%.

| book | final equity | realised | unrealised | return | max DD |
|---|---|---|---|---|---|
| hold | 1,094,720 | 0 | 94,720 | 9.47% | 3.14% |
| rotate | 1,061,837 | 27,268 | 34,569 | 6.18% | 3.59% |

The rotation **underperformed** on this sample — the 3% trigger only fired on 2026-07-06, near the end of the window, so the beta leg had no recovery left to ride. That is a genuine result, not a bug: `equity == capital + realised + unrealised` holds at every bar and the rotation is self-financing to the cent. The interesting part is the split: the rotation has 27k banked and 35k still at risk; the hold book has nothing banked and 95k fully exposed.

### Known issue found (NOT fixed — shared library, outside this scope)
`PerformanceSummary._total_return_resampled` ([performance.py:281](../../src/portutils/analysis/performance.py#L281)) computes `(1 + resampler).prod()` for `kind="simple"`, which raises `TypeError: unsupported operand type(s) for +: 'int' and 'DatetimeIndexResampler'` on current pandas. Only the `kind="log"` branch works. `drawdown_rotation_sim.py` feeds log returns to route around it.

### Decisions log
- 2026-07-25 — Extract **and** migrate kts.py (not a parallel copy) — one source of truth for the §4b rules.
- 2026-07-25 — Scenario fills driven by **both** scripted rules and hand-specified trade lists.
- 2026-07-25 — Prices from **cached parquet** in `data/processed/`, pulled once via `get_equity_data`; sim runs offline/reproducibly.
- 2026-07-25 — `sim_position`/`sim_avg_entry`/`sim_realised` kept as property shims over `Book` so the ~30 kts.py call sites stay untouched and the diff is auditable. `state_df` is a property over `StateLedger.df` for the same reason.
- 2026-07-25 — kts.py's `Book` uses `default_symbol=""` rather than the live ticker: the app trades one instrument at a time, so an unnamed position is invisible to every existing call site and a symbol change just resets the book. Multi-symbol routing is only used by the offline simulator.
- 2026-07-25 — Rules return **signed delta units**, not target weights or target units — that is what an execution backend consumes, and it lets a scripted policy and a manual trade list merge in one run.
- 2026-07-25 — Test/run environment is the conda env **`venv-stats`** (`~/miniconda3/envs/venv-stats/python.exe`); it is the only env with `portutils` installed (editable) and `pytest` available.
