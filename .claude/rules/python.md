---
glob: **/*.py
---

When editing Python files:
- Follow the coding standards in `src/CLAUDE.md`
- Never hardcode credentials or call `os.getenv()` directly — secrets live in `.env` (gitignored), loaded once by `load_dotenv()` in `portutils.utils.config`, and accessed via that module everywhere else
- Library functions in `src/portutils/` must be pure (no side effects on import)
- Side effects (API calls, file writes) belong in `src/pipelines/`
- All new modules in `portutils/` need an `__init__.py`
