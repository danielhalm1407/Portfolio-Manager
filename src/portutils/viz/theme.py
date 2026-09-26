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

# ---------------------------------------------------------------------------
# SPACING ON A FIGURE THAT CARRIES A SECONDARY (RIGHT-HAND) Y-AXIS.
# Moved here from pipelines/option_probe_figures.py on 2026-09-20: these are
# presentation numbers, and presentation numbers belong beside the palette they
# are tuned against rather than in whichever pipeline needed them first.
#
# One builder serves BOTH the interactive window and the published page, and the
# page renders the figure much wider. `legend.x` is a fraction of the PLOT AREA's
# width while the margin is in pixels, so the GAP between the plot's right edge
# and the legend is `(x - 1.0) * plot_width` PIXELS — it scales with the render.
#
# CORRECTED 2026-09-20. The reasoning here was previously inverted: it claimed a
# single x was "too tight once the page stretches the plot", but stretching the
# plot makes the same fraction a WIDER pixel gap, not a narrower one. The
# consequence of that inversion was a real bug — the NARROW inline render was
# given the SMALL fraction (1.04) and the wide export the large one (1.10), i.e.
# exactly backwards. What has to fit in the gap is the y2 tick labels (~22px),
# the title standoff (24px) and the rotated y2 title: about 60px. Inline, with a
# ~880px plot area, 1.04 bought only ~35px, so the y2 title rendered straight
# through the legend; the export's ~1180px plot area at 1.10 bought ~118px and
# looked fine, which is why the bug showed up inline ONLY.
#
# Both values are now 1.10, which clears 60px at either width (88px inline,
# 118px on the page). They stay as two constants — rather than collapsing into
# one — so the page value can still be tuned independently if a longer series
# name ever needs a different gap there; `apply_export_spacing` keeps swapping.
# ---------------------------------------------------------------------------

# Legend's left edge INLINE, as a fraction of the plot area's width (1.0 = the plot's right edge).
# Larger than it looks it should be ON PURPOSE: the inline plot area is the NARROW one, so it needs
# the bigger fraction to buy the same pixel gap. See the correction note above.
SECONDARY_AXIS_LEGEND_X = 1.10

# Legend's left edge in the EXPORTED figure (standalone page and report page). Applied only by
# `apply_export_spacing`, so changing it cannot affect what the interactive window shows.
SECONDARY_AXIS_LEGEND_X_EXPORT = 1.10

# Right margin in pixels — the strip the y2 ticks, the y2 title and the legend share.
SECONDARY_AXIS_MARGIN_R = 340

# Gap in pixels between the y2 tick labels and the rotated y2 title.
SECONDARY_AXIS_TITLE_STANDOFF = 24

# ---------------------------------------------------------------------------
# STACKED-SUBPLOT HEIGHT. Plotly's default figure height is 450px regardless of how many rows
# `make_subplots` was given — it sizes the FIGURE, not the panels inside it. A 2- or 3-row stacked
# figure left at that default renders every row squeezed into a fraction of 450px, which is
# unreadable once `.figure` (build_report._CSS) stretches the same figure to ~1600px wide on a
# published page: the chart gets wider and wider while staying exactly as short.
#
# Keyed by row count so a new stacked figure picks its height by asking "how many rows do I have"
# rather than by choosing a number: `fig_overlay_values` (2 rows) and `fig_put_paths_market` /
# `fig_ladder` (3 rows) all read from here. Values are the ones already in use across the repo,
# centralised here rather than left as three separate literals that could drift.
STACKED_FIGURE_HEIGHT = {2: 820, 3: 900}


def _is_secondary_axis(fig, key):
    # `overlaying` names whichever axis is underneath, which is "y" on a single-panel figure but
    # "y3" for a secondary axis inside a subplot row — so test that it is SET, not that it is "y".
    return (key.startswith("yaxis") and key != "yaxis"
            and getattr(fig.layout, key).overlaying not in (None, ""))


def apply_secondary_axis_spacing(fig, *, enabled=None):
    """Clear the legend of a right-hand axis, in place, and return the figure.

    Plotly puts the legend immediately right of the plot area and does NOT count a right-hand axis
    as something to clear, so on a two-axis figure the y2 tick labels and its title render straight
    through the legend text. Pushing the legend further right, reserving margin for it and pushing
    the y2 title clear of its own ticks separates the three.

    Called during construction, so the interactive window and the export agree on everything but
    the one value `apply_export_spacing` changes.

    ``enabled`` overrides the auto-detection, which asks only WHETHER a secondary axis exists, not
    whether it is anywhere near the legend: on a stacked subplot the legend is anchored to the top
    of the figure, so a secondary axis in a lower row never reaches it and the reserved strip would
    only steal width from every panel. ``pipelines.option_probe_figures.fig_overlay_values`` passes
    its own data-driven flag for exactly that reason.
    """
    has_secondary = (any(_is_secondary_axis(fig, key) for key in fig.layout)
                     if enabled is None else enabled)
    if not has_secondary:
        return fig

    fig.update_layout(
        # Fraction of the PLOT AREA's width, not pixels. Anchored left so the legend grows
        # rightwards into the margin reserved below.
        legend=dict(x=SECONDARY_AXIS_LEGEND_X, xanchor="left", y=1.0, yanchor="top"),
        # The margin, in PIXELS, that the legend now lives in. Too small and the legend is
        # clipped; this is the number to raise if a longer series name is ever cut off.
        margin=dict(r=SECONDARY_AXIS_MARGIN_R),
    )
    # Push the y2 TITLE clear of its own tick labels. Plotly's default standoff assumes there is
    # nothing to its right, so on a wide render the rotated title drifts into the legend.
    for key in fig.layout:
        if _is_secondary_axis(fig, key):
            fig.layout[key].title.standoff = SECONDARY_AXIS_TITLE_STANDOFF
    return fig


def apply_export_spacing(fig):
    """Widen the legend gap for a figure about to be SAVED, in place, and return it.

    The one layout difference between what the interactive window shows and what is published.
    Every colour, font and grid line is already identical because both go through
    ``apply_export_theme``; spacing has to differ because ``legend.x`` is a fraction of the plot
    area's width, so the same number is a narrower gap inline than on the full-width page.

    Only figures that went through ``apply_secondary_axis_spacing`` carry a legend x at all, which
    is exactly the set this needs to touch — hence the identity test against the inline constant
    rather than a flag threaded through every caller. Idempotent.
    """
    if fig.layout.legend.x == SECONDARY_AXIS_LEGEND_X:
        fig.update_layout(legend=dict(x=SECONDARY_AXIS_LEGEND_X_EXPORT))
    return fig


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


# ============================================================================
# THE PAGE SURFACE — CSS, not Plotly attributes.
#
# `apply_export_theme` above darkens the FIGURE. It cannot darken the PAGE: a document written by
# `fig.write_html(full_html=True)` carries a bare `<body>` with no background of its own, so the
# margin around the figure div is whatever the browser paints — white. The figure is then a dark
# rectangle on a white sheet, which is the half-fixed version of the failure the module docstring
# records.
#
# These live HERE rather than in either pipeline because BOTH produce pages off the same palette:
# `pipelines/option_probe_figures.write_index` and `research/validation/option_ladder_probe`'s
# GFC index. Two copies of "what colour is the page" is two things to forget to change.
# ============================================================================

# The minimum rule that makes a page dark: the surface behind the figure, the default text colour
# for anything the figure does not draw, and no body margin (Plotly's own div manages its spacing).
BODY_CSS = f"html,body{{background:{PAGE_BG};color:{INK};margin:0}}"

# The same rule as a complete <style> element, ready to inject into a document that already has a
# <head>. Kept as a constant rather than built per call so `darken_page`'s idempotence check below
# is an exact string comparison.
BODY_STYLE_TAG = f"<style>{BODY_CSS}</style>"


def darken_page(html):
    """Inject the page background into an already-written HTML document, before ``</head>``.

    Post-processing the document rather than assembling it by hand: ``fig.write_html`` owns the
    script tags, the div id and the config JSON, and re-implementing all of that just to add one
    ``<style>`` would be a second thing to keep in step with Plotly's output format.

    Idempotent — a document that already carries the tag is returned unchanged, so re-running an
    export does not grow the file. Returns the HTML rather than writing it, so the caller keeps
    control of encoding and of the path.
    """
    if BODY_STYLE_TAG in html:
        return html
    return html.replace("</head>", f"{BODY_STYLE_TAG}</head>", 1)


def page_css(max_width="46rem"):
    """The full stylesheet body for a HAND-ROLLED page — an index, a narrative report.

    Everything `BODY_CSS` covers, plus the typographic and link treatment those pages share:
    readable measure, system font stack, links in the first categorical colour (so a link and the
    first series on the page below it are the same hue), `MUTED` for caveat lines, `GRID` for
    table rules — the same tone the gridlines inside the figures use, so a table and a chart on
    one page do not disagree about what a faint line looks like.

    `max_width` is the one thing that legitimately varies: a link list wants a narrow measure, a
    page with a wide summary table wants more. Returned WITHOUT the enclosing <style> tag so a
    caller can append its own page-specific rules.
    """
    return (
        f"body{{background:{PAGE_BG};color:{INK};"
        "font:16px/1.6 system-ui,-apple-system,sans-serif;"
        f"max-width:{max_width};margin:0 auto;padding:3rem 1.5rem}}"
        f"a{{color:{CATEGORICAL[0]}}}"
        "li{margin:.5rem 0}"
        f"p.note,.note{{color:{MUTED};font-size:.9rem}}"
        "table{border-collapse:collapse}"
        f"td,th{{border:1px solid {GRID};padding:.35rem .6rem;text-align:right}}"
        "th:first-child,td:first-child{text-align:left}"
    )
