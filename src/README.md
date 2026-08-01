# `src/` — the library and the scripts that use it

Two directories, one rule.

| | [`portutils/`](portutils/) | [`pipelines/`](pipelines/) |
|---|---|---|
| what | installable package — **library code only** | runnable workflow scripts |
| side effects on import | **none** | expected (API calls, file writes) |
| how it runs | imported | `python src/pipelines/<name>.py` |
| docs | [portutils/README.md](portutils/README.md) | [pipelines/README.md](pipelines/README.md) |

## Why the split is enforced rather than encouraged

A cell script with module-level side effects transmits real orders **when someone imports it**.
`orders/rebalance_port_basic.py` ends its module level with a `dry_run=False` rebalance and a stray
market order; anything importing a helper from it would have placed those orders before running a
single one of its own safety checks. That is why `submit_rebalance_orders` lives in
`portutils/ingestion/ibkr_requests.py` and not where it was first written, and why
`tests/test_rebalance_live.py` asserts that nothing under `src/` imports the cell scripts.

So: **importing anything in `portutils/` must be safe, always.** A pipeline's own entry point sits
behind `if __name__ == "__main__":` for the same reason — `orders/rebalance_live_debug.py` imports
`rebalance_live` to reuse its arithmetic, and that import must not connect to a broker.

## Installation

```bash
pip install -e .        # exposes `portutils`; pipelines are run by path
```

Python ≥3.11. The environment that has `portutils` plus `pytest` is `~/miniconda3/envs/venv-stats`.

## Conventions

Full list in [`CLAUDE.md`](CLAUDE.md). The ones that bite:

- **Never call `os.getenv()` directly.** Secrets live in `.env` (gitignored), loaded once by
  `load_dotenv()` inside [`portutils/utils/config.py`](portutils/utils/config.py), and read through
  that module everywhere else.
- Every new module in `portutils/` needs an `__init__.py`.
- Functions in `portutils/` should be pure where they can be; the impure edges are the ingestion
  helpers that talk to a socket, and they say so.
