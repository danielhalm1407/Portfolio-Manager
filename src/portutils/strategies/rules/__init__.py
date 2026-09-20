"""
RULES — named compositions of stages; one thing that can fire at one point in time.

A rule DECIDES. It does not price (that is ``instruments/``), it does not count the calendar
(``schedule.py``) and it does not account (``portfolio/``'s Book and Fill). Phase 16's CONTEXT
records why the split is by stage: a stack trace should name the step that failed.

Current contents:

* ``options.py`` — ``OptionOverlayRule`` and the three hedge structures from 12-01:
  ``ProtectivePutRule``, ``PutSpreadRule``, ``RollingCollarRule``. Added by 16-03. They subclass
  ``portfolio.rules.RebalanceRule`` so ``PortfolioSimulator`` runs them beside the existing
  weight-based rules.
"""
