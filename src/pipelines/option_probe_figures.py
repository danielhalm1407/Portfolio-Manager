"""
OPTION PROBE FIGURES — the data prep and Plotly builders behind the option-overlay probe.

WHY THIS MODULE EXISTS, AND WHY IT IS A PIPELINE RATHER THAN A LIBRARY
----------------------------------------------------------------------
It serves TWO consumers with one implementation:

* ``research/option_overlay_probe.py`` imports the builders and calls ``.show()`` on them, so
  the figures appear inline in the VS Code interactive window;
* ``main()`` in this file renders the same figures to standalone HTML under ``docs/figures/``
  for GitHub Pages.

That is exactly the arrangement ``viz/theme.py`` documents for ``pipelines/rebalance_study.py``
and ``research/rebalance_realisation.py``, and the reason ``apply_export_theme`` exists as a
post-hoc stamp rather than a ``theme=`` argument threaded through every signature. The builders
below therefore return UNTHEMED figures with transparent backgrounds — correct inline — and only
``main()`` stamps the dark palette on, immediately before writing.

It lives in ``pipelines/`` and not ``portutils/`` for the same reason ``rebalance_study.py``
does: it has side effects (it writes files), and ``src/CLAUDE.md`` puts side effects here. The
pure pieces it depends on — the vol surface and the pricer — are in
``portutils/strategies/instruments/``.

WHAT THE FIGURES SHOW
---------------------
1. ``fig_smirk``            the surface's shape at one spot, across four tenors
2. ``fig_iv_paths``         IV per ladder strike through time, v1 frozen vs v2 current-spot
3. ``fig_put_paths``        the same ladder valued, with spot on a secondary axis
4. ``fig_drawdown_episode`` a put struck AT THE PEAK and held through the drawdown — the figure
                            that shows monetisation rather than carry

Run as a script to export:  ``python -m pipelines.option_probe_figures``
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from portutils.strategies.instruments.pricing import black_scholes_put
from portutils.strategies.instruments.vol import synthetic_iv_surface
from portutils.utils.config import PROJECT_ROOT
from portutils.viz import theme


# ============================================================================
# PARAMETERS — shared by the research cells and the export, so a figure shown
# inline and the same figure on the published page can never disagree.
# ============================================================================

# The strike ladder as moneyness against the reference spot, not as absolute strikes, so it is
# meaningful whatever window the panel covers. 0.80 is roughly where the Obsidian hedge
# catalogue's collar puts its floor; 1.00 is the ATM reference.
MONEYNESS = (0.80, 0.90, 0.95, 1.00)

# 63 trading days — the roll period the collar resets on ("How to roll a put hedge").
TENOR_YEARS = 63 / 252

# 12-01's worked examples use r = 0.02 and q = 0, kept identical so any number printed here can
# be cross-checked against the plan by hand. SPY's real ~1.2% yield is a parameter 16-02's
# OptionLeg must carry; leaving it at zero here is a deliberate probe-grade omission.
RATE = 0.02
DIV_YIELD = 0.0

# The v1 ATM vol level. Named rather than inlined because the episode figure needs to vary it.
BASE_VOL = 0.16

# ILLUSTRATIVE vol-level response for the episode figure: base rises by VOL_BETA vol points per
# 1.0 of drawdown, so a -9.1% fall lifts the ATM level 0.16 -> 0.34. This is a STAND-IN for term
# A (a dynamic vol level), not a calibrated model — calibrating it is what Phase 11's data is
# for. It exists to show the MAGNITUDE v1 leaves on the table, and it is always drawn as a
# separate, dashed series so it can never be mistaken for the v1 result.
VOL_BETA = 2.0

# Dense grid and tenors for the smirk figure only.
SMIRK_SPOT = 100.0
SMIRK_MONEYNESS_GRID = np.linspace(0.60, 1.30, 141)
SMIRK_TENORS = {"1 month": 21 / 252, "3 months": 63 / 252, "6 months": 126 / 252, "1 year": 1.0}

# The panel the spot path is read from, and where the exported pages land. `docs/` is chosen
# because GitHub Pages can serve a repo's `docs/` folder on the default branch directly, and
# because `outputs/` is gitignored (.gitignore:37) — a published figure has to be committed.
PRICE_PANEL = PROJECT_ROOT / "data" / "processed" / "prices_spy_kmlm.parquet"
UNDERLYING = "SPY"
DOCS_FIGURES = PROJECT_ROOT / "docs" / "figures"


# ============================================================================
# DATA PREP — every frame the figures need, built from the price panel alone.
# Pure functions: no I/O beyond the one parquet read, no globals mutated.
# ============================================================================

def load_spot_path(panel=PRICE_PANEL, symbol=UNDERLYING):
    """Return the underlying's close series, NaNs dropped.

    A straight parquet read rather than ``PanelBuilder``: this needs ONE column and no
    normalisation, and ``PanelBuilder._load`` currently truncates ragged history (the defect
    11-01 fixes at both sites). Nothing here is ragged — one ticker, one file — but going around
    the loader keeps the probe independent of how that fix lands.
    """
    return pd.read_parquet(panel)[symbol].dropna()


def build_ladder(spot_ref, moneyness=MONEYNESS):
    """Absolute strikes from moneyness against a single reference spot."""
    return {m: m * float(spot_ref) for m in moneyness}


def iv_paths(spot_path, ladder, tenor=TENOR_YEARS, spot_ref=None, base=BASE_VOL):
    """Return ``(iv_v1, iv_v2)`` — implied vol per strike, per bar, under both references.

    v1 pins the moneyness reference to ``spot_ref`` (the period-start spot), so for a fixed
    strike ``log(K / spot_ref)`` never changes and the IV is CONSTANT through time. That flat
    line is not a bug — it is the definition of a frozen reference, and it is what ships.

    v2 re-evaluates against each bar's own spot, so as the market falls a fixed strike slides
    DOWN the smirk and its IV FALLS. That is the whole content of the 2026-08-29 decision:
    term B on its own makes protection cheaper in exactly the selloff it is meant to pay in.
    """
    spot_ref = float(spot_path.iloc[0]) if spot_ref is None else float(spot_ref)
    v1 = pd.DataFrame(
        # One scalar per strike, broadcast across the index — the frozen reference.
        {m: np.full(len(spot_path), float(synthetic_iv_surface(k, tenor, spot_ref, base=base)))
         for m, k in ladder.items()},
        index=spot_path.index,
    )
    v2 = pd.DataFrame(
        # The surface re-evaluated against every bar's spot — the current reference.
        {m: synthetic_iv_surface(k, tenor, spot_path.to_numpy(), base=base)
         for m, k in ladder.items()},
        index=spot_path.index,
    )
    return v1, v2


def put_paths(spot_path, ladder, iv_frame, tenor=TENOR_YEARS):
    """Value the whole ladder bar by bar against one IV frame.

    Tenor is held CONSTANT on purpose: every bar shows the value of a FRESHLY STRUCK 63-day put,
    so the series isolates the spot and vol effect. Theta decay within a roll period is a
    property of the roll, and the roll is 16-02's ``RollCalendar``, not this probe's.
    """
    return pd.DataFrame(
        {m: black_scholes_put(spot_path.to_numpy(), k, tenor, iv_frame[m].to_numpy(),
                              rate=RATE, div_yield=DIV_YIELD)
         for m, k in ladder.items()},
        index=spot_path.index,
    )


def worst_drawdown_episode(spot_path):
    """Return ``(peak_ts, trough_ts, drawdown_series)`` for the window's deepest fall.

    The peak is the running maximum that PRECEDES the trough, not the window's global maximum —
    they coincide here, but the distinction matters the moment a longer history is loaded.
    """
    drawdown = spot_path / spot_path.cummax() - 1.0
    trough = drawdown.idxmin()
    peak = spot_path.loc[:trough].idxmax()
    return peak, trough, drawdown


def episode_paths(spot_path, peak, tenor=TENOR_YEARS, moneyness=MONEYNESS,
                  base=BASE_VOL, vol_beta=VOL_BETA):
    """Value a ladder STRUCK AT ``peak`` across the whole life of that option.

    This is the figure the first pass of the probe was missing. A ladder struck at the start of
    the window has whatever the market does next measured against the START, so a rally followed
    by a fall of the same size leaves it exactly where it began and the puts look like pure
    carry. Striking at the PEAK puts the drawdown IN FRONT of the strike, which is what a rolled
    hedge does automatically every 63 days.

    Returns ``(episode_index, strikes, mtm_flat, mtm_spike)`` where the two frames are the
    mark-to-market per strike under a FROZEN vol level and under the illustrative vol-level
    response respectively.
    """
    # The option's life: `tenor` in trading days from the peak, truncated by the data's end.
    n_bars = int(round(tenor * 252))
    start = spot_path.index.get_loc(peak)
    idx = spot_path.index[start:start + n_bars + 1]
    path = spot_path.loc[idx]

    # Strikes are set once, at the peak spot, and held for the option's life — a struck option
    # does not restrike intra-period.
    ref = float(spot_path.loc[peak])
    strikes = build_ladder(ref, moneyness)

    # Time to expiry decays one trading day per bar. Unlike the constant-tenor ladder above, this
    # IS a single option being carried, so theta is part of what is being measured.
    elapsed = np.arange(len(idx)) / 252.0
    tau = np.maximum(tenor - elapsed, 1e-6)

    # Drawdown measured from the peak, i.e. from the moment the option was struck.
    dd_from_peak = (path / ref - 1.0).clip(upper=0.0)

    flat, spike = {}, {}
    for m, k in strikes.items():
        # v1: IV fixed at the value the strike had when struck. The put's value moves ONLY
        # through the pricer's spot and tau arguments — the surface is never consulted again.
        iv_flat = float(synthetic_iv_surface(k, tenor, ref, base=base))
        flat[m] = black_scholes_put(path.to_numpy(), k, tau, iv_flat,
                                    rate=RATE, div_yield=DIV_YIELD)
        # The illustrative term-A response: the ATM level rises with the drawdown, and the whole
        # smirk lifts with it. Moneyness stays on the frozen reference, so this varies the LEVEL
        # alone — term A without term B, which is the combination v1 leaves unmeasured.
        base_t = base + vol_beta * (-dd_from_peak.to_numpy())
        iv_spike = synthetic_iv_surface(k, tau, ref, base=base_t)
        spike[m] = black_scholes_put(path.to_numpy(), k, tau, iv_spike,
                                     rate=RATE, div_yield=DIV_YIELD)

    return idx, strikes, pd.DataFrame(flat, index=idx), pd.DataFrame(spike, index=idx)


# ============================================================================
# FIGURE BUILDERS — each returns an UNTHEMED figure with transparent
# backgrounds. Correct as-is inline; main() stamps the export palette on.
# ============================================================================

# Transparent rather than a dark template. theme.py is explicit that the dark palette applies
# where an HTML DOCUMENT is produced; a figure shown in the VS Code interactive window inherits
# VS Code's own theme through a transparent background and already reads correctly. Baking
# plotly_dark in here would fix the export case by breaking the inline one.
_TRANSPARENT = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")


def _strike_colours(moneyness=MONEYNESS):
    # One fixed colour per ladder strike, assigned in ladder order so the same strike is the same
    # colour in every figure — the same guarantee theme.ticker_colour_map gives for tickers.
    return {m: theme.CATEGORICAL[i] for i, m in enumerate(moneyness)}


def fig_smirk(spot=SMIRK_SPOT, tenors=SMIRK_TENORS, grid=SMIRK_MONEYNESS_GRID, base=BASE_VOL):
    """The surface's shape at one spot: skew, term structure and convexity, separately visible."""
    fig = go.Figure()
    for i, (label, tenor) in enumerate(tenors.items()):
        # Strikes are absolute (moneyness x spot) because that is what the surface takes; the
        # x-axis stays in moneyness because that is what is readable.
        fig.add_trace(go.Scatter(
            x=grid, y=synthetic_iv_surface(grid * spot, tenor, spot, base=base),
            mode="lines", name=label,
            line=dict(color=theme.CATEGORICAL[i], width=2),
            hovertemplate="K/S %{x:.2f}<br>IV %{y:.1%}<extra>" + label + "</extra>",
        ))
    # ATM marker: the one point whose value is `base` plus the term term and nothing else, since
    # log-moneyness is zero there and the skew and smile both vanish.
    fig.add_vline(x=1.0, line=dict(color=theme.MUTED, width=1, dash="dot"),
                  annotation_text="ATM", annotation_position="top")
    fig.update_layout(
        title=f"Synthetic IV surface — the smirk at spot {spot:.0f}",
        xaxis_title="moneyness K / S", yaxis_title="implied vol",
        yaxis_tickformat=".0%", hovermode="x unified", **_TRANSPARENT,
    )
    return fig


def fig_iv_paths(iv_v1, iv_v2, spot_ref, tenor=TENOR_YEARS):
    """IV per ladder strike through time, both moneyness references overlaid.

    v1 solid because it is what ships; v2 dashed because it is the alternative under
    consideration. Same colour per strike, ``legendgroup``ed so clicking a strike hides both
    rather than leaving its twin behind.
    """
    colours = _strike_colours(tuple(iv_v1.columns))
    fig = go.Figure()
    for m in iv_v1.columns:
        fig.add_trace(go.Scatter(x=iv_v1.index, y=iv_v1[m], mode="lines",
                                 name=f"{m:.2f}x  v1 frozen", legendgroup=f"{m:.2f}",
                                 line=dict(color=colours[m], width=2)))
        fig.add_trace(go.Scatter(x=iv_v2.index, y=iv_v2[m], mode="lines",
                                 name=f"{m:.2f}x  v2 current", legendgroup=f"{m:.2f}",
                                 line=dict(color=colours[m], width=1.5, dash="dash")))
    fig.update_layout(
        title=f"Implied vol per ladder strike — {tenor * 252:.0f}-day tenor, "
              f"struck off spot {spot_ref:.2f}",
        xaxis_title="date", yaxis_title="implied vol", yaxis_tickformat=".0%",
        hovermode="x unified", **_TRANSPARENT,
    )
    return fig


def fig_put_paths(put_v1, put_v2, spot_path, tenor=TENOR_YEARS, symbol=UNDERLYING):
    """The ladder valued through time, with spot on a secondary axis.

    The v1 series MOVE even though their IV does not: spot is the dominant term in a put, so
    holding vol fixed simply means the price is responding to the underlying alone. The v1/v2
    GAP is therefore the pure vol-reference effect with the spot effect divided out.
    """
    colours = _strike_colours(tuple(put_v1.columns))
    fig = go.Figure()
    for m in put_v1.columns:
        fig.add_trace(go.Scatter(x=put_v1.index, y=put_v1[m], mode="lines",
                                 name=f"{m:.2f}x  v1 frozen", legendgroup=f"{m:.2f}",
                                 line=dict(color=colours[m], width=2)))
        fig.add_trace(go.Scatter(x=put_v2.index, y=put_v2[m], mode="lines",
                                 name=f"{m:.2f}x  v2 current", legendgroup=f"{m:.2f}",
                                 line=dict(color=colours[m], width=1.5, dash="dash")))
    # Without the underlying the put series are uninterpretable — every feature in them is a
    # feature of spot.
    fig.add_trace(go.Scatter(x=spot_path.index, y=spot_path.to_numpy(), mode="lines",
                             name=f"{symbol} spot (rhs)", yaxis="y2",
                             line=dict(color=theme.MUTED, width=1)))
    fig.update_layout(
        title=f"European put value per ladder strike — {tenor * 252:.0f}-day tenor, "
              f"re-struck never (one snapshot repriced)",
        xaxis_title="date", yaxis_title="put value (price units)",
        yaxis2=dict(title=f"{symbol} spot", overlaying="y", side="right", showgrid=False),
        hovermode="x unified", **_TRANSPARENT,
    )
    return fig


def fig_drawdown_episode(idx, strikes, mtm_flat, mtm_spike, spot_path, peak, trough,
                         symbol=UNDERLYING):
    """A ladder struck at the peak, carried through the drawdown, in multiples of premium.

    Plotted as a MULTIPLE OF THE PREMIUM PAID rather than in price units, because that is the
    quantity the strikes can be compared on: a 0.90x put costing a fifth of the ATM put has to
    move five times as far to be worth the same money.
    """
    colours = _strike_colours(tuple(mtm_flat.columns))
    fig = go.Figure()
    for m in mtm_flat.columns:
        premium = float(mtm_flat[m].iloc[0])
        fig.add_trace(go.Scatter(
            x=idx, y=mtm_flat[m] / premium, mode="lines",
            name=f"{m:.2f}x  (K={strikes[m]:.0f}, prem {premium:.2f})", legendgroup=f"{m:.2f}",
            line=dict(color=colours[m], width=2),
        ))
        # The illustrative vol-level response, always dashed and always labelled as such, so it
        # cannot be mistaken for the v1 result it is there to bound.
        fig.add_trace(go.Scatter(
            x=idx, y=mtm_spike[m] / premium, mode="lines",
            name=f"{m:.2f}x  + vol spike (illustrative)", legendgroup=f"{m:.2f}",
            line=dict(color=colours[m], width=1.5, dash="dash"),
        ))
    # Break-even: below this line the put is worth less than it cost.
    fig.add_hline(y=1.0, line=dict(color=theme.MUTED, width=1, dash="dot"),
                  annotation_text="premium paid", annotation_position="right")
    fig.add_vline(x=trough, line=dict(color=theme.MUTED, width=1),
                  annotation_text="trough", annotation_position="top")
    fig.add_trace(go.Scatter(x=idx, y=spot_path.loc[idx].to_numpy(), mode="lines",
                             name=f"{symbol} spot (rhs)", yaxis="y2",
                             line=dict(color=theme.MUTED, width=1)))
    fig.update_layout(
        title=f"Puts struck at the peak ({peak.date()}) and carried — "
              f"value as a multiple of premium paid",
        xaxis_title="date", yaxis_title="value / premium paid",
        yaxis2=dict(title=f"{symbol} spot", overlaying="y", side="right", showgrid=False),
        hovermode="x unified", **_TRANSPARENT,
    )
    return fig


# ============================================================================
# EXPORT — the Track A (10-01) pattern, applied to the figures that exist.
# ============================================================================

def build_all():
    """Build every probe figure. Returns ``{filename_stem: figure}``, ready to show or write."""
    spot_path = load_spot_path()
    spot_ref = float(spot_path.iloc[0])
    ladder = build_ladder(spot_ref)
    iv_v1, iv_v2 = iv_paths(spot_path, ladder, spot_ref=spot_ref)
    put_v1 = put_paths(spot_path, ladder, iv_v1)
    put_v2 = put_paths(spot_path, ladder, iv_v2)
    peak, trough, _ = worst_drawdown_episode(spot_path)
    idx, strikes, mtm_flat, mtm_spike = episode_paths(spot_path, peak)
    return {
        "iv_smirk": fig_smirk(),
        "iv_paths": fig_iv_paths(iv_v1, iv_v2, spot_ref),
        "put_paths": fig_put_paths(put_v1, put_v2, spot_path),
        "drawdown_episode": fig_drawdown_episode(idx, strikes, mtm_flat, mtm_spike,
                                                 spot_path, peak, trough),
    }


# Human-readable titles for the index page, keyed by the same stem build_all() returns. Kept
# beside the builders so a new figure cannot be added without a caption being noticed.
FIGURE_CAPTIONS = {
    "iv_smirk": "The synthetic IV surface at one spot — skew, term structure and convexity",
    "iv_paths": "Implied vol per ladder strike through time — v1 frozen vs v2 current-spot",
    "put_paths": "The ladder valued through time, against spot",
    "drawdown_episode": "Puts struck at the peak and carried through the drawdown",
}


def write_index(out_dir, figures, captions=FIGURE_CAPTIONS):
    """Write a minimal index page linking every exported figure.

    Deliberately hand-rolled HTML with inline CSS and no build step: GitHub Pages serves this
    directory as static files, so anything requiring a bundler would need CI that does not exist
    yet. The palette is read from theme.py so the index and the figures it links are one surface.
    """
    rows = "\n".join(
        f'      <li><a href="figures/{name}.html">{captions.get(name, name)}</a></li>'
        for name in figures
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Manager — option overlay probe</title>
<style>
  body {{ background: {theme.PAGE_BG}; color: {theme.INK};
         font: 16px/1.6 system-ui, -apple-system, sans-serif;
         max-width: 46rem; margin: 0 auto; padding: 3rem 1.5rem; }}
  a {{ color: {theme.CATEGORICAL[0]}; }}
  li {{ margin: .6rem 0; }}
  p.note {{ color: {theme.MUTED}; font-size: .9rem; }}
</style></head><body>
  <h1>Option overlay probe</h1>
  <p>Pre-rendered, fully interactive Plotly figures — hover, zoom and legend toggles all work
     with no server. Rendered by
     <code>src/pipelines/option_probe_figures.py</code>; the write-up is
     <code>research/option_overlay_probe.md</code>.</p>
  <ul>
{rows}
  </ul>
  <p class="note">Underlying: SPY close, 250 bars, 2025-07-28 to 2026-07-24. Vol is a synthetic
     surface with a constant ATM level, not market data — see the write-up for what that does and
     does not support.</p>
</body></html>
"""
    (out_dir.parent / "index.html").write_text(html, encoding="utf-8")


def main(out_dir=DOCS_FIGURES):
    """Render every probe figure to standalone, fully interactive HTML.

    ``include_plotlyjs="directory"`` emits ``plotly.min.js`` ONCE beside the pages, and every
    figure references that single copy — rather than inlining ~3MB per figure, or depending on a
    CDN being reachable at view time. That is 10-01's stated choice. Switching to ``"cdn"`` is a
    one-word change if the vendored copy is ever judged too heavy for the repo.

    ``apply_export_theme`` is NOT optional here. A saved figure has no page div behind it, so a
    transparent background lets the browser paint its own white and the dark figure becomes
    unreadable. That is the exact failure theme.py's docstring records.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    figures = build_all()
    for name, fig in figures.items():
        theme.apply_export_theme(fig)
        fig.write_html(out_dir / f"{name}.html", include_plotlyjs="directory")
    # The index lives one level up, at docs/index.html, because that is what GitHub Pages serves
    # as the site root when the source is set to the docs/ folder.
    write_index(out_dir, figures)
    print(f"wrote {len(figures)} HTML figures to {out_dir} (+ index at {out_dir.parent / 'index.html'})")
    return figures


if __name__ == "__main__":
    main()
