"""Centralised config loader for the project.

Reads secrets from .env and project settings from config/settings.yaml.
All other modules should import config values from here rather than
calling os.getenv() or reading YAML directly.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
import yaml

# Load .env from project root
load_dotenv()

# Project root is three levels up from this file: src/portutils/utils/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# --- Secrets (from .env) ---
EIA_API_KEY = os.getenv("EIA_API_KEY")
FRED_API_KEY = os.getenv("FRED_API_KEY")

# --- Project settings (from config/settings.yaml) ---
_settings_path = PROJECT_ROOT / "config" / "settings.yaml"
if _settings_path.exists():
    # encoding is explicit: Python defaults to the platform encoding, which is cp1252 on
    # Windows, and any non-Latin-1 character in a config comment then raises
    # UnicodeDecodeError at import time — taking every module that imports config with it.
    with open(_settings_path, encoding="utf-8") as f:
        SETTINGS = yaml.safe_load(f) or {}
else:
    SETTINGS = {}

# --- Investable universe (from config/asset_universe.yaml) ---
# The single source of truth for ticker -> role (hedge / beta / core), sleeve grouping and
# named weight vectors. Loaded once here so no pipeline, study or chart ever opens the YAML
# itself — same rule as SETTINGS above.
_universe_path = PROJECT_ROOT / "config" / "asset_universe.yaml"
if _universe_path.exists():
    # Same explicit encoding as SETTINGS above, for the same reason.
    with open(_universe_path, encoding="utf-8") as f:
        ASSET_UNIVERSE = yaml.safe_load(f) or {}
else:
    ASSET_UNIVERSE = {}


def asset_meta(ticker):
    """Metadata dict for one ticker (name / role / sleeve / note), or {} if unknown.

    Returns empty rather than raising: a ticker that is priced but not yet catalogued
    should degrade to "no role" in a report, not crash the run that produced it.
    """
    return (ASSET_UNIVERSE.get("universe") or {}).get(ticker, {})


def asset_role(ticker, default="unclassified"):
    """The 'hedge' / 'beta' / 'core' role for one ticker.

    This is the axis realised P&L gets attributed along — "did the hedge sleeve bank gains
    while beta fell?" is a question about roles, not about individual tickers.
    """
    return asset_meta(ticker).get("role", default)


def tickers_by_role(role):
    """Every ticker carrying the given role, in file order."""
    return [t for t, m in (ASSET_UNIVERSE.get("universe") or {}).items()
            if m.get("role") == role]


def portfolio_weights(name):
    """A named weight vector from the `portfolios:` block, e.g. 'spy_kmlm'.

    Raises on an unknown name — unlike the metadata lookups above, silently returning an
    empty book would produce a study that runs, reports zeros, and looks plausible.
    """
    portfolios = ASSET_UNIVERSE.get("portfolios") or {}
    if name not in portfolios:
        raise KeyError(
            f"portfolio '{name}' not in config/asset_universe.yaml; "
            f"available: {sorted(portfolios)}"
        )
    return dict(portfolios[name])
