# Portfolio Manager

A thematic-fundamental research engine that turns qualitative market narratives into portfolio
tilts, feeds those tilts into a disciplined allocator, and **executes the result against a real
Interactive Brokers account**.

Two halves, which is the thing to understand first:

| | the research half | the execution half |
|---|---|---|
| question | *what should I hold?* | *how does that become a real position?* |
| state | scaffolded; pipelines exist, briefs not yet written | **built and live-tested** |
| start here | [`research/`](research/README.md) | [**EXECUTION_STACK.md**](EXECUTION_STACK.md) |

---

## Where do I look for…

| I want to know… | read |
|---|---|
| how a target weight becomes a real order at IBKR | [EXECUTION_STACK.md](EXECUTION_STACK.md) |
| what `REJECTED` / `PENDING_OPEN` / `NO_ANSWER` mean on an order | [EXECUTION_STACK.md](EXECUTION_STACK.md#reading-the-answer-the-verdict-vocabulary) |
| how to run the live rebalancer, and what stops it trading by accident | [src/pipelines/README.md](src/pipelines/README.md) |
| how to debug a rebalance cell by cell | [orders/rebalance_live_debug.md](orders/rebalance_live_debug.md) |
| how P&L, average cost and positions are accounted | [src/portutils/portfolio/README.md](src/portutils/portfolio/README.md) |
| everything about talking to TWS | [src/portutils/ingestion/README.md](src/portutils/ingestion/README.md) |
| what went wrong in live trading and what it taught | [orders/live_trading_notes.md](orders/live_trading_notes.md) |
| how a market narrative becomes a weight | [research/README.md](research/README.md) |
| what the tests pin, and why one of them fails on purpose | [tests/README.md](tests/README.md) |

---

## What the project aims to do

Build a robust portfolio starting from the themes raised in financial market commentary, then build
conviction within the gas/power/electrification value chain on a deeper structural modelling layer.

**Core:** a thematic-fundamental research engine converting qualitative narratives into portfolio
tilts, fed into a disciplined allocator.
**Extension:** a structural commodity sleeve around gas/power/value-chain exposure rather than crude
alone.

### The Claude Code infrastructure objective

A second goal runs alongside the portfolio one: use this project to build out an effective Claude
Code setup, in five layers.

1. **Context & memory** — hierarchical `CLAUDE.md` files, persistent cross-session memory, a file
   structure that can be navigated reliably.
2. **Reusable workflows** — slash commands (`/run-theme-scan`, `/build-conviction-brief`) and skills
   for richer multi-step tasks.
3. **Automation** — hooks tied to tool calls, scheduled agents for daily ingestion and weekly review.
4. **External connectivity** — MCP servers (financial data, web, Gmail/Calendar) and APIs (yfinance,
   FRED, EIA).
5. **Agent architecture** — specialised subagents: **Research** (theme extraction), **Modelling**
   (structural fundamentals), **Allocator** (construction and risk), **Reporter** (briefings).

---

## Setup

```bash
pip install -e .                # installs `portutils` and dependencies
playwright install chromium     # once per machine, for the Reuters scraper
```

Python ≥3.11. The browser binary lives in a machine-level cache
(`%LOCALAPPDATA%\ms-playwright` on Windows), shared across environments.

Run tests with the environment that has `portutils` + `pytest`:

```bash
~/miniconda3/envs/venv-stats/python.exe -m pytest tests/ -q
```

---

## Layout

```
Portfolio-Manager/
├── EXECUTION_STACK.md   how a weight becomes a filled order — read this first
├── CLAUDE.md            project conventions (instructions to Claude, not docs)
│
├── config/              settings.yaml, asset_universe.yaml — weights, guards, universe
├── src/
│   ├── portutils/       the installable library (no side effects on import)
│   │   ├── ingestion/   all TWS plumbing + the Reuters scraper
│   │   ├── portfolio/   Book, rules, simulator, broker bridge
│   │   ├── analysis/    returns, performance, attribution, factors
│   │   ├── utils/       the single config loader
│   │   └── viz/         Plotly/Dash helpers, one colour theme
│   └── pipelines/       ten runnable scripts, two of which trade
├── orders/              interactive harnesses and the Kalman trading GUI — never imported
├── research/            themes, conviction write-ups, ad-hoc studies
├── models/              thematic / structural / allocator logic (planned)
├── app/                 Dash dashboard, deployed via Render
├── data/                raw (append-only) → processed → reference
├── outputs/             live order audit trails, portfolio state, reports, scenarios
├── notebooks/           exploration (messy) and reports (reproducible)
├── tests/               126 offline tests — no TWS connection needed
└── .claude/             agents, commands, skills, hooks, rules, plans
```

---

## The areas

### Execution stack → [EXECUTION_STACK.md](EXECUTION_STACK.md)

The path from `config/settings.yaml` to a fill and back to an audit trail, spanning four folders.
Covers the three failures that shaped it (a ticker is not an instrument; nothing IBKR returns is in
one currency; an order is async with errors that lie), the order-verdict vocabulary, and the five
independent safety gates. **If you are about to place an order, read this.**

### `src/` → [src/README.md](src/README.md)

Library and scripts, split by one enforced rule: importing anything in `portutils/` must be safe.
That rule exists because a cell script with module-level side effects transmits real orders when
someone imports it.

#### `src/portutils/` → [portutils/README.md](src/portutils/README.md)

- **[`ingestion/`](src/portutils/ingestion/README.md)** — the only place that speaks to TWS:
  connection lifecycle, the single `IBApp(EWrapper, EClient)`, pure contract/order builders, request
  helpers, `OrderApp`. Also the Playwright Reuters scraper.
- **[`portfolio/`](src/portutils/portfolio/README.md)** — `Book` (average-cost accounting, realised
  and unrealised P&L), the rebalance rules including `ConstantMixRule`, the simulator, the backcast,
  and `ibkr_sync` as the sole broker→book bridge.
- **[`analysis/`](src/portutils/analysis/README.md)** — returns, performance summaries, return
  attribution, PCA/factor decomposition.
- **[`utils/`](src/portutils/utils/README.md)** — the single config loader. Nothing else reads `.env`.
- **[`viz/`](src/portutils/viz/README.md)** — one colour per ticker across notebooks, app and exports.

#### `src/pipelines/` → [pipelines/README.md](src/pipelines/README.md)

Ten runnable scripts. `rebalance_live.py` places orders (dry run unless `--live`);
`cancel_orders.py` is the undo; `log_positions.py` works around TWS's ~7-day history ceiling; the
rest ingest, cache or simulate.

### `orders/` → [orders/README.md](orders/README.md)

Interactive harnesses and GUIs, all human-driven, **none importable**. The cell-by-cell rebalance
harness lives here, as does `kts.py`, a Kalman/OU trading GUI that now consumes the library rather
than duplicating it.

### `research/` → [research/README.md](research/README.md)

Commentary → theme brief → conviction write-up → target weights. Format rules (every brief carries
source, tilt, confidence and horizon) and the ad-hoc studies that have been run so far. Honest note:
`themes/` is currently empty.

### `models/`

Intended home for executable modelling logic — `thematic/` (narrative→tilt scoring), `structural/`
(gas/power value chain), `allocator/` (construction and risk). **Currently holds only its
[`CLAUDE.md`](models/CLAUDE.md); no code yet.** The conventions there specify input/output shapes and
units for when it is filled.

### `app/`

A Dash multi-page dashboard: [`main.py`](app/main.py) exposes `server` for gunicorn (see
`Procfile`), pages in `app/pages/` (portfolio, themes, conviction), shared chart functions in
`app/components/charts.py`, CSS auto-served from `app/assets/`. Run locally with `python app/main.py`
→ http://localhost:8050. It consumes [`portutils/viz`](src/portutils/viz/README.md) rather than
defining its own styling.

### `data/`, `outputs/`, `config/`

`data/raw/` is **append-only and gitignored** — transformations happen in code and land in
`data/processed/` (parquet price panels), with static lookups in `data/reference/`. `outputs/` holds
decisions rather than workings: `live/` (one CSV per rebalance run, dry runs included), `portfolio/`,
`reports/`, `scenarios/`. `config/` holds the non-secret knobs — `settings.yaml` (guards, account,
model params) and `asset_universe.yaml` (universe, roles, target weights); secrets live in `.env`,
never committed.

### `notebooks/`

`exploration/` is for thinking and may be messy; `reports/` must be reproducible — restart the
kernel and run all before saving. Anything that proves useful gets extracted into `portutils/`
rather than left in a notebook. See [`notebooks/CLAUDE.md`](notebooks/CLAUDE.md).

### `tests/` → [tests/README.md](tests/README.md)

126 tests, all offline. Includes three source-level assertions that keep the cell scripts disarmed
and unimportable — and one test that fails **by design** while the debug harness is armed.

### `.claude/`

Agents, slash commands, skills, hooks, glob-scoped rules, and `plans/` — one living plan document per
piece of work, each with a progress log and a decisions table. `settings.json` carries permissions
and hooks; `settings.local.json` is machine-local and gitignored.

---

## Conventions worth knowing before editing

Full detail in [`CLAUDE.md`](CLAUDE.md) and the module-level `CLAUDE.md` files.

- **Comments are retained.** Move them with their code; update rather than delete. The dense
  rationale comments in `ibkr_requests.py` and `kts.py` are the house style, not clutter.
- **`data/raw/` is append-only.** Never edit it by hand.
- **Secrets in `.env`**, non-secret config in `config/`, both read only through
  [`portutils/utils/config.py`](src/portutils/utils/README.md).
- **Plans live in `.claude/plans/`** in this repo, as living documents.
- `research/` is narrative; `models/` is executable; `notebooks/` is for thinking; `outputs/` is for
  decisions.

---

## Appendix

### `.claude/settings.json` — permissions and hooks

The active Claude Code configuration for this project; without it, no permissions or hooks are in
effect.

**Permissions** control what may run via `Bash` or MCP. The `allow` list whitelists safe commands
(pytest, pip, ruff, `mcp__playwright__browser_navigate` for the Reuters scraper); the `deny` list
blocks destructive or sensitive ones (`rm -rf`, `cat .env`, force push).

**Hooks** fire on tool use. A `PostToolUse` hook matching `Edit|Write` runs `post-edit-lint.sh`,
applying `ruff` to any Python file that was edited, so style stays consistent without a manual step.
A second hook mirrors plan files written under `~/.claude/plans/` into `.claude/plans/` with their
links rewritten to relative paths.

`settings.local.json` holds machine-specific overrides and is gitignored — use it for anything that
differs between machines.

### Dash dashboard and public deployment

[`app/`](app/) is a Dash (Plotly) application following the multi-page pattern:
[`app/main.py`](app/main.py) is the entry point and exposes `server` for gunicorn, pages live in
`app/pages/`, reusable chart functions in `app/components/charts.py`, and Dash auto-serves
`app/assets/`.

```bash
pip install -e .
python app/main.py     # http://localhost:8050
```

Deployment is via Render, using the `Procfile` start command `gunicorn app.main:server`.
