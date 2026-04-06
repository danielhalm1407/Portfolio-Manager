# What this project aims to do

Build a robust portfolio starting with analysis of the themes pointed out in financial market commentary, and then buidling conviction within the gas/power/electrification value chain theme on a deeper structural modelling layer.

## Core project

Build a thematic-fundamental research engine that converts qualitative market narratives into portfolio tilts, then feeds those tilts into a disciplined allocator.

## Extension later

A structural commodity sleeve, probably around gas/power/value-chain exposure rather than crude alone.

## Additional Objectives:

Leverage Claude Code with an effective infrastructure, organised into five layers:

### 1. Context & Memory
*How Claude understands the project across sessions*
- Hierarchical `CLAUDE.md` files (project-level, module-level)
- Persistent memory system (cross-session facts, preferences, project state)
- Well-organised file structure (so Claude navigates it reliably)

### 2. Reusable Workflows
*Stored, repeatable task patterns*
- Slash commands — stored prompts (e.g., `/run-theme-scan`, `/build-conviction-brief`)
- Skills — richer instruction sets for complex multi-step tasks (e.g., model a value chain, score a narrative)

### 3. Automation & Event-Driven Execution
*Proactive and reactive triggers*
- Hooks — event-driven scripts tied to Claude tool calls (pre/post)
- Scheduled agents — cron-based triggers (e.g., daily commentary ingestion, weekly portfolio review)

### 4. External Connectivity
*What Claude can pull from and push to*
- MCP servers — financial data feeds, web search, Gmail/Calendar
- APIs — market data (yfinance, FRED, EIA for gas/power), news/commentary sources

### 5. Agent Architecture
*How work is decomposed and delegated*
- Subagents — specialised agents: **Research** (theme extraction), **Modelling** (structural fundamentals), **Allocator** (portfolio construction), **Reporter** (output generation)
- Shared state/handoff protocols between agents

## Project Directory Structure

```
Portfolio-Manager/
│
├── CLAUDE.md                              # Top-level: project overview, conventions, agent roles
├── pyproject.toml                         # Package definition, dependencies (dash, plotly, gunicorn included)
├── Procfile                               # Deployment start command for Render: gunicorn app.main:server
├── .env                                   # Secrets and environment-specific config — never committed *
├── .gitignore                             # Excludes: .env, data/raw/, settings.local.json, __pycache__, etc.
├── README.md
│
├── .mcp.json                              # MCP server declarations (Gmail, Google Calendar)
│
├── .claude/                               # Claude Code config for this project
│   ├── settings.json                      # Permissions (allow/deny) + hooks — active Claude Code config **
│   ├── settings.local.json                # Machine-local overrides — never committed
│   │
│   ├── agents/                            # Subagent definitions — autonomous specialised workers
│   │   ├── research-agent.md              # Theme extraction from commentary
│   │   ├── modelling-agent.md             # Structural fundamental analysis
│   │   ├── allocator-agent.md             # Portfolio construction and risk
│   │   ├── reporter-agent.md              # Output generation and briefings
│   │   └── workflows/                     # Multi-agent orchestration definitions
│   │
│   ├── agent-memory/                      # Persistent cross-session facts for agents
│   │
│   ├── commands/                          # User-triggered /slash commands
│   │   ├── run-theme-scan.md
│   │   └── workflows/                     # Multi-step workflow commands
│   │       ├── build-conviction-brief.md
│   │       └── weekly-portfolio-review.md
│   │
│   ├── skills/                            # Reusable instruction modules loaded into agents
│   │   ├── score-narrative.md
│   │   ├── model-value-chain.md
│   │   ├── fetch-reuters-section.md       # Playwright-based Reuters headline scraper (interactive)
│   │   └── presentation/                  # Presentation/reporting skill modules
│   │
│   ├── hooks/                             # Lifecycle event scripts wired in settings.json
│   │   ├── scripts/
│   │   │   └── post-edit-lint.sh          # Runs ruff on any .py file Claude edits
│   │   ├── config/                        # Hook configuration files
│   │   └── sounds/                        # Audio alert assets
│   │
│   └── rules/                             # Glob-scoped instructions auto-applied by file context
│       ├── python.md                      # Applied when **/*.py files are in context
│       ├── research.md                    # Applied when research/** files are in context
│       └── notebooks.md                   # Applied when notebooks/**/*.ipynb are in context
│
├── app/                                   # Dash dashboard — served publicly via Render
│   ├── main.py                            # Entry point; exposes `server` for gunicorn
│   ├── pages/                             # One file per page (Dash multi-page pattern)
│   │   ├── portfolio.py                   # Weights, tilts, current positions
│   │   ├── themes.py                      # Scored theme briefs
│   │   └── conviction.py                  # Deep-dive per name/sector
│   ├── components/                        # Reusable Plotly chart functions
│   │   └── charts.py
│   └── assets/                            # CSS/images — Dash auto-serves this folder
│       └── style.css
│
├── config/
│   ├── settings.yaml                      # Non-secret config: model params, thresholds
│   └── asset_universe.yaml                # Investable universe definition
│
├── src/
│   ├── CLAUDE.md                          # Coding standards, how to add modules
│   ├── portutils/                         # Installable package — library code only, no side effects
│   │   ├── __init__.py
│   │   ├── ingestion/                     # Data fetch functions (APIs, scrapers)
│   │   │   ├── __init__.py
│   │   │   └── reuters.py                 # Playwright-based Reuters scraper (headline + full article)
│   │   ├── analysis/                      # Core analytical functions
│   │   │   └── __init__.py
│   │   ├── portfolio/                     # Allocation, rebalancing, risk functions
│   │   │   └── __init__.py
│   │   └── utils/                         # Shared helpers: logging, formatting
│   │       ├── __init__.py
│   │       └── config.py                  # Single loader for .env + settings.yaml
│   └── pipelines/                         # Runnable scripts — orchestrate workflows, have side effects
│       ├── daily_ingest.py
│       ├── scrape_commentary.py           # Scrape Reuters headlines/articles → data/raw/commentary/
│       ├── theme_scan.py
│       └── weekly_review.py
│
├── models/
│   ├── CLAUDE.md                          # Modelling conventions, input/output specs, units
│   ├── thematic/                          # Narrative-to-tilt scoring logic
│   ├── structural/                        # Gas/power/value-chain structural models
│   └── allocator/                         # Portfolio construction & risk logic
│
├── research/
│   ├── CLAUDE.md                          # Research format/template, how to score a theme
│   ├── themes/                            # Theme briefs — qualitative narrative → scored tilt
│   ├── conviction/                        # Deeper fundamental write-ups per name/sector
│   └── archive/                           # Dated snapshots of past research
│
├── data/
│   ├── raw/                               # Unprocessed inputs — never manually edited
│   │   ├── commentary/                    # Ingested market commentary (text/pdf)
│   │   └── market/                        # Price/vol/fundamentals feeds (csv, json)
│   ├── processed/                         # Cleaned, structured outputs from ingestion
│   └── reference/                         # Static reference data (sector maps, asset universe)
│
├── notebooks/
│   ├── CLAUDE.md                          # What belongs here vs outputs/, naming conventions
│   ├── exploration/                       # One-off analysis, ideation — messy is fine
│   └── reports/                           # Polished output notebooks for review
│
├── outputs/
│   ├── portfolio/                         # Current positions, weights, tilts
│   └── reports/                           # Generated research reports, briefings
│
└── tests/                                 # Unit + integration tests for portutils/ and models/
```

---

## Appendix

### ** `.claude/settings.json` — Claude Code permissions and hooks

`settings.json` is the active configuration file for Claude Code within this project. Without it, no permissions or hooks are in effect.

**Permissions** control what Claude is allowed to run via `Bash` or MCP. The `allow` list whitelists safe commands (pytest, pip, ruff, and `mcp__playwright__browser_navigate` for Reuters scraping); the `deny` list blocks destructive or sensitive ones (rm -rf, cat .env, force push).

**Hooks** fire automatically when Claude uses a tool. The `PostToolUse` hook with matcher `Edit|Write` runs `post-edit-lint.sh` after every file edit, which applies `ruff` linting to any modified Python file. This keeps code style consistent without requiring a manual step.

`settings.local.json` holds machine-specific overrides and is gitignored — use it for anything that differs between your machines (e.g. allowing `open *` on macOS).

Note: `.claude/skills/` is not a native Claude Code directory. Those files have been moved into `.claude/commands/` alongside slash commands — both are plain `.md` files invoked the same way.

---

### *** Dash dashboard and public deployment

The `app/` directory contains a Dash (Plotly) web application that surfaces portfolio outputs publicly. It follows Dash's multi-page pattern: `app/main.py` is the entry point; individual pages live in `app/pages/`; reusable chart functions live in `app/components/`.

**Running locally:**
```bash
pip install -e .
python app/main.py
# open http://localhost:8050
```

**Deploying to Render (free tier):**
1. Push this repo to GitHub
2. Go to render.com → New Web Service → connect your repo
3. Set build command: `pip install -e .`
4. Set start command: `gunicorn app.main:server`
5. Render gives you a public URL (`yourapp.onrender.com`) and auto-redeploys on every push to `master`

The `Procfile` at the project root contains the start command for Render. The critical line in `app/main.py` is `server = app.server` — this exposes the underlying Flask server that gunicorn wraps for production.

The free tier spins down after 15 minutes of inactivity; the next visit triggers a ~30 second cold start.

---

### * `.env` — what it is and why it exists

`.env` is a plain text file of `KEY=VALUE` pairs that sits at the project root. It is the designated home for anything secret or environment-specific: API keys, credentials, and any config values that differ between machines or that should never appear in source control.

It is not a virtual environment. The project's Python isolation (its own packages, independent of your system Python) is handled separately by a virtual environment (`.venv/`), which is also gitignored but for a different reason — it is large and machine-specific, not sensitive.

**How it works:** the `python-dotenv` library reads `.env` at runtime when a script is executed and injects the key-value pairs into `os.environ` for that process. This is triggered by a single `load_dotenv()` call in `src/portutils/utils/config.py`, which all pipelines and modules import from — so credentials are centralised and `load_dotenv()` is only ever called once.

**Why credentials live here and not in `config/`:** `config/settings.yaml` is committed to git and holds non-sensitive project config (model parameters, thresholds, asset universe). `.env` is gitignored and holds everything that must not be exposed. The line between them is: if it is a secret, it goes in `.env`; if it is safe to share, it goes in `config/`.

**`.env.example`:** a companion file that is committed to git with all the same keys but dummy values. It serves as a setup template so anyone (or a fresh machine) knows exactly which environment variables need to be populated before running the project.