# Portfolio Manager — Claude Instructions

## Project overview
A thematic-fundamental research engine that converts qualitative market narratives into portfolio tilts, then feeds those tilts into a disciplined allocator. Extension: structural commodity sleeve focused on gas/power/electrification value chain.

## Key conventions
- Library code (importable, no side effects) lives in `src/portutils/`
- Runnable workflow scripts live in `src/pipelines/`
- Secrets go in `.env` (never committed), non-secret config in `config/`
- `data/raw/` is append-only — transformations happen in code and land in `data/processed/`
- `research/` is human-readable narrative; `models/` is executable logic
- `notebooks/` are for thinking; `outputs/` are for decisions

## Agent roles
- **Research agent** — theme extraction from market commentary
- **Modelling agent** — structural fundamental analysis
- **Allocator agent** — portfolio construction and risk
- **Reporter agent** — output generation and briefings

## Python
- Package: `portutils` (installed via `pip install -e .`)
- Python >=3.11
- Config loading centralised in `src/portutils/utils/config.py`
