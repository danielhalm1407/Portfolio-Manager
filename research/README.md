# `research/` — narrative in, tilts out

The human-readable half of the project. Where [`models/`](../models/) holds executable logic and
[`src/`](../src/) holds the machinery, this folder holds **reasoning**: what the market is saying,
what that implies, and how much conviction it deserves.

## The intended flow

```
market commentary            data/raw/commentary/   ← scrape_commentary.py
  ▼
theme brief                  research/themes/       ← source, narrative, scored tilt, confidence, horizon
  ▼
conviction write-up          research/conviction/   ← deeper fundamental work on one name or sector
  ▼
target weights               config/asset_universe.yaml
  ▼
the execution stack                                 ← EXECUTION_STACK.md
```

**Current state, stated plainly:** `themes/` and `archive/` exist but are empty, and `conviction/`
has not been created yet. The pipeline that would fill them
([`theme_scan.py`](../src/pipelines/theme_scan.py)) exists. This part of the project is scaffolded,
not yet populated.

## Format rules

From [`CLAUDE.md`](CLAUDE.md), which is the authority:

- every theme brief carries **source, narrative summary, scored tilt, confidence level, time
  horizon** — a tilt without a confidence and a horizon cannot be acted on or reviewed later;
- superseded research moves to `archive/` with a `YYYY-MM-DD` prefix; it is **never deleted**,
  because being wrong is the useful part of a research record;
- conviction write-ups belong in `conviction/`, not `themes/`.

## The studies that live here

Ad-hoc `# %%` cell scripts — exploratory, run by hand, each with a companion write-up:

| script | question it answers | write-up |
|---|---|---|
| [`check_existing_port.py`](check_existing_port.py) | What is the realised/unrealised P&L of every holding in the paper account, using broker data rather than simulated fills? | [check_existing_port.md](check_existing_port.md) |
| [`rebalance_realisation.py`](rebalance_realisation.py) | How does rebalancing itself generate realised P&L? | [rebalance_realisation.md](rebalance_realisation.md) |
| [`hedge_sleeves/hedge_sleeves.py`](hedge_sleeves/hedge_sleeves.py) | How should a hedge sleeve be sized and rotated? | — |
| [`option_overlay_probe.py`](option_overlay_probe.py) | Does the synthetic vol surface plus a European put price a sane strike ladder on the real SPY path? | [option_overlay_probe.md](option_overlay_probe.md) |

These use `CLIENT_ID = 141` when they connect, distinct from the pipeline's 151 and the debug
harness's 161 — TWS misbehaves silently when two connections share an id.

Anything here that proves durable should be extracted into [`portutils/`](../src/portutils/) rather
than left in a cell script; that is how `Book` and the rebalance rules got their home.
