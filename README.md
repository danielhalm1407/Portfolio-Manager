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
├── pyproject.toml                         # Package definition, dependencies
├── .env                                   # Secrets and environment-specific config — never committed *
├── .gitignore                             # Excludes: .env, data/raw/, __pycache__, etc.
├── README.md
│
├── .claude/                               # Claude Code config for this project
│   ├── commands/                          # Slash commands (stored prompts)
│   │   ├── run-theme-scan.md
│   │   ├── build-conviction-brief.md
│   │   └── weekly-portfolio-review.md
│   ├── skills/                            # Skill instruction files
│   │   ├── score-narrative.md
│   │   └── model-value-chain.md
│   └── hooks/                             # Event-driven scripts (pre/post tool calls)
│       └── post-edit-lint.sh
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
│   │   │   └── __init__.py
│   │   ├── analysis/                      # Core analytical functions
│   │   │   └── __init__.py
│   │   ├── portfolio/                     # Allocation, rebalancing, risk functions
│   │   │   └── __init__.py
│   │   └── utils/                         # Shared helpers: logging, formatting
│   │       ├── __init__.py
│   │       └── config.py                  # Single loader for .env + settings.yaml
│   └── pipelines/                         # Runnable scripts — orchestrate workflows, have side effects
│       ├── daily_ingest.py
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

### * `.env` — what it is and why it exists

`.env` is a plain text file of `KEY=VALUE` pairs that sits at the project root. It is the designated home for anything secret or environment-specific: API keys, credentials, and any config values that differ between machines or that should never appear in source control.

It is not a virtual environment. The project's Python isolation (its own packages, independent of your system Python) is handled separately by a virtual environment (`.venv/`), which is also gitignored but for a different reason — it is large and machine-specific, not sensitive.

**How it works:** the `python-dotenv` library reads `.env` at runtime when a script is executed and injects the key-value pairs into `os.environ` for that process. This is triggered by a single `load_dotenv()` call in `src/portutils/utils/config.py`, which all pipelines and modules import from — so credentials are centralised and `load_dotenv()` is only ever called once.

**Why credentials live here and not in `config/`:** `config/settings.yaml` is committed to git and holds non-sensitive project config (model parameters, thresholds, asset universe). `.env` is gitignored and holds everything that must not be exposed. The line between them is: if it is a secret, it goes in `.env`; if it is safe to share, it goes in `config/`.

**`.env.example`:** a companion file that is committed to git with all the same keys but dummy values. It serves as a setup template so anyone (or a fresh machine) knows exactly which environment variables need to be populated before running the project.