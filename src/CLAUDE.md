# src/ — Claude Instructions

## Structure
- `portutils/` — installable package. Library code only, no side effects on import.
- `pipelines/` — runnable workflow scripts. These orchestrate `portutils` functions and have side effects (API calls, file writes).

## Coding standards
- All new modules in `portutils/` must have an `__init__.py`
- Credentials are never hardcoded — always import from `portutils.utils.config`
- Functions should be pure where possible; side effects belong in `pipelines/`
