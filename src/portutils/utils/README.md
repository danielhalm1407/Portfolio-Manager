# `utils/` — the single config loader

One module, one job: [`config.py`](config.py) is the **only** place this project reads configuration
or secrets.

```python
from portutils.utils import config as cfg

weights = cfg.portfolio_weights("core")      # from config/asset_universe.yaml
meta    = cfg.asset_meta("BARC")
role    = cfg.asset_role("BARC")
tickers = cfg.tickers_by_role("hedge")
```

## The rule

**Never call `os.getenv()` outside this module, and never hardcode a credential.** `config.py` calls
`load_dotenv()` once and everything else imports from here. Secrets live in `.env`, which is
gitignored; non-secret configuration lives in [`config/`](../../../config/) as YAML:

| file | holds |
|---|---|
| `config/settings.yaml` | model parameters, thresholds, live-trading guards (`max_order_value`, `min_turnover`), account and portfolio selection |
| `config/asset_universe.yaml` | the investable universe — tickers, roles, target weights |
| `config/settings.local.yaml` | machine-local overrides; gitignored |

The point is not tidiness. A credential read directly from the environment in a random module is one
that cannot be found when it needs rotating, and one that a test can silently pick up from a
developer's shell.

## Why weights come from YAML rather than code

`portfolio_weights()` is what the live rebalancer sizes against, so the target allocation is a
reviewable, diffable artefact rather than a literal buried in a script. A weights change shows up in
`git diff` before it shows up in the market.
