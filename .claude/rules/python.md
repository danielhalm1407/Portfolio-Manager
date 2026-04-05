---
glob: **/*.py
---

When editing Python files:
- Follow the coding standards in `src/CLAUDE.md`
- Never hardcode credentials — import from `portutils.utils.config`
- Library functions in `src/portutils/` must be pure (no side effects on import)
- Side effects (API calls, file writes) belong in `src/pipelines/`
- All new modules in `portutils/` need an `__init__.py`
