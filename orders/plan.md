# KTS Extension Plan — Drifting-Mean OU, Quantile Forecasts, Risk-Aware Trade Recommendations

Scope: extend `orders/kts.py`. Three layers — (1) richer forecasting model, (2) uncertainty as first-class citizen, (3) trade recommendations grounded in displayed risk/return.

---

## Status — implemented vs outstanding (as of 2026-06-05)

What `kts.py` already has:
- `ForecastView`, `_forecast_quantiles` (Gaussian closed-form), `_compute_recommendation`,
  `_update_recommendation_panel` + the Recommendation panel (§7.1, build step 0).
- Quantile bands replacing the ±1σ cone in `_draw_price` (§7.2 / build step 2).
- Stacked-axes chart (`_build_axes`), accounting engine (`Fill` / `apply_fill`), `state_df`,
  and all three replay plots: P&L decomposition, position breakdown, entry-vs-market
  (§4b/§4c, build steps 4–5).
- `SimExecutionBackend` + sim path in `place_trade`: clicking Long/Short/Close fills
  locally and books through `apply_fill` with NO IBKR connection.

What is still **NOT** built (the two gaps + the rest):

### GAP A — order ledger distinct from fills (BUILT — see §7.5)
Three separate artifacts, each with one job, so the heavy trade history never bloats the
per-bar state frame:

| Artifact | Holds | Granularity | Persisted as |
|---|---|---|---|
| `self.orders: dict[int, Order]` | orders == fills (100% immediate fill in sim) | per-trade | `replay_orders.json` (dummy_orders.json shape) |
| `state_df` (augmented) | per-bar position → realised P&L **plus** the full recommendation block + portfolio value/P&L | per-bar | `replay_state.json` (one record per bar) |
| (the top widgets) | latest `state_df` row only | now | — |

Key decisions taken with the user:
- The `Order` dataclass mirrors **exactly** the nested structure of
  `orders/dummy_orders.json` — `metadata` (incl. `contract` + `rationale`), `state`,
  `history[]` — via `Order.to_dict()`. In replay there is **one fill that fills the whole
  order**, so `history` collapses to a `Submitted → Filled` pair at the same timestamp and
  `qty_filled == total_qty`, `qty_outstanding == 0`. The lifecycle plumbing (an order that
  *could* sit unfilled / partial) still exists for the live path to reuse unchanged.
- **Trades are NOT stored in `state_df`** (the old plan's `trades` column is dropped — see
  §4b). The trade/fill history lives only in the orders ledger / `replay_orders.json`.
- `state_df` instead carries, per bar: the position/P&L block **and** every `ForecastView`
  field (recommendation context at that bar) **and** `portfolio_value` + `portfolio_pnl`,
  so the user can eyeball position value vs portfolio value (are the weights sane?) at any
  scrubbed point. See §7.5 for the exact column list.
- Both JSON files are written by `_build_replay` (and on demand via an **Export JSON**
  button) so a replay run is inspectable offline / exportable for the static web view (§6).

### GAP B — historical-replay scrubber / slider (BUILT — see §7.4)
**Implemented as a precompute-once drag scrubber** (build step 7). `_fetch_replay_bars`
loads a long history via `get_historical_bars`; `_build_replay` runs the full
forecast + recommendation + accounting pipeline over it ONCE, snapshotting `state_df` +
per-bar model frames; the `replay_slider` (`ttk.Scale`) drives `_replay_to(i)`, which
reconstructs "the state as of bar `i`" by pure slicing (price history, P&L, position,
recommendation) — dragging backward works because everything is precomputed. The
band/forecast/direction decision was factored into a shared `_ou_signal_side` used by both
live auto-trade and the replay builder. See §7.4 for the full design.

Still outstanding within this area (deferred, not part of step 7):
- **Play/pause/step/speed auto-advance.** The shipped UX is pure manual drag (what the user
  asked for); a timer-driven auto-play was intentionally skipped.
- **Cached parquet/CSV loader** under `data/raw/bars/` — replay currently always pulls
  live-from-IBKR historical bars (needs a connection).
- **Recommendation cadence vs execution schedule** (§4a): evaluate every bar but only
  *execute* on a rebalance schedule (daily/weekly/monthly) — still to wire; the replay
  currently fires the signal every bar.

### Other outstanding (lower priority, unchanged from build order below)
- Drifting-mean OU (`mu_t` linear trend) — still a scalar `mu` (build step 1, §1b).
- Auto-trade still fires on band breach (`_check_auto_signal`), NOT on the recommendation's
  `w_delta` + min-turnover gate — panel is display-only, not yet wired to dispatch (step 3).
- Live IBKR reconciliation (`_reconcile_orders`, P&L match badge) — sim only (step 6, §7.3).
- Chart UX: navigation toolbar + date-range selector (step 7, §5). Hover annotation exists.
- Steps 8–11 (external trend, cyclical components, bootstrap quantiles, portfolio risk
  pipeline, Kelly, static Plotly.js export) — not started.

---

## 1. Forecasting model

### 1a. Keep OU core
Retain `KalmanOU` / `estimate_ar1` as the mean-reversion engine. Per-tick `predict`/`update` stays.

### 1b. Replace constant `mu` with a **time-varying long-run mean** `mu_t`

Reversion target becomes a function of time, not a scalar.

**Phase 1 — linear trend mean (fast feedback for testing).**
- `mu_t = mu_0 + beta * (t - t_0)`, with `beta` either:
  - hardcoded small positive slope (smoke test — visible drift over minutes), or
  - fit from the calibration window via OLS on `(t_i, price_i)`.
- OU transition becomes `x_{t+1} = phi * x_t + (1 - phi) * mu_t + noise`.
- Residuals for `phi`/`sigma` estimation computed against the **detrended** series so `phi` measures speed of reversion to the *trend*, not to a flat level.

**Phase 2 — externally-supplied trend.**
- Accept `mu_t` from an upstream model (e.g. fundamental growth path for a semi ETF).
- Practical wiring: model returns trend series aligned to bar timestamps; KTS demeans observations against it and runs OU on residual. Forecast = `mu_t_forecast + OU_residual_forecast`.
- No `mu` mutation needed inside `KalmanOU` if we instead carry an external `mu_provider(t) -> float` and adapt `predict`/`update` to call it.

**Phase 3 — cyclical components.**
- Layer additive seasonal terms onto `mu_t`: intraday (minutes → hours), daily, weekly, seasonal, industrial cycle.
- Implementation: `mu_t = trend(t) + sum_k seasonal_k(t)`. Each component fit independently or jointly via regression on history. Keep components toggleable in UI so user can isolate behaviour.

### 1c. Forecast returns, not just prices
- For horizon `h`, compute forecast price path `p_hat_{t+1..t+h}`.
- Derive forecast simple return: `r_hat_h = p_hat_{t+h} / p_t - 1`.
- Display both price path (existing chart) and `r_hat_h` as a new readout next to the forecast horizon control.

### 1d. Uncertainty — replace ±1σ cone with quantile bands

Current ±1σ Gaussian cone is a placeholder. Replace with explicit quantile forecasts.

**Initial set (2 bounds):**
- `q_high` — "best case to reasonably account for" (≈ P90–P98).
- `q_low`  — "worst case to reasonably account for" (≈ P2–P10).

**Eventual (3–5 bounds):** central forecast plus inner band (e.g. P25/P75) and outer band (P5/P95) plus an extreme tail (P1/P99) for stress.

**Derivation options:**
1. Closed-form Gaussian quantiles from OU conditional variance `sigma^2 * (1 - phi^(2h))` — fastest, current infra reusable. Just swap ±k for ±z(q).
2. Empirical residual bootstrap — resample fitted residuals to build forecast distribution. Captures fat tails OU misses.
3. Monte Carlo simulation of OU path with drifting mean for full distribution at each horizon.

Start with (1), keep API such that (2)/(3) are drop-in.

---

## 2. Uncertainty → portfolio risk pipeline

Idiosyncratic quantiles feed three stages (build incrementally):

1. **Per-asset floor.** Reject trades whose `q_low` (worst-case return) is below a configurable threshold.
2. **Forecast correlation.** Estimate forward correlations vs existing book; size position so marginal contribution to portfolio variance stays within budget.
3. **Forecast factor exposure.** Project asset onto a small factor set (sector, beta, rates, etc.). Cap incremental exposure per factor.
4. **Portfolio quantiles.** Combine per-asset distributions (with correlation) to portfolio-level `q_low`/`q_high`. Trade allowed only if it leaves portfolio worst-case above floor.

Stages 2–4 are out of scope for the first PR but the data model (per-asset forecast distribution object) must be designed to support them.

---

## 3. Trade recommendations

### 3a. Strategy: risk-aware weight mapping
- Map expected return `r_hat_h` → target weight `w_target`.
- Simple v1: piecewise linear. `w_target = clip(alpha * r_hat_h, 0, w_cap)`.
- `w_cap` itself a function of risk:
  - if `q_low < q_low_hard_floor` → `w_cap = 0` (no trade).
  - else `w_cap = f(q_low)` — e.g. `q_low >= -5%` → up to 15%; linearly scale down to 0 as `q_low` worsens.
- v2: Kelly fraction from full forecast distribution, with the same risk cap on top.
- v3: cap considers **portfolio-incremental** worst case, not just idiosyncratic.

### 3b. Display the rationale
Recommendation panel surfaces:
- Forecast central price + long-run mean it converges to (already on chart).
- `r_hat_h` expected return — neutral colour.
- `q_high` best case — green intensity proportional to upside size.
- `q_low` worst case — red intensity proportional to downside size.
- The cap currently binding (idiosyncratic floor, factor cap, portfolio cap, none).

### 3c. Order spec
Each recommendation states the actual order with all components visible:

| Field | Description |
|---|---|
| Direction | long / short / flat |
| Sizing mode | fixed lots (v1) → portfolio-weight (v2) → Kelly (v3) |
| Current weight | `w_current` |
| Target weight | `w_target` |
| Weight delta | `w_target - w_current` |
| Min turnover weight | smallest `|w_target - w_current|` that triggers a trade (suppress noise) |
| Current value | $ exposure now |
| Target value | $ exposure after |
| Value delta | $ to trade |
| Recommended order | direction + qty/notional, ready to send |

Order only dispatches (auto-trade mode) when `|weight delta| >= min turnover weight` AND all caps pass.

---

## 4. Replay mode

Goal: rerun the full forecasting + recommendation stack against historical bars to inspect behaviour and back-fit decisions.

### 4a. Bar loader + playback
- Load a historical bar series (multi-year daily feasible; intraday smaller windows) between two timestamps from IBKR (`reqHistoricalData`) or a cached parquet/CSV under `data/raw/bars/`.
- Replay controls: start index (e.g. bar 60 = use first 60 as warm-up calib window), end index, play/pause/step/speed.
- Each playback step: feed bar to the same `on_tick` pipeline, recompute forecast cone, quantiles, recommendation, update chart.
- Recommendation cadence configurable: evaluate every bar but **execute** only on a rebalance schedule (daily, weekly, monthly). Mirrors real workflow (weekly rebalancer that monitors daily).

### 4b. Position + P&L tracking

Maintain a **state dataframe** indexed by bar timestamp. Columns per row:

| Column | Definition |
|---|---|
| `position` | signed open units |
| `avg_entry_price` | VWAP of currently-open units (long > 0, short tracked symmetrically) |
| `entry_cost` | `avg_entry_price * position` (negative if short — cash received) |
| `mark_value` | `last_price * position` (negative if short) |
| `unrealised_pnl` | `(last_price - avg_entry_price) * position` |
| `realised_pnl` | cumulative crystallised P&L from closed units |

**Trades are deliberately NOT a column** (was `trades` here in the original draft). The full
fill/trade history is too heavy to pack per-row; it lives in the **order ledger**
(`self.orders` / `replay_orders.json`, §7.5), not in `state_df`. `state_df` instead gains
the recommendation block + portfolio value/P&L columns (§7.5) so each row answers both
"where am I now" and "what was the recommended trade / are my weights sane" at that bar.

Open orders (sent but unfilled, or standing limit orders) kept in a **separate dict** keyed by order id, not as columns — avoids column explosion when many orders open.

**Accounting rules** (applied per fill):
- Same-direction fill (position grows): `avg_entry_price` updated to volume-weighted average of old and new fills.
- Opposite-direction fill that **reduces** position:
  - `realised_pnl += sign(position) * (fill_price - avg_entry_price) * units_closed`
  - `avg_entry_price` unchanged for the units still open.
- Opposite fill that **flips sign** (closes existing + opens opposite): split into two legs — close leg uses old `avg_entry_price` for realised P&L; new leg becomes opening trade with `avg_entry_price = fill_price`.

Dataframe is the source of truth; widget at top (live position + total P&L figure) is just a view of the latest row.

### 4c. Replay plots (in addition to price chart)

1. **P&L decomposition** — line series of `realised_pnl`, `unrealised_pnl`, `total_pnl`. Each toggleable.
2. **Position size** — `filled`, `unfilled` (intended target not yet executed), `intended = filled + unfilled`, and a marker series for `closed_positions` (size of unit closures over time). Each toggleable.
3. **Entry vs market** — `avg_entry_price` and `last_price` on the same axis; background shaded blue when `position > 0`, pink when `position < 0`, neutral when flat.

Implementation note: matplotlib `axvspan` for the shaded long/short regions; checkboxes (`matplotlib.widgets.CheckButtons` or Tk checkbuttons toggling line `set_visible`) for series toggles.

---

## 5. Chart UX upgrades

- **Zoom + pan**: add `matplotlib.backends.backend_tkagg.NavigationToolbar2Tk` to existing canvas — gives zoom/pan/save out of the box. Alternative: switch to `mplfinance` or wrap with `mpl_interactions` for richer hover.
- **Series toggling**: per-series checkboxes (existing pattern works fine).
- **Hover beyond axes**: matplotlib `Annotation` with `clip_on=False` and `xycoords='figure fraction'` can spill out of axes; resizing requires custom event handlers.
- **Date/time range selector**: Tk Entry pair (start, end) wired to a callback that re-renders within the selected slice. Matches the dash/plotly pattern with native Tk callbacks.
- **Historical-replay scrubber (IMPLEMENTED — build step 7).** Instead of a date-range
  pair, the shipped UX is a single **drag slider** over a *precomputed* replay (the
  Plotly-range-handle feel the user asked for). Workflow: set "Bars to load", click
  **Build replay** (one heavy pass), then drag the slider to ANY bar — the chart, P&L,
  position and recommendation instantly show the state "as if the replay had run up to
  that bar." No play/pause/physical waiting; scrubbing backward works because the whole
  run is precomputed and the slider only slices. See §7.4 for the concrete design.

---

## 6. Public deployment — discussion (out of code scope)

Treat tkinter as the personal cockpit. For a public showcase, decouple a **replay-only** view (no IBKR creds, pre-loaded bars) and ship that as a web app.

**Option ranking for free public hosting:**

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Dash / Streamlit on free tier (Render, Fly.io free, HF Spaces)** | Python-native, reuses the existing analytics code directly. HF Spaces is genuinely free and persistent. | Streamlit Community Cloud and HF Spaces sleep on inactivity (cold start ~10–30s); Render free web services sleep after 15min idle. Not great for "always-on" but fine for showcase. | Best **fast-path** if you want to stay in Python. HF Spaces is the most painless. |
| **Static export (Plotly + GitHub Pages)** | Truly free, zero cold start, already on GitHub Pages. Pre-compute replay frames in Python → dump JSON → render with Plotly.js in a static page. | No live interactivity beyond what Plotly.js handles client-side; no Python at runtime. | Best **showcase** option if scenarios are pre-baked. |
| **Next.js (React/TS) on Vercel** | Vercel free tier is the actual go-to, no sleep, fast cold starts, custom domains. TypeScript ecosystem mature. Charting via Plotly.js / Recharts / Visx / Lightweight Charts (TradingView). | Need to port plotting + replay logic to TS, or expose a Python backend separately. Python compute → serverless function works but cold starts. | Best **long-term** if you want a polished, fast, always-on site. Rewrite cost is real. |
| **FastAPI backend on Fly.io / Railway + React frontend on Vercel** | Clean split: Python keeps the analytics, JS handles UI. | Two deploys; Railway free tier has shrunk; Fly free tier requires credit card. | Reasonable but more moving parts than needed for showcase. |
| **Observable notebooks / Marimo cloud** | Reactive, embedded JS charts, sharable URL. | Less control over the UX, smaller audience familiarity. | Niche. |

**Recommendation:**
1. Short term — **pre-compute replay frames in Python, dump to JSON, render with Plotly.js on the existing GitHub Pages site.** Zero hosting cost, no cold start, fully interactive (zoom/toggle/hover all native to Plotly.js). The "replay" becomes a slider over precomputed frames.
2. Medium term — if you want users to pick their own asset/window, move the compute to a **FastAPI service on Fly.io** (always-on within free allowance if light) and keep the React frontend on **Vercel**. This is the "go-to" stack people mean when they say "free webapp hosting" — Vercel + a small backend elsewhere.
3. Keep tkinter as the live trading cockpit. Don't try to make one app do both.

AppScript / GAS is not relevant here — it's bound to Google Workspace and won't render arbitrary charts well. TypeScript over JS is a quality-of-life win but not a hosting decision.

---

## 7. Implementation detail (priority-ordered): risk/return panel → plots → IBKR accounting

This section makes §3 and §4 concrete: exact kts.py functions/attributes to add, where
they hang off the existing `KalmanTradingApp`, and how they read from the library
(`portutils.ingestion.ibkr_requests`). Ordered by the priority we agreed: **show the
risk/return + recommended trade first**, *then* the graphic objects, *then* the order
accounting/reconciliation underneath.

### 7.1 — PRIORITY 1: risk/return + recommended-trade panel

Goal: make the *decision* legible before any new plotting — expected return, worst case,
best case, and the concrete recommended trade, all visible as text/colour.

**Forecast → distribution object (the data the panel renders).**
- Add a small dataclass `ForecastView` (module-level in kts.py, near `KalmanOU`):
  fields `r_hat_h`, `q_low`, `q_high`, `central_price`, `mu_now`, plus the trade fields
  `direction`, `qty`, `w_current`, `w_target`, `w_delta`, `value_current`,
  `value_target`, `value_delta`, `binding_cap` (one of `"none"/"idio_floor"/"factor"/"portfolio"`).
  This *is* the "per-asset forecast distribution object" §2 says must exist early.
- `_forecast_quantiles(self, h, qs)` → dict `{q: price}`. v1 = Gaussian closed-form
  (1d option 1): `price_q = central + z(q) * sigma * sqrt(1 - phi**(2h))`, `central`
  from `self.kalman.forecast(h)[-1]`, `z` via `statistics.NormalDist().inv_cdf(q)` (no
  scipy dep). Keep the signature so the bootstrap/MC variants (1d 2/3) drop in later.
- `_compute_recommendation(self) -> ForecastView`: pulls `r_hat_h` from the forecast
  price path (1c), `q_low`/`q_high` from `_forecast_quantiles`, maps `r_hat_h → w_target`
  per §3a v1, applies the idiosyncratic `q_low` floor to set `binding_cap`, and derives
  `qty`/`value_delta` from `w_delta * account_value`. **`account_value` comes from
  `self.ib.account_value`** (now populated by the library `accountSummary`), falling back
  to `self.cash`.

**Widgets (extend `setup_ui`).**
- New `ttk.LabelFrame` "Recommendation" beside the Trading frame. Store labels as
  `self.rec_ret_lbl` (`r_hat_h`, neutral), `self.rec_best_lbl` (`q_high`, green),
  `self.rec_worst_lbl` (`q_low`, red), `self.rec_trade_lbl` (direction + qty/notional),
  `self.rec_cap_lbl` (binding cap). Mirror the §3c order-spec table as a compact grid.
- `_update_recommendation_panel(self, fv: ForecastView)`: sets label text and scales the
  green/red **intensity** by `abs(q_high)/abs(q_low)` magnitude (interpolate hex toward
  brighter as upside/downside grows). Tk-thread only.

**Wiring.** Call `_compute_recommendation()` + `_update_recommendation_panel()` from
`on_tick`'s bar-close branch (right after the forecast is recomputed) so the panel and the
chart cone always agree. The "recommended order" shown is exactly what auto-trade would
dispatch — surface it in manual mode too, so the user sees the call before clicking.

### 7.2 — PRIORITY 2: graphic objects (existing chart + new inventory/P&L plots)

**Refactor the single axis into a stacked, shared-x figure.**
- `setup_chart` today builds one `self.ax`. Change to a `GridSpec` (or
  `fig.subplots(n, 1, sharex=True)`): `self.ax` (price, tallest), `self.ax_pnl`,
  `self.ax_pos`, `self.ax_entry`. Keep the existing price drawing untouched — just move
  it into a `_draw_price()` helper called from `redraw_chart`.
- Split `redraw_chart` into `_draw_price()` / `_draw_pnl()` / `_draw_positions()` /
  `_draw_entry_vs_market()`. `redraw_chart` calls whichever sub-axes are enabled. This
  keeps the current behaviour behind defaults (only price on until replay/live state exists).
- **Replace the ±1σ cone** (`show_forecast_bounds`) with quantile bands from
  `_forecast_quantiles`: `fill_between(fc_x, q_low_path, q_high_path)`. Add inner/outer
  bands later by stacking more `fill_between` calls at different alphas.

**The three new plots (§4c) — exact objects + data source.**
- `_draw_pnl()`: `self.ax_pnl.plot(idx, realised)`, `(idx, unrealised)`, `(idx, total)`
  from `self.state_df`. Toggle via `self.show_realised_var` / `show_unrealised_var` /
  `show_total_var` flipping `line.set_visible`.
- `_draw_positions()`: `filled`, `unfilled` (= intended − filled), `intended` lines +
  `scatter` markers for `closed_units`. Same toggle pattern.
- `_draw_entry_vs_market()`: `avg_entry_price` and `last_price` on `self.ax_entry`;
  long/short background via `axvspan` (blue when `position>0`, pink when `<0`, none flat) —
  iterate contiguous sign runs in `state_df` and draw one span each.
- `self.state_df` (pandas, indexed by bar timestamp) is the single source of truth for all
  three; the top widget (position + P&L) is just a view of its last row (§4b).

**How the plots get their numbers — LIVE vs REPLAY (interacts with `ibkr_requests`).**
- **Replay:** `state_df` is filled by the simulated accounting engine (7.3) — no IBKR.
- **Live:** the same `state_df` rows are *cross-checked / sourced* from the library on a
  cadence (extend `refresh_timer`, currently 2 s):
  - `get_positions_data(self.ib)` → DataFrame of `position`/`avgCost` per `(account, conId)`.
  - `get_account_updates(self.ib, account)` → `updatePortfolio` rows giving
    `marketValue`, `averageCost`, `unrealizedPnL`, `realizedPnL` — the authoritative P&L
    decomposition to plot against ours.
  - `get_pnl_single_data(self.ib, account, con_id)` → IBKR's contract-level
    `dailyPnL`/`unrealizedPnL`/`realizedPnL` for a direct numeric check.
  Plot OUR computed series solid and IBKR's as a faint reference overlay so divergence is
  visible at a glance.

**Chart UX (§5)** stays as-is in priority; navigation toolbar + range selector come after
the plots exist.

### 7.3 — PRIORITY 3: order accounting + IBKR reconciliation

The accounting engine is backend-agnostic; only the *fill source* differs (live IBKR vs
simulated replay). Design both to emit the SAME `Fill` shape so §4b rules and the 7.2 plots
are identical in either mode.

**Common types (module-level).**
- `Fill(order_id, ts, side, qty, price, perm_id=None, source="sim"|"live")`.
- `apply_fill(self, fill)`: implements the §4b accounting rules (VWAP on grow, realised on
  reduce, split on flip) and appends/updates the current `state_df` row. This is the ONLY
  function that mutates position/P&L — both backends funnel through it.

**Execution backend split.**
- `SimExecutionBackend` (replay): on a recommended trade, synthesize a `Fill` at the bar's
  price (optionally a slippage model later) and call `apply_fill`. Fully simulated — no
  network. Also stamps each fill/record with the forecast context (`r_hat`, `q_low`,
  `q_high`, `binding_cap` at decision time) — our EXTRA fields beyond what IBKR returns.
- `LiveExecutionBackend`: `order_id = self.order_app.place_order(contract, order)`; record
  an intended order `{order_id, intended_qty, status:"PendingSubmit"}`. Then **verify
  against IBKR** (below) and only call `apply_fill` from the *authoritative* execution
  callbacks, not from the optimistic guess.

**Live verification — "did IBKR actually take it, and is the P&L what IBKR says?"**
Because we placed the order on this client, the fills come back automatically on the reader
thread into the library `IBApp` state — no extra request needed:
- `self.ib.order_status[order_id]` (from `orderStatus`) → `status`, `filled`, `remaining`,
  `avgFillPrice`, `lastFillPrice`, `permId`. Watch `status` move
  `PendingSubmit → Submitted → Filled`; that transition IS the confirmation the order went
  through.
- `self.ib.executions` (from `execDetails`) → the actual fills (`shares`, `price`, `side`,
  `time`, `orderId`, `permId`). Build `Fill`s from THESE for `apply_fill` so our ledger
  equals IBKR's prints.
- `get_open_orders_data(self.ib)` already merges `open_orders` + `order_status` into one
  DataFrame — use it for a periodic `_reconcile_orders()` (on the `refresh_timer`) that
  flips our intended orders to filled/cancelled and reconciles `self.position` to the
  IBKR-derived figure (authoritative over the optimistic update in `place_trade`).
- For PnL truth: `get_pnl_single_data` / `get_account_updates` as in 7.2 — assert our
  `unrealised`/`realised` track IBKR's `unrealizedPnL`/`realizedPnL`; show a small "✓ matches
  IBKR / ⚠ drift Δ" badge.

**1:1 field mapping (our ledger ⊇ IBKR).** Our `state_df` / order ledger must carry at
least every field IBKR exposes, mapped directly, plus our additions:

| Our field | IBKR source (callback / helper) |
|---|---|
| `position` | `position()` / `updatePortfolio.position` (get_positions_data / get_account_updates) |
| `avg_entry_price` | `avgCost` (position) / `averageCost` (updatePortfolio) |
| `mark_value` | `marketValue` (updatePortfolio) |
| `unrealised_pnl` | `unrealizedPnL` (updatePortfolio / pnlSingle) |
| `realised_pnl` | `realizedPnL` (updatePortfolio / pnlSingle) |
| fill `qty`/`price`/`side`/`time` | `execDetails` → `shares`/`price`/`side`/`time` |
| order `status`/`filled`/`avg_fill` | `orderStatus` → `status`/`filled`/`avgFillPrice` |
| `order_id` / `perm_id` | `orderStatus`/`execDetails` → `orderId`/`permId` |
| **`intended_qty` / `unfilled`** | *ours* — target not yet executed |
| **`r_hat`/`q_low`/`q_high`/`binding_cap` at entry** | *ours* — forecast context |
| **`source` (sim/live)** | *ours* — replay vs live provenance |

**Small library additions this implies (in `ibkr_requests.py`).** The callbacks
(`orderStatus`, `execDetails`, `updatePortfolio`, `pnlSingle`) and most getters already
exist. Two gaps to fill when we get here:
- `execDetailsEnd` callback + a `get_executions_data(app, ...)` helper (calls
  `reqExecutions` with an `ExecutionFilter`, waits on the End event) — only needed to pull
  fills from *other* sessions / historical; live same-session fills already arrive via
  `execDetails`.
- An optional `get_order_status(app, order_id)` thin convenience over
  `app.order_status[order_id]` for the reconcile loop (or just read the dict directly).
Keep these in the library (pure request workflows); the GUI only orchestrates.

---

### 7.4 — Historical-replay scrubber (build step 7, IMPLEMENTED)

Design principle: **compute once, scrub freely.** All heavy work happens on one button
click; dragging the slider afterwards is pure slicing + a redraw, so it feels instant even
over thousands of bars.

**UI (in `setup_ui`, new "Historical Replay" band at grid row 7; chart moved to row 8).**
- `replay_bars_var` Entry — how many bars to pull.
- `replay_build_btn` → `_build_replay`.
- `replay_slider` (`ttk.Scale`, disabled until built) → `_on_replay_scrub`.
- `replay_pos_lbl` — "bar i/N  <timestamp>" readout of the handle position.

**Build (`_build_replay`, runs once).**
1. `_fetch_replay_bars(num)` — pull + normalize the OHLC series from IBKR (mirrors
   `refresh_30m`'s pull path; needs a connection; refuses while a live stream is running).
2. Calibrate the initial OU/Kalman on the first `W` (= calib-window) bars.
3. March bar-by-bar: advance the filter (honouring "recalibrate each bar"), compute the
   recommendation, fire the band signal (shared `_ou_signal_side`) through
   `SimExecutionBackend` with the position cap, then `_record_state_row`. One `state_df`
   row **and** one model snapshot `{phi, mu, sigma, x}` per bar.
4. Stash `_replay_bars`, `_replay_kalman_prices`, `_replay_state_df`, `_replay_frames`;
   arm the slider over `[0, N-1]`; render the final bar.

**Scrub (`_replay_to(i)`, runs on every drag).**
- Restore the model snapshot `i` (rebuild a display `KalmanOU`, pin its mean `x`).
- `ohlc_bars = deque(_replay_bars[:i+1])`, `current_bar = None` (bar `i` is closed).
- `kalman_prices = _replay_kalman_prices[:i+1]`; `state_df = _replay_state_df.iloc[:i+1]`.
- Restore the sim ledger (position/avg/realised) from row `i` so the top widgets and the
  recommendation reflect the book held at `i`. `_replay_mark` = bar `i`'s close so
  `_last_mark_price` evaluates everything at that point (never a stale live tick).
- `_on_replay_scrub` throttles redraws (~40 ms, trailing flush) so a fast drag coalesces.

**Shared decision logic.** The band/forecast/direction test was extracted from
`_check_auto_signal` into a pure `_ou_signal_side(...)` so live auto-trade and the replay
builder fire on identical logic (no duplication, no drift).

**Performance.** `REPLAY_CANDLE_LIMIT` (300): above it `_draw_price` renders the close as a
single line instead of one Rectangle per candle, keeping long-history scrubs smooth.

**Known limitation.** Under "recalibrate each bar", the orange Kalman-history dots for
*past* bars during a scrub reflect the final recalibration (one flat array is stored, not a
per-bar copy, to keep memory `O(N)`). In the default frozen-params mode the dots are exact.

`_exit_replay` (called from `clear_chart` and before a live stream starts) drops the mark
override and disarms the slider so replay state can never leak into live marking.

---

### 7.5 — Order ledger + state_df enrichment (data model, IMPLEMENTED)

Three artifacts, one job each (see GAP A table). The `Fill` stays the atomic execution
print; the new `Order` sits **above** it and owns the dummy_orders.json-shaped record.

**`Order` dataclass (module-level, next to `Fill`).** Holds the flat fields needed to
rebuild the exact `dummy_orders.json` nesting and exposes `to_dict()`:
- `metadata`: `order_id`, `perm_id`, `client_id`, `parent_id`, `submitted_at`,
  `contract{symbol, sec_type, exchange, currency, con_id}`, `action`, `order_type`,
  `total_qty`, `limit_price`, `tif`, `market_price_at_submit`, `strategy_tag`, and the
  nested `rationale` block (the ForecastView fields, keyed exactly as dummy_orders.json:
  `expected_return_h`, `q_low_h`, `q_high_h`, `previous_weight`, `target_weight`,
  `capped_weight`, `recommended_weight`, `weight_change`, `min_turnover_weight`,
  `previous_value`, `target_value`, `reference_price`, `signed_qty`).
- `state`: `as_of`, `status`, `qty_filled`, `qty_outstanding`, `lifecycle`,
  `realised_cum`, `unrealised`.
- `history[]`: append-only event log. In sim: one `Submitted` then one `Filled` event at
  the same ts, the `Filled` event carrying the single fill dict (`exec_id`, `ts`, `qty`,
  `price`, `commission=0`, `role` = OPEN/CLOSE, `realised_total`). 100% immediate fill.

**Where orders are minted.** `SimExecutionBackend.execute` is the single choke point for
both the manual-click sim path (`place_trade`) and the replay builder (`_build_replay`), so
the `Order` is constructed THERE: capture `sim_position`/`sim_realised` before `apply_fill`,
funnel the `Fill` through `apply_fill` (still the sole P&L mutator), then build the `Order`
from the post-fill book + the `ctx` ForecastView and register it in `app.orders[oid]`.

**Weight naming (post-rename) + the turnover threshold.** `ForecastView` now distinguishes
four weights cleanly: `w_target` = the RAW α·r̂ target BEFORE any cap/threshold (→ rationale
`target_weight`); `w_capped` = the target AFTER risk caps but BEFORE the turnover gate (→
rationale `capped_weight`); `w_rec` = the RECOMMENDED weight AFTER risk caps AND the
min-turnover gate (→ rationale `recommended_weight`, and what `qty`/`value_delta`/`w_delta`/
the panel are sized from); `w_current` = the held weight. The **min-turnover threshold**
(`REC_MIN_TURNOVER`, default 1%) is a real gate in `_compute_recommendation` (§3a/§3c): if
`|w_capped - w_current| < threshold` the recommendation suppresses the trade (`w_rec =
w_current` → zero trade) so noise can't churn the book. The gate does NOT tag `binding_cap`:
that field reports RISK caps only (`none`/`idio_floor`/`factor`/`portfolio`), and turnover
suppression is read off `w_capped` vs `w_threshold` instead — the panel shows the transition
`w_current→w_capped`, the change `Δ = w_capped−w_current` with a `≥/< min` operator, then the
cap, so a zero trade beside `cap none` + `< min` reads as a turnover suppression. The applied
threshold rides on `ForecastView.w_threshold` → rationale `min_turnover_weight` →
`state_df.weight_threshold`. `account_value` also rides on the view (→
`state_df.portfolio_value`) so nothing recomputes.

**`state_df` new columns** (on top of the existing position/P&L set), all populated in
`_record_state_row` from `self._last_forecast_view`:
`central_price`, `mu_now`, `reference_price`, `direction`, `signed_qty`, `previous_weight`,
`target_weight`, `capped_weight`, `recommended_weight`, `weight_change`, `weight_threshold`,
`previous_value`, `target_value`, `value_delta`, `binding_cap`, `portfolio_value`,
`portfolio_pnl`.
(`r_hat`/`q_low`/`q_high`
were already present.) `portfolio_value` = equity (cash + realised + unrealised);
`portfolio_pnl` = realised + unrealised — both let the user check position value/P&L
against the whole book and confirm the weights look normal at any scrubbed bar.

**Serialization.** `export_orders_json()` dumps `{str(oid): order.to_dict()}` →
`orders/replay_orders.json` (dummy_orders.json shape); `export_state_json()` dumps
`state_df` → `orders/replay_state.json` (one record per bar). Both fire at the end of
`_build_replay` and from an **Export JSON** button in the replay band. `_reset_accounting`
clears `self.orders` alongside the ledger so a fresh run starts clean.

---

## Build order

0. **[DONE]** Risk/return + recommendation panel (§7.1): `ForecastView`,
   `_forecast_quantiles` (Gaussian), `_compute_recommendation`, `_update_recommendation_panel`.
   Pure display over the *existing* model — no new plots, no accounting yet.
1. **[TODO]** Drifting-mean OU with linear trend (1b Phase 1) + return readout (1c).
   (Return readout E[r] is in the panel; the drifting `mu_t` itself is not — `mu` still scalar.)
2. **[DONE]** Quantile bands via Gaussian closed-form (1d option 1) — replaces the ±1σ cone (§7.2).
3. **[PARTIAL]** Wire the panel from step 0 to the drifting-mean + quantile outputs and to
   auto-trade dispatch (idiosyncratic floor only); the recommended order shown == what
   auto-trade sends. (Panel is wired to quantiles; auto-trade still fires on band breach,
   NOT on the recommendation's `w_delta`/min-turnover gate.)
4. **[PARTIAL]** Stacked-axes chart refactor (§7.2) + accounting engine (`Fill`/`apply_fill`,
   §7.3) + replay loader + `state_df` + P&L decomposition plot (§4b/§4c). (Axes, accounting,
   `state_df`, P&L plot DONE. **Replay loader NOT built — see GAP B.**)
5. **[DONE]** Replay plots 2 + 3 (position breakdown, entry vs market with long/short shading, §7.2).
6. **[TODO]** Live order reconciliation against IBKR (§7.3): `_reconcile_orders` on the timer, P&L
   match badge via `get_account_updates`/`get_pnl_single_data`; plus the small library
   additions (`execDetailsEnd` + `get_executions_data`) if cross-session fills are needed.
   (Depends on GAP A — the order ledger this reconciles against does not exist yet.)
7. **[DONE]** Chart UX (§5). **Historical-replay scrubber** (§7.4: precompute-once drag
   slider — `_build_replay` / `_replay_to` / `replay_slider`, shared `_ou_signal_side`,
   `REPLAY_CANDLE_LIMIT` fast path). **Navigation toolbar** (`NavigationToolbar2Tk`, built in
   `_mount_canvas`: box-zoom / pan / home, axes auto-rescale). **Chart UI overhaul** (§A–E):
   dynamic axis layout with independent per-axis toggles (P&L / Units / Entry, `_build_axes`),
   per-series toggles for every line (split `unfilled`/`closed`/`last`), resizable pop-out
   window (`_mount_canvas` / `_toggle_popout` — one figure, canvas re-mounted), and per-axis
   hover tooltips showing only enabled series, clamped inside the figure (`_on_hover` +
   `_hover_text_price` / `_hover_text_analytics`, `_make_hover_annot`).
   **Chart UX round 2:** (a) **1-based bar numbering everywhere** — x-axis tick labels
   shifted +1 via a `FuncFormatter` in `_style_ax` (data stays 0-based), hover reads
   "bar i+1", matching the scrubber. (b) **Jump-to-bar** box (`replay_jump_var` /
   `_jump_to_bar`) scrubs to an exact 1-based bar. (c) **Price-pane per-series toggles**
   (close / kalman hist / live mean / μ / markers / rationale) gated in `_draw_price`.
   (d) **Trade markers** on the price plot (`_draw_trade_markers`: ▲ buy / ▼ sell, solid
   blue, from `self.orders`) with a per-marker hover (`_trade_marker_at` / `_hover_text_trade`:
   BOUGHT/SOLD coloured + bold, size @ price, then the toggleable recommendation rationale).
   (e) **`next_intended`** line in the Units pane (`target_value / reference_price` = the
   position the recommendation would hold after its trade, vs `intended`/`filled`).
   **Risk-cap controls:** user-editable **Min turnover %** (default 0.25%) and **Max weight %**
   (default 3%) entries + on/off checkboxes in the Trading & Portfolio panel
   (`_min_turnover_frac` / `_max_weight_frac`, read live by `_compute_recommendation`);
   sim/replay portfolio value now bases on today's IBKR NetLiq (`_sim_base_equity`).
8. **[TODO]** Externally-supplied trend (1b Phase 2).
9. **[TODO]** Cyclical components (1b Phase 3) + bootstrap quantiles (1d option 2).
10. **[TODO]** Portfolio-level risk pipeline (section 2 stages 2–4) + Kelly sizing.
11. **Plotly track** (matplotlib stays the live engine; Plotly added incrementally):
    - **Step 1 — [DONE]** Render-once replay export (`_export_replay_plotly`, "⧉ Export
      Plotly" button). ADDITIVE — does not touch the in-app matplotlib replay/scrubber.
      Reads the per-bar `state_df` (prefers `_replay_state_df`), builds a `make_subplots`
      figure (price candlesticks + P&L + Units + Entry, shared x, bottom range slider,
      `Scattergl` for WebGL-sharp deep zoom, `hovermode="x unified"`), `write_html` to
      `orders/replay_chart.html`, opens in the browser. Native zoom / pan / legend-toggle /
      hover for free. Lazy `import plotly` so the app runs without it (`pip install plotly`).
      Feeds the eventual static GitHub Pages view (§6 step 1).
    - **Step 2 — [FUTURE]** Live chart in Plotly via webview. Embed a webview widget
      (pywebview / tkinterweb) in the Tk window, port the `_draw_*` panes to Plotly traces,
      and stream per-tick updates with `Plotly.react`/`extendData` over a Python↔JS bridge.
      The cost is the per-tick streaming bridge, not Plotly itself (render-once is trivial,
      see step 1). Only worth it if interactive zoom on the LIVE chart matters beyond what
      the matplotlib nav toolbar already gives.
    - **Step 3 — [FUTURE]** Full Dash/browser GUI. Replace Tkinter entirely with a Plotly
      Dash app; everything Plotly-native (zoom / range-slider / legend / hover built in).
      Largest rewrite — all controls, threading, and IBKR wiring re-architected for a
      callback/browser model. Reserve for if the desktop app outgrows Tk.

> Gaps called out at the top (Status section): **GAP A** — order ledger distinct from fills
> (Order dataclass in dummy_orders.json shape, `self.orders`, enriched `state_df`, JSON
> exports; sim fills 100% immediately but the lifecycle plumbing exists): **now built**
> (§7.5). **GAP B** — the historical-replay scrubber/slider: **now built** (§7.4).

Each step keeps the existing UI runnable; new controls additive, old behaviour preserved behind defaults.
