# Portfolio Manager — Claude Instructions

## Project overview
A thematic-fundamental research engine that converts qualitative market narratives into portfolio tilts, then feeds those tilts into a disciplined allocator. Extension: structural commodity sleeve focused on gas/power/electrification value chain.

## Key conventions
- Library code (importable, no side effects) lives in `src/portutils/`
- Runnable workflow scripts live in `src/pipelines/`
- Secrets go in `.env` (never committed), non-secret config in `config/`
- `data/raw/` is append-only — transformations happen in code and land in `data/processed/`
- `research/` is human-readable narrative; `models/` is executable logic
- `notebooks/` are for thinking; `outputs/` are for decisions

## Key approach flags
- Before editing any file, read it first. Before modifying a  │
│ function, grep for all callers. Research before you edit.
- Please do not re-read the same files that you have already read
- Focus primarily only on the most relevant files and within those files, the parts of the files (often lines of code) that are most relevant. You do not need to explore the whole directory.
  - This might involve only editing a specific method within a given py file or class that I wish for you to edit. E.g., In <file> lines <start>-<end>, look at the <function>       │
│ function.  

## Agent roles
- **Research agent** — theme extraction from market commentary
- **Modelling agent** — structural fundamental analysis
- **Allocator agent** — portfolio construction and risk
- **Reporter agent** — output generation and briefings

## Comments — RETENTION IS MANDATORY
- **Never delete or shorten an existing comment** unless I have *explicitly* told you to. When you move, refactor, or relocate code, the comments travel with it verbatim. Editing the logic of a line does not license you to drop the comment above it — update it to stay accurate, but keep it.
- **Comment-retention requirement**
Move every comment with its code. The dual-class rationale,
the daemon-thread explanation in `connect_ib`, and the kts-style reasoning already in
`IBApp` must all survive the collapse. Update (don't delete) the `nextValidId` comment
to reflect that it now sets `connected` itself. Apply the **comment-combination
policy** above wherever an attribute or helper exists in more than one file.

- **Always write fairly detailed comments**, especially a comment above each key or non-trivial line of code. Explain *why*, not just *what*.
- **Maintain the logical, grounded explanatory style already used in `orders/kts.py`** — a header block summarising the function's role / threading model / when it's called, then a short rationale comment above each meaningful step. New code should read like the surrounding kts.py code.

Example of the expected style (from `orders/kts.py`):

```python
def _check_auto_signal(self, last_price):
    # ============================================================================
    # AUTO-TRADE SIGNAL CHECK — OU mean-reversion.
    # Called from on_tick at bar-close cadence (controlled by Eval-every-N-bars
    # dropdown). Returns silently if the signal is off, the model is not yet
    # calibrated, or the signal condition is not met. When the signal fires, an
    # order is DISPATCHED via root.after(0, ...) so it runs on the Tk main thread
    # (we are on the IB reader thread here — Tk widgets are NOT thread-safe).
    # ============================================================================

    # Gate 1: master toggle. If the user untoggled Auto-trade, do nothing.
    if not self.auto_trade_var.get():
        return
    # Gate 2: the model must be calibrated. Without phi/mu/sigma and a KalmanOU
    # instance, there are no bands and no forecast — nothing to act on.
    if self.kalman is None or self.sigma is None or self.mu is None:
        return
    # Gate 3: sanitize the price. Bad ticks (None / NaN / inf / 0) must never
    # reach the band comparison or the order dispatch.
    if last_price is None or not np.isfinite(last_price) or last_price <= 0:
        return

    # Read the band multiplier k from the slider — same value used by redraw_chart
    # to draw the dotted yellow bands, so the trader sees exactly what the algo
    # is reasoning about.
    k = float(self.band_mult_var.get())
    # Compute upper trading band: mu + k * sigma (stationary std).
    upper = self.mu + k * self.sigma
    # Compute lower trading band: mu - k * sigma.
    lower = self.mu - k * self.sigma
```

## Python
- Package: `portutils` (installed via `pip install -e .`)
- Python >=3.11
- Config loading centralised in `src/portutils/utils/config.py`
