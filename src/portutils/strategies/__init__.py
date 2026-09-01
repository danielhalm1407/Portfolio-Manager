"""
STRATEGIES — the stage-based strategy package (Phase 16).

WHY THIS PACKAGE EXISTS
-----------------------
Three systems in this repo compute portfolio decisions and none of them import each other:
``analysis/strategies.py`` (vectorised, weights), ``portfolio/rules.py`` (event-driven, units)
and ``orders/kts.py`` (live GUI, weights -> units). Phase 16 consolidates them behind one set of
STAGE functions — observe, constraints, sizing, orders, schedule, targets — with concrete
strategies composed from those stages rather than each re-implementing them. The full scoping
lives in ``.paul/phases/16-strategy-architecture/CONTEXT.md``; it is not restated here.

WHAT IS ACTUALLY HERE RIGHT NOW
-------------------------------
Only ``instruments/`` — and within it only the two pieces the option-overlay probe needed:
``vol.py`` (the synthetic implied-vol surface) and ``pricing.py`` (the European put). Plan 16-01
fills in the remaining stage modules; 16-02 adds ``instruments/options.py``. This package is
deliberately created EARLY and EMPTY-ish so the probe's code has a home that matches where it
will permanently live, rather than sitting in a research script and being moved later.

Nothing here imports from ``portfolio/`` or ``analysis/``, and nothing here has import-time side
effects — the same rule that governs the rest of ``portutils``.
"""
