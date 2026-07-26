# Rebalancing as the natural source of realised P&L

> **This is the canonical plan file.** Lives in the Portfolio-Manager repo at `.claude/plans/`, alongside `pnl-accounting-extraction.md`. Living document: keep the Progress Log at the bottom current as each step lands.

## Context

Phase 1 (extracting the P&L engine out of `orders/kts.py` into `portutils.portfolio`) is **done** — 22 tests green, parity with the original engine pinned at 1e-9. Progress log: [.claude/plans/pnl-accounting-extraction.md](pnl-accounting-extraction.md).

This plan is the follow-up, prompted by a correct observation about what [research/hedge_sleeves/hedge_sleeves.py](../../research/hedge_sleeves/hedge_sleeves.py) actually models.

**The finding.** `hedge_sleeves.py` builds a constant weight vector and hands it to `PanelBuilder.attribution`, which computes `contrib = weights * rets` ([panel.py:276](../../src/portutils/viz/panel.py#L276)). Holding weights *fixed* on every bar is mathematically a **daily-rebalanced constant-mix portfolio** — it already assumes you trade back to target every day, which mechanically sells whatever rallied and buys whatever fell. When KMLM rallies into an equity drawdown, that model is silently selling the hedge at the high and buying equity at the low, every single day.

**The gap.** That representation carries no units, no cost basis and no fills, so it cannot express:
- the **realised P&L** the rebalancing generates (a continuous stream, not a one-off),
- the **turnover cost** of doing it ~250×/year,
- the per-leg attribution of *which* sleeve is doing the crystallising.

So the drawdown cutoff rule in `DrawdownRotationRule` is **not** the natural way to bank hedge gains — constant-mix rebalancing already does it, continuously and without a threshold. The first scenario run bore this out: the 3% trigger fired once, on 2026-07-06, near the end of the window, and the rotation underperformed a plain hold.

**Goal.** Make constant-mix rebalancing a first-class rule in the fill-based engine, quantify the realised-P&L stream it produces on a deliberately simple two-asset book, and test whether the drawdown overlay adds anything on top — if anything.

**Decisions taken with the user:**
- Two-asset panel: **SPY (beta) + KMLM (hedge), 60/40**. Plain SPY, no leverage — the cached panel has no 2× S&P ETF and a synthetic one would be a modelled proxy, not real data.
- Policy: **constant-mix daily**. Drawdown rule **kept**, tested as an **overlay** on top of rebalancing.
- Reports must be **visual**, in the same idiom as `hedge_sleeves.py` (`PanelBuilder` + `make_level_figure`).

### On `cache_prices.py` (asked directly — recording the answer here)
It is a **caching pipeline, not synthetic data**. Default mode pulls real daily closes from IBKR via `get_equity_data` (needs TWS); `--from-csv` reuses the panel `get_equity_data` already wrote to `data/raw/market/portfolio_prices.csv`. Either way it normalises to a DatetimeIndex and writes `data/processed/prices_<tag>.parquet` so the study never re-pulls and runs offline. The completed phase-1 run used real IBKR data: 250 daily bars, 2025-07-28 → 2026-07-24. Synthetic prices exist **only** in `tests/test_simulator.py`, where assertions must not depend on what the market happened to do.

---

## 1. `ConstantMixRule` — [src/portutils/portfolio/rules.py](../../src/portutils/portfolio/rules.py)

The one genuinely new piece of logic. Each bar:

```
target_units[s] = w[s] * book.equity(prices) / price[s]      # reuse weights_to_units()
delta[s]        = target_units[s] - book.position(s).qty
```

Sizing off **current equity** (not starting capital) is what makes it constant-*mix* rather than constant-notional, and it is precisely what makes the rule contrarian: a leg that rallied is now above weight, so its delta is a sell.

Signature: `ConstantMixRule(weights, every=1, tolerance=0.0, min_trade_frac=1e-6, capital=None)`.
- `every=1` → the daily rebalance this study runs. The parameter exists because calendar rebalancing is one integer away; **only `every=1` is run here**.
- `tolerance` → optional drift band (skip a leg within X of target). Default 0 = always trade, matching what `attribution` implicitly assumes.
- `min_trade_frac` → suppress dust trades as a fraction of equity, so the blotter is not 250 bars × 2 legs of float noise.

Reuses `weights_to_units` and the `RebalanceRule` base already in the file. Emits signed delta units, so it composes with `DrawdownRotationRule` and `TradeListRule` in one simulator run exactly like the existing rules.

## 2. Durable ticker→role mapping — [config/asset_universe.yaml](../../config/asset_universe.yaml)

Long-lived infrastructure, not scaffolding for this one study. The file **already exists** as an empty stub whose header says "Define asset classes, tickers, sectors, and constraints here" — fill it rather than adding a competing file.

```yaml
universe:
  SPY:  {name: "SPDR S&P 500",            role: beta,  sleeve: broad_market}
  KMLM: {name: "KFA Mount Lucas",         role: hedge, sleeve: managed_futures}
  MNA:  {name: "IQ Merger Arbitrage",     role: hedge, sleeve: event_driven}
  FTXL: {name: "First Trust Nasdaq Semi", role: beta,  sleeve: growth_satellite}
  ...   # all 14 tickers in portfolio_prices.csv
portfolios:
  spy_kmlm:      {SPY: 0.60, KMLM: 0.40}          # this study's two-asset book
  hedge_sleeves: {ACWX: 0.30, FTXL: 0.30, ...}    # the existing 12-ticker vector
```

Roles are `hedge` / `beta` / `core`, so realised P&L can be attributed **by role** and not only by ticker — that is the reporting axis the whole question turns on. The per-ticker rationale comments currently living as inline comments in `hedge_sleeves.py` move into the YAML as `name`/`note` fields, so the descriptions have one home.

Loaded through [src/portutils/utils/config.py](../../src/portutils/utils/config.py), never by reading the YAML directly — the repo rule is that all config access goes through that module. Add alongside the existing `SETTINGS` block: `ASSET_UNIVERSE`, plus two thin helpers `tickers_by_role(role)` and `portfolio_weights(name)`. Pure reads, no side effects, matching what is already there.

Every consumer then derives its universe from this one file:
- `cache_prices.py --portfolio spy_kmlm` reads its symbol list from the YAML instead of the hardcoded `HEDGE_SLEEVE`/`HIGH_BETA` dicts.
- `rebalance_study.py` reads weights and roles from it.
- `research/rebalance_realisation.py` pulls SPY + KMLM from it, and keys its colour/label maps off `role` so hedge and beta legs are visually consistent across every chart in the repo.

## 3. Turnover in the ledger — [src/portutils/portfolio/simulator.py](../../src/portutils/portfolio/simulator.py)

Add two per-bar extras next to the existing `equity` / `gross_exposure` / `n_trades`: `turnover_notional` (Σ|qty × price| traded this bar) and `turnover_frac` (that over equity). Without these, "constant-mix daily realises more P&L" is an incomplete claim — the cost side belongs on the same page.

## 4. Data — `cache_prices.py` reads its universe from the YAML

`SPY` and `KMLM` are both already in `data/raw/market/portfolio_prices.csv`, so `--from-csv` runs offline with no TWS. Delete the hardcoded `HEDGE_SLEEVE` / `HIGH_BETA` dicts and take the symbol list from `config.portfolio_weights(args.portfolio)` instead, so adding a ticker is a YAML edit rather than a code edit. Run: `--portfolio spy_kmlm --tag spy_kmlm --from-csv`.

## 5. Pipeline — `src/pipelines/rebalance_study.py`

Three books over the same panel, same starting capital:

| book | rules | what it isolates |
|---|---|---|
| `drift` | `BuyAndHoldRule({"SPY": 0.6, "KMLM": 0.4})` | zero-realised baseline — everything stays unrealised, weights drift |
| `mix` | `ConstantMixRule(weights, every=1)` | the `hedge_sleeves.py` assumption made explicit |
| `mix+overlay` | `ConstantMixRule` + `DrawdownRotationRule` | does the cutoff add anything over plain rebalancing? |

Console/CSV output per book: final equity, realised / unrealised split, return, max drawdown, cumulative turnover, trade count, **and realised P&L per ticker and per role** (roles from the YAML) — the table that answers the actual question ("is the `hedge` role banking gains because it rallied while `beta` fell?").

Slippage sensitivity: rerun `mix` at `slippage_k` ∈ {0, 0.0005, 0.001} using the parameter already on `SimExecutionBackend`. 250 rebalances/year is where a "free" strategy quietly stops being free.

Metrics come from `PerformanceSummary`, fed **simple** returns — its `kind="simple"` resampled path is fixed as part of this sweep (§7a), so the phase-1 log-return workaround goes away.

## 6. Visual reporting — same idiom as `hedge_sleeves.py`

Charts are the deliverable, not an afterthought. Built with `PanelBuilder` + `make_level_figure` ([dash_timeseries_app.py:1506](../../src/portutils/viz/dash_timeseries_app.py#L1506)), using the same call style already in `hedge_sleeves.py` — explicit `colour_map`, `label_map`, `auto_colour_map=False`, `x_tick_label_mode='year_month'`, dark `font_color`, `fig_height`. No new plotting helpers.

Five figures:

0. **Return attribution** — the chart `hedge_sleeves.py` already produces, kept, but driven by each book's **actual** per-bar weights from the ledger rather than a constant vector (§7b). `stack_mode='stack_split_sign'`, `overall` line on top.
1. **Equity paths** — `drift` vs `mix` vs `mix+overlay` on one axis (`reindex=False`, `show_overall_line=False`). Which book ended richer.
2. **Realised vs unrealised split, per book** — the direct analogue of the attribution chart: two stacked components (`stack_mode='stack_split_sign'`) summing to total P&L, with total P&L as the `overall` line. This is the picture that makes "same P&L, different bucket" obvious.
3. **Cumulative realised P&L by ticker** for `mix` — SPY vs KMLM stacked. The chart that shows the hedge crystallising gains into equity drawdowns. Colours follow the `hedge_sleeves.py` scheme: KMLM `#f0c040`, SPY steel blue `#1f77b4`.
4. **Weight drift vs target** — each book's actual SPY/KMLM weight over time against the 60/40 line, from the same ledger columns that feed figure 0. Shows *why* `mix` trades and `drift` does not.

Delivered two ways, because they serve different moments:
- `rebalance_study.py` writes standalone HTML (`fig.write_html`) to `outputs/scenarios/` — reproducible, no kernel needed.
- `research/rebalance_realisation.py` — the interactive version, described next.

### `research/rebalance_realisation.py` — a clean copy of `hedge_sleeves.py`, extended

Structurally a **near-copy of `hedge_sleeves.py`**: same `# %%` cell layout, same imports and `importlib.reload` dev block, same `PanelBuilder` → `attribution` → `make_level_figure` flow, same explicit colour/label map style. It keeps that file's existing visuals (the stacked return-attribution chart, restricted to SPY + KMLM) and **adds the P&L figures above** in later cells.

Differences from `hedge_sleeves.py`, all deliberate:
- **Data comes from the parquet**, not a live `get_equity_data` call: `pd.read_parquet("data/processed/prices_spy_kmlm.parquet")`. Runs with TWS closed, reproducible, same bars every time.
- **Tickers and weights come from `config/asset_universe.yaml`** (`config.portfolio_weights("spy_kmlm")`), not from a literal weight block in the script. That is the durable mapping; the script just selects from it.
- The book-level cells import from `src/pipelines/rebalance_study.py` rather than re-deriving anything, so the interactive script and the pipeline can never disagree.

Lives at the top level of `research/`, not under `research/hedge_sleeves/`: it is about the rebalancing mechanic generally, not that specific sleeve. **`hedge_sleeves.py` is left untouched** — the user may retire it manually later, and until then it stays as the clean reference.

## 7. Two fixes to shared library code (folded into this sweep)

### 7a. `PerformanceSummary` simple-return compounding — one line

[performance.py:281](../../src/portutils/analysis/performance.py#L281) does `(1 + resampler).prod() - 1`, which raises `TypeError: unsupported operand type(s) for +: 'int' and 'DatetimeIndexResampler'` on current pandas — you cannot add an int to a Resampler object. Only the `kind="log"` branch works, which is why the phase-1 pipeline routes around it by feeding log returns.

```python
tr = resampler.agg(lambda s: (1 + s).prod() - 1)
```

This is the **only** broken site. The visually identical `(1 + frame).prod() - 1` at [performance.py:265](../../src/portutils/analysis/performance.py#L265) is correct — `frame` there is a real DataFrame, not a Resampler.

Regression risk is effectively zero: the `kind="simple"` resampled path currently *raises*, so nothing in the repo can depend on its output, and the `kind="log"` branch is untouched.

Verification, cheap and decisive: run the same equity path through both configurations — `kind="simple"` on simple returns and `kind="log"` on log returns — and assert the total-return columns agree to ~1e-12. Two representations of the same thing must produce the same number. Once this passes, `rebalance_study.py` can use simple returns directly and drop the log-return workaround comment.

### 7b. `simulate_weights` — deliberately NOT used, and the better substitute

[`simulate_weights`](../../src/portutils/viz/panel.py#L199) hardcodes `equal_w = np.ones(n) / n` and snaps back to 1/N on a calendar. It cannot express a 60/40 target at all, so it is the wrong tool for this study regardless of the fact that `hedge_sleeves.py` bypasses it.

The substitute is strictly better: feed `PanelBuilder.attribution` the **actual per-bar weights from the fill-based book** — `<SYM>_mark_value / equity` per symbol, already columns in the ledger frame. That is the real weight path rather than a simulated one, it automatically includes whatever the overlay traded, and it makes the return-attribution chart consistent with the P&L charts instead of a parallel model that could disagree with them.

Concretely, each of the three books gets its attribution run on its own realised weight path: `drift` shows weights visibly wandering away from 60/40, `mix` shows them pinned to it. That contrast IS the answer to the original question, drawn straight from the accounting rather than assumed.

`simulate_weights` is left alone — it remains the right tool for a quick equal-weight calendar-rebalance what-if that does not need a fill simulation.

Also noted while reading, not acted on: `hedge_sleeves.py`'s chart label reads `'Portfolio (EW, Quarterly Rebal)'` while its weights are constant-daily and not equal-weight. The new file will not inherit that label.

## 8. Tests — `tests/test_rules_constant_mix.py` — **SPECIFIED, NOT BUILT IN THIS PASS**

Per the user's instruction: **do not create or run these tests now.** The real-data run in the Verification section is the validation for this pass. Kept here so the coverage is on record and can be built later without re-deriving it.

On a synthetic two-asset panel where one leg rallies while the other falls (synthetic precisely so the assertions do not depend on what SPY and KMLM happened to do):
- constant-mix **sells the riser and buys the faller** on the next bar (sign of the delta, not just magnitude);
- realised P&L accrues **monotonically without any threshold rule** firing — the point of the whole exercise;
- the drift baseline realises **exactly zero** over the same path;
- weights are restored to target after each rebalance (within `min_trade_frac`);
- `turnover_notional` equals the blotter's Σ|notional| for that bar;
- `every=N` and `tolerance` genuinely suppress trades on the skipped bars;
- `equity == capital + realised + unrealised` holds at every bar, as in the existing simulator tests.

---

## Verification

Validation for this pass is the **real-data run**, not new tests (no new tests are written — see §8).

1. `"$HOME/miniconda3/envs/venv-stats/python.exe" -m pytest tests/ -q` — the **existing 22** must stay green. `simulator.py` and `performance.py` are being edited, and the phase-1 parity tests are what prove the §4b rules were not disturbed. No new tests added.
2. The `PerformanceSummary` fix (§7a) is verified inline in the pipeline run: same equity path through `kind="simple"` on simple returns and `kind="log"` on log returns, totals asserted equal to ~1e-12. If they disagree, the fix is wrong and the run stops there.
3. `python src/pipelines/cache_prices.py --portfolio spy_kmlm --tag spy_kmlm --from-csv` — the two-asset panel lands in `data/processed/`, built from the real IBKR CSV already on disk, with the ticker list resolved from the YAML. No TWS needed.
4. `python src/pipelines/rebalance_study.py` — three-book table, per-ticker and per-role realised attribution, five HTML figures in `outputs/scenarios/`.
5. Sanity checks on the output, not just "it ran":
   - `drift` realised == 0.00 exactly; `mix` realised strictly non-zero and growing across the path.
   - `equity == capital + realised + unrealised` at every bar for all three books.
   - KMLM shows positive realised in the windows where it rallied and SPY fell — the mechanic under discussion, confirmed in the numbers rather than assumed.
   - `mix+overlay` vs `mix`: report the difference honestly, including if the overlay adds nothing or costs money.
6. Open each HTML and confirm it renders with legible legends/colours before calling the reporting done.
7. Turnover reported alongside every P&L figure, and the slippage sweep shown, so the realised-P&L result is never quoted without its cost.

## Order of work

1. `ConstantMixRule` in `rules.py`.
2. Fill `config/asset_universe.yaml` + the `ASSET_UNIVERSE` / `tickers_by_role` / `portfolio_weights` accessors in `utils/config.py`.
3. Turnover extras in `simulator.py` + the one-line `performance.py` fix (§7a); rerun the existing 22 tests to confirm no regression.
4. Point `cache_prices.py` at the YAML; build the parquet.
5. `rebalance_study.py` + the five figures + the real run.
6. `research/rebalance_realisation.py` — the `hedge_sleeves.py` copy, extended.
7. Update the Progress Log below, and cross-link from `.claude/plans/pnl-accounting-extraction.md` as the phase-2 follow-up.

---

## Progress Log

Status key: `TODO` / `WIP` / `DONE` (with verification evidence) / `BLOCKED`.

| # | Step | Status | Notes / evidence |
|---|------|--------|------------------|
| 0 | `## Plans` conventions in `CLAUDE.md` | DONE | location + link rules + corrected verification one-liner |
| 1 | `ConstantMixRule` | DONE | [rules.py](../../src/portutils/portfolio/rules.py); exported from the package |
| 2 | `config/asset_universe.yaml` + config accessors | DONE | 14 tickers with roles, 2 named portfolios; `ASSET_UNIVERSE` / `asset_role` / `tickers_by_role` / `portfolio_weights` in [config.py](../../src/portutils/utils/config.py) |
| 3 | Turnover extras + `performance.py` §7a fix | DONE | 22 tests green; simple-vs-log totals agree to **0.0** (exact); ledger turnover == blotter Σ\|notional\| on all 3 books |
| 4 | `cache_prices.py` reads YAML; parquet built | DONE | `prices_spy_kmlm.parquet`, 250 bars, 2025-07-28→2026-07-24; `drawdown_rotation_sim.py` (a caller) migrated to roles and reproduces its old numbers exactly |
| 5 | `rebalance_study.py` + figures + real run | DONE | 3 books, 10 HTML figures, 6 CSVs in `outputs/scenarios/`; all invariants verified |
| 6 | `research/rebalance_realisation.py` | DONE | 13 cells, executes end-to-end, 11 figures build |

### Results (2026-07-25) — SPY 60 / KMLM 40, 250 daily bars, capital 1,000,000

| book | final equity | realised | unrealised | return | max DD | turnover | trades |
|---|---|---|---|---|---|---|---|
| drift | 1,137,670 | **0** | 137,670 | 13.77% | 4.64% | 1.0× | 2 |
| mix | 1,142,005 | **29,098** | 112,907 | 14.20% | 4.70% | 2.1× | 500 |
| mix+overlay | 1,139,086 | 125,347 | 13,740 | 13.91% | 4.38% | **13.1×** | 500 |

**The user's hypothesis is confirmed in the numbers.** Daily constant-mix rebalancing produced 29,098 of realised P&L with no threshold rule anywhere. Realised P&L by leg: SPY 20,802, KMLM 8,296. And the mechanic is exactly the claimed one — of the 250 bars, 67 had SPY falling while KMLM rose, and **86% of KMLM's entire year of realised P&L was booked on those 67 bars**. The hedge banks money by being sold into its own rally, continuously, because the rebalance sizes off live equity.

**The overlay does not earn its complexity.** `mix+overlay` ends *below* plain `mix` while trading **13.1× capital versus 2.1×** — a 6× turnover increase for a worse result. The two rules fight: the overlay dumps 50% of the sleeve on a trigger, and the constant-mix rule buys it straight back on the next bar. The large realised figure (125,347) is churn, not edge. The drawdown cutoff was the wrong instrument for this job, which was the thing in question.

**Cost is real but not decisive at this turnover:** slippage sweep on `mix` — 0bps → 1,142,005; 5bps → 1,140,841 (−1,164); 10bps → 1,139,678 (−2,327).

### Decisions log
- 2026-07-25 — Two-asset panel **SPY 60 / KMLM 40**, plain SPY (no 2x ETF in the cached data; a synthetic one would be a modelled proxy, not real data).
- 2026-07-25 — Policy is **constant-mix daily**; drawdown rule kept and tested as an **overlay**, not as the primary mechanism.
- 2026-07-25 — Tests specified in §8 but **not built this pass** — the real-data run is the validation.
- 2026-07-25 — `hedge_sleeves.py` left **untouched**; `research/rebalance_realisation.py` is a clean extended copy, to be retired manually later at the user's discretion.
- 2026-07-25 — Ticker→role mapping lives in `config/asset_universe.yaml` (filling the existing stub), read only via `portutils.utils.config`.
- 2026-07-25 — Plan files belong in the **repo** `.claude/plans/`, not `~/.claude/plans/`. This file is canonical.
- 2026-07-25 — Markdown links in plan files must be relative **to the plan file's own directory**, i.e. `../../src/...` from `.claude/plans/` — not repo-root-relative, which silently breaks in every viewer. Convention plus the verification one-liner now recorded in `CLAUDE.md` under `## Plans`. Both plan files pass (12 targets, 0 misses).
