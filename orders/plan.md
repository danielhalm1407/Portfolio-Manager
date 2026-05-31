# KTS Extension Plan — Drifting-Mean OU, Quantile Forecasts, Risk-Aware Trade Recommendations

Scope: extend `orders/kts.py`. Three layers — (1) richer forecasting model, (2) uncertainty as first-class citizen, (3) trade recommendations grounded in displayed risk/return.

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
| `trades` | list/dict of fills at this bar (qty, price, side) |

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

## Build order

1. Drifting-mean OU with linear trend (1b Phase 1) + return readout (1c).
2. Quantile bands via Gaussian closed-form (1d option 1).
3. Recommendation panel showing `r_hat`, `q_low`, `q_high`, suggested `w_target`, with idiosyncratic floor only.
4. Replay loader + state dataframe + P&L decomposition plot (§4).
5. Replay plots 2 + 3 (position breakdown, entry vs market with long/short shading).
6. Chart UX: navigation toolbar, date range selector (§5).
7. Externally-supplied trend (1b Phase 2).
8. Cyclical components (1b Phase 3) + bootstrap quantiles (1d option 2).
9. Portfolio-level risk pipeline (section 2 stages 2–4) + Kelly sizing.
10. (Parallel track) Static replay export → Plotly.js on GitHub Pages (§6 step 1).

Each step keeps the existing UI runnable; new controls additive, old behaviour preserved behind defaults.
