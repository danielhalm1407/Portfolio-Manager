"""
THEME — the single source of truth for colour across every figure in the repo.

WHY THIS MODULE EXISTS
----------------------
"Off-white" was spelled four different ways (`#f2f5fa`, `#e0e0e0`, `#ffffff`, the CSS keyword
`white`) across `dash_timeseries_app.py`, `panel.py` and the pipelines, and the semantic palettes
(which colour is KMLM, which is the `mix` book) lived in `pipelines/rebalance_study.py` — a
PIPELINE owning a definition that `research/rebalance_realisation.py` then reached back into.
One import-light module fixes both: anything can ask for a colour without pulling in the
1,700-line Dash app, and there is one place to change when the palette changes.

THE RULE ABOUT WHERE THE DARK PALETTE APPLIES
---------------------------------------------
It applies where an HTML DOCUMENT is produced — a running Dash app, or a figure saved with
``write_html``. It does NOT apply to a figure built in a ``# %%`` cell and shown in the VS Code
interactive window: there, ``make_level_figure``'s transparent background inherits VS Code's own
dark theme and already looks correct. Baking an opaque background into ``make_level_figure``'s
defaults would fix the export case by breaking the inline case, which is why those defaults are
deliberately left alone and ``apply_export_theme`` exists instead.

That is the whole reason a standalone HTML opened in Chrome used to come out white: the figure was
transparent, the Dash page normally supplies a ``#111`` div behind it, and a saved file has no such
div — so the browser painted its own white. Chrome's dark mode styles its UI, not page content.
"""

# ============================================================================
# CORE PALETTE — the dark theme, used for HTML output (saved figures + Dash).
# ============================================================================

# Primary text: figure titles, axis titles, tick labels. A soft off-white rather than pure
# white — full-strength white on a near-black background glares and makes thin type shimmer.
INK = "#e0e0e0"

# Gridlines, zerolines and axis ticks. The plotly_dark grid tone: visible enough to read a value
# off, dim enough that the data sits in front of it rather than competing with it.
GRID = "#526070"
ZEROLINE = "#526070"

# Background outside the plot frame (the "paper") and inside it (the axes region). Kept equal so
# the frame boundary is invisible and the whole figure reads as one dark surface.
PAPER_BG = "rgb(17,17,17)"
PLOT_BG = "rgb(17,17,17)"

# The Dash page div sitting behind the figure. Very slightly darker than the figure itself, so a
# transparent-background figure still lands on something dark.
PAGE_BG = "#111"

# De-emphasised series — the attribution residual ("cash / costs") and anything else that is a
# remainder rather than an instrument. Neutral grey on purpose: it must not read as one of the
# tickers.
MUTED = "#6e7681"

# Hover tooltips invert: dark text on a light box, because a dark tooltip on a dark chart has no
# edge and disappears into the background.
HOVER_FG = "black"
HOVER_BG = "white"


# ============================================================================
# SEMANTIC PALETTES — what a given thing is coloured, repo-wide.
#
# Moved here from pipelines/rebalance_study.py. `rebalance_study` re-exports them, so
# `study.TICKER_COLOURS` and friends keep working for the research scripts that use that spelling.
# ============================================================================

# Book colours, fixed so every figure in a study is mutually consistent: the same book is the same
# colour whether you are looking at equity, P&L split or weights.
BOOK_COLOURS = {
    "drift": "#8b949e",        # grey — the do-nothing baseline
    "mix": "#1f77b4",          # steel blue — the rebalanced book
    "mix+overlay": "#d62728",  # crimson — the discretionary overlay on top
}

# Ticker colours follow the hedge_sleeves.py scheme so charts read the same across the repo:
# KMLM yellow (grouped with the managed-futures/metals family), SPY steel blue.
#
# THIS DICT IS AN OVERRIDE TABLE, NOT THE PALETTE. It pins the handful of tickers that must
# keep one identity across every study in the repo. Any other ticker is coloured by
# ticker_colour_map() below — because a caller doing `TICKER_COLOURS.get(t, "#6baed6")` over
# an arbitrary holdings list paints EVERY unknown ticker the same blue, which is how a
# five-symbol stacked chart ends up looking like one series.
TICKER_COLOURS = {"KMLM": "#f0c040", "SPY": "#1f77b4", "MNA": "#d62728", "FLSP": "#9467bd"}

# ============================================================================
# CATEGORICAL SERIES PALETTE — for tickers with no fixed identity above.
#
# Eight hues, in a FIXED order that is itself the colour-blindness mechanism: the order was
# chosen so that every ADJACENT pair stays separable under simulated protanopia/deuteranopia/
# tritanopia, which is what matters for a stacked area where neighbouring bands touch.
# Validated against this repo's dark surface (PLOT_BG, rgb(17,17,17)):
#   lightness band  all 8 inside OKLCH L 0.48-0.67
#   chroma floor    all 8 >= 0.1
#   CVD separation  worst adjacent pair dE 8.4 (protan), target >= 8
#   normal vision   worst adjacent pair dE 19.3, floor >= 15
#   contrast        all 8 >= 3:1 against the surface
# Re-run the check before editing this list; do not re-order it by eye. The order IS the
# guarantee, so swapping two entries can silently break the adjacent-pair separation.
# ============================================================================
CATEGORICAL = [
    "#3987e5",  # 1 blue
    "#d95926",  # 2 orange
    "#199e70",  # 3 aqua
    "#c98500",  # 4 yellow
    "#d55181",  # 5 magenta
    "#008300",  # 6 green
    "#9085e9",  # 7 violet
    "#e66767",  # 8 red
]


def ticker_colour_map(symbols, overrides=None):
    """Deterministic symbol -> colour, stable across runs and across figures.

    # ========================================================================
    # WHY A FUNCTION AND NOT A DICT
    # The set of tickers in a research script is whatever the account happens to
    # hold, so it cannot be enumerated in advance. This assigns each one a slot
    # from CATEGORICAL in a FIXED order — never a hash, never a cycle:
    #   * fixed order   the same basket always produces the same picture, so two
    #                   charts of the same book are directly comparable
    #   * never cycled  a 9th series does NOT wrap around to slot 1 and become a
    #                   twin of an existing band. It goes MUTED grey, which reads
    #                   as "not individually tracked" rather than lying about
    #                   identity. Eight is the honest limit of this encoding.
    # Symbols are consumed in the order given, so callers wanting a stable map
    # should pass a sorted list.
    # ========================================================================

    Parameters
    ----------
    symbols : iterable[str]
        Tickers to colour.
    overrides : dict[str, str] | None
        Fixed identities that must win. Defaults to TICKER_COLOURS; pass {} to
        ignore them entirely and colour purely by slot.

    Returns
    -------
    dict[str, str] — symbol -> hex colour.
    """
    overrides = TICKER_COLOURS if overrides is None else overrides
    out = {}
    slot = 0
    for sym in symbols:
        # A pinned ticker keeps its repo-wide colour and does NOT consume a slot, so the
        # unpinned names still start at the top of the palette.
        if sym in overrides:
            out[sym] = overrides[sym]
            continue
        if slot < len(CATEGORICAL):
            out[sym] = CATEGORICAL[slot]
            slot += 1
        else:
            # Past the eighth distinct hue, invent nothing. See the note above.
            out[sym] = MUTED
    return out

# Realised vs unrealised: green for banked, amber for still-at-risk. The whole point of the
# rebalancing study is the distinction, so it gets its own unambiguous pair.
SPLIT_COLOURS = {"realised": "#2ca02c", "unrealised": "#f0c040", "total": INK}

# The attribution residual: whatever a book's equity did that weights-times-returns cannot explain
# (slippage, uninvested cash). Neutral grey on purpose — it must not read as one of the
# instruments, and it must be distinguishable from every TICKER_COLOURS entry above.
RESIDUAL_LABEL = "cash / costs"
RESIDUAL_COLOUR = MUTED


# ============================================================================
# EXPORT THEMING
# ============================================================================

def apply_export_theme(fig, *, ink=INK, grid=GRID, paper=PAPER_BG, plot=PLOT_BG):
    """Stamp the dark palette onto a FINISHED figure, in place, and return it.

    Called immediately before ``write_html``. Applied to a completed figure rather than threaded
    through figure construction on purpose: the ``fig_*`` builders in
    ``pipelines/rebalance_study.py`` serve BOTH the pipeline (which saves HTML) and
    ``research/rebalance_realisation.py`` (which shows the same figures inline). Theming at the
    point of export means one implementation serves both, with no ``theme=`` argument to thread
    through five signatures and no change to what the interactive window sees.

    Only presentation attributes are touched — never traces, never data.
    """
    fig.update_layout(
        # Opaque backgrounds. This is the actual fix for white standalone HTML: the saved document
        # has no page div behind it, so the figure has to bring its own surface.
        paper_bgcolor=paper,
        plot_bgcolor=plot,
        # Global font, then the specific roles. Plotly does not reliably inherit the global font
        # colour into axis tick labels, so each role is set explicitly rather than assumed.
        font=dict(color=ink),
        title_font=dict(color=ink),
        legend=dict(font=dict(color=ink)),
    )
    # Axes are updated separately because update_layout's xaxis/yaxis keys would clobber, rather
    # than merge with, whatever tick configuration the figure already carries.
    fig.update_xaxes(
        gridcolor=grid, zerolinecolor=grid, linecolor=grid, tickcolor=grid,
        title_font=dict(color=ink), tickfont=dict(color=ink),
    )
    fig.update_yaxes(
        gridcolor=grid, zerolinecolor=grid, linecolor=grid, tickcolor=grid,
        title_font=dict(color=ink), tickfont=dict(color=ink),
    )
    return fig
