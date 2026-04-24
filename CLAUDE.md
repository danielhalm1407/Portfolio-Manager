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

## Key approach flags
- Before editing any file, read it first. Before modifying a  │
│ function, grep for all callers. Research before you edit.
- Please do not re-read the same files that you have already read
- Focus primarily only on the most relevant files and within those files, the parts of the files (often lines of code) that are most relevant. You do not need to explore the whole directory.
  - This might involve only editing a specific method within a given py file or class that I wish for you to edit. E.g., In <file> lines <start>-<end>, look at the <function>       │
│ function.  

## Agent roles
- **Research agent** — theme extraction from market commentary
- **Modelling agent** — structural fundamental analysis
- **Allocator agent** — portfolio construction and risk
- **Reporter agent** — output generation and briefings

## Python
- Package: `portutils` (installed via `pip install -e .`)
- Python >=3.11
- Config loading centralised in `src/portutils/utils/config.py`
