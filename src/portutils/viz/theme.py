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

FINISHED 2026-08-31. Defining the token was only half the job — the old sites went on carrying
hand-copied literals for months afterwards, so "one place to change" was aspirational rather than
true. ``panel.py``'s ``OFF_WHITE`` (:931), its PCA-waterfall title (:559) and
``rebalance_study.py``'s ``FIG_KW`` (:104) now all read ``INK`` from here.

ONE DIVERGENCE SURVIVES, DELIBERATELY: ``make_level_figure``'s ``font_color`` default
(``dash_timeseries_app.py:1542``, again at :1701) is the CSS keyword ``white``, i.e. full-strength
#ffffff, not this off-white. It is left alone pending a decision on whether the Dash surface
wants the brighter text — a question about design intent, not a stray literal.

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

# Gridlines and axis ticks: white at 10% opacity. Visible enough to read a value off, dim enough
# that the data sits in front of it rather than competing with it.
#
# CHANGED 2026-08-31, from the solid "#526070" plotly_dark grid tone. Two grid colours were in
# use — this token, and the rgba literal inside PanelBuilder.dark_axis_style — and when
# dark_axis_style was lifted into this module the two had to become one. The rgba won because it
# is the one the panel figures have actually been rendering with, and because a TRANSPARENT grid
# composites correctly against any surface: the same figure now sits on VS Code's editor
# background, on the Dash page div and on a static page, and a solid tone that suits one of those
# is wrong on the others. Consequence: figures exported through apply_export_theme (the
# rebalance_study scenarios) now carry a slightly fainter grid than before. Deliberate.
GRID = "rgba(255,255,255,0.1)"

# The zero line, kept SOLID and therefore brighter than the grid: it is a reference value, not
# background rule, and should read as such. Consumed by dash_timeseries_app's BaseFigureConfig
# (:134). NOTE: apply_export_theme below passes `grid` for zerolinecolor rather than this token —
# left as written, since changing it would move existing exports for a reason nobody asked for.
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
# FIGURE THEMING — the palette applied to a figure being BUILT.
#
# Lifted out of PanelBuilder (panel.py) on 2026-08-31, verbatim apart from the
# changes noted below. It lived there as two classmethods, which meant that
# only code willing to import an 1,100-line class that also loads CSVs could
# reuse it — so pipelines and research scripts each grew their own partial
# copy instead. It is not a panel concept; it is THE theme, and it belongs
# with the tokens it uses.
#
# PanelBuilder.dark_axis_style and PanelBuilder.apply_dark_theme both remain,
# as thin wrappers, so every existing call site is untouched.
# ============================================================================

def dark_axis_style(ink=INK, grid=GRID):
    """
    Return the standard axis-style dict

    Was a classmethod on PanelBuilder so that it could reference the OFF_WHITE color constant
    defined at the class level, and so that it could be called directly on the class without
    needing an instance (e.g. PanelBuilder.dark_axis_style()). Now a plain function reading INK
    from this module: the class-level indirection existed only to reach a colour that already
    lives here, and OFF_WHITE is itself an alias of INK as of 2026-08-31.
    """
    return dict(
        showgrid=True,
        gridcolor=grid, # light transparent grid lines
        tickfont=dict(color=ink), # tick labels in off-white
        linecolor=ink, # axis lines in off-white, just like the ticks and labels
        zeroline=False,
        title_font=dict(color=ink), # axis title in off-white as well
    )


def apply_dark_theme(fig, # important: usually a now enriched figure object that we have created
                     height=None, # pixel height; None leaves it unset, so the figure fills its container
                     width=None, # pixel width; None likewise — see the note on responsiveness below
                     second_axis=False, # whether to apply the standard axis styling to a secondary y-axis (useful for the PCA waterfall plot where we have a secondary y axis for the cumulative variance)
                     ink=INK,
                     grid=GRID,
                     **layout_overrides
                     # note that the ** syntax allows us to accept any number of additional keyword
                     #  arguments that will be collected into a dictionary called layout_overrides;
                     #  this is useful for allowing users to override or add to the base layout
                     #  settings when calling this method, without having to explicitly define every
                     #  possible layout parameter in the method signature
                     ):
    """
    Apply the standard dark transparent theme to *fig*.

    Extra keyword arguments are forwarded to ``fig.update_layout()``.

    CHANGED ON EXTRACTION (2026-08-31): ``height`` and ``width`` default to None rather than
    700/1200. A fixed pixel size is right for a Dash panel and wrong for a static page, which
    should fill the browser window; ``PanelBuilder.apply_dark_theme`` passes the old defaults, so
    its figures are unaffected. The play/animate button moved to that wrapper too — it is
    PanelBuilder's animation machinery, not part of a palette.
    """
    # ---- 1. Base Layout ----
    # we start by defining a base layout dict with the standard dark theme settings: we use the "plotly_dark"
    # template for overall styling,
    # the 'theme' being dark means that the default colors for text, axes, and gridlines will be light/off-white,
    #  which provides good contrast against the dark background;
    # and we set both the paper and plot background colors to transparent so that it can blend seamlessly
    # when embedded in the portfolio site, which has its own dark background
    #   (the portfolio site only has this dark background because the plots have transparent backgrounds while the
    #   rest of the site design is light on dark (due to the fact that we specify the theme as "plotly_dark"))
    base = dict(
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)', # transparent background for embedding in the portfolio site, which has its own dark background; this way we get a seamless look without a black box around the plot
        plot_bgcolor='rgba(0,0,0,0)',
        # The three text roles plotly treats separately. Added on extraction: the original relied
        # on plotly_dark's own font colour for these, which is NOT this repo's ink.
        font=dict(color=ink),
        title_font=dict(color=ink),
        legend=dict(font=dict(color=ink)),
    )
    # Only pin a pixel size if the caller asked for one — see the docstring note.
    if height is not None:
        base['height'] = height
    if width is not None:
        base['width'] = width

    # The below allows us to override any of the base layout settings by passing additional keyword
    # arguments when calling apply_dark_theme.
    # note that base is a dictionary and using the .update() method allows us to update it with
    # the key-value pairs from layout_overrides, (e.g., if we have showlegend=True, the base dict
    #  will be updated to include that setting)
    base.update(layout_overrides)

    # in the belwo syntax, we apply the possibly overridden base layout settings to the figure
    # even though base is a dict, and fig_update_layout() accepts keyword arguments, the ** syntax allows us
    #  to unpack the key-value pairs in the base dictionary and pass them as keyword arguments to
    # fig.update_layout();
    fig.update_layout(**base)

    # ----- 2. Axis Styling ----

    # apply standard dark styling to all axes. NOTE — the two comments here previously described
    # the opposite of what the code does ("grid lines on x-axes only"); corrected on extraction
    # rather than carried across wrong. The code has always turned the x grid OFF and left the y
    # grid ON, i.e. HORIZONTAL rules only, which is the right default for a time series: you read
    # a level off the y-axis, and vertical rules just add noise.
    axis_style = dark_axis_style(ink=ink, grid=grid)

    # x-axes: same style, grid suppressed
    x_axis_style = {**axis_style, 'showgrid': False}
    fig.update_xaxes(x_axis_style)

    # y-axes keep the grid.
    # update_yaxes applies to EVERY y-axis in the figure (yaxis, yaxis2, etc.)
    # overwrite=False only affects nested dict properties (e.g. title=dict(...)): it merges
    # the existing nested dict with the update rather than replacing it wholesale.
    # for flat scalar properties (showgrid, gridcolor, tickfont, linecolor, etc.) the flag has
    # no effect — update_yaxes always overwrites them, including on yaxis2
    fig.update_yaxes(axis_style, overwrite=False)

    # Because update_yaxes above replaced any yaxis2-specific settings BESIDES nested dict properties
    # (i.e., title, overlyaing and side, unchanged, but showgrid and tickfont.color were replaced) that were written
    # in update_layout(**base) (step 1 above), we re-apply them here for the secondary axis.
    # merged = axis_style defaults overridden by whatever the caller passed as yaxis2 in layout_overrides
    # (e.g. range, title, overlaying, side, tickformat).
    # fig.update_layout(yaxis2=merged) MERGES into the existing yaxis2 object — it does NOT
    # wipe and replace it. Only the keys present in merged are touched; any other properties
    # already on yaxis2 (written by earlier calls) survive untouched.
    if second_axis:
        merged = {**axis_style, **layout_overrides.get('yaxis2', {})}
        fig.update_layout(yaxis2=merged)

    return fig


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
