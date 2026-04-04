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
    with open(_settings_path) as f:
        SETTINGS = yaml.safe_load(f) or {}
else:
    SETTINGS = {}
