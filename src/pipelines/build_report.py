"""
BUILD REPORT — a research write-up and its figures, published as ONE static HTML page.

WHY THIS EXISTS (Phase 10, Track A / plan 10-01)
------------------------------------------------
``option_probe_figures.main()`` publishes five standalone figure pages and a link list. A reader
following that link list lands on a chart with no argument around it: the findings, the caveats and
the next steps all live in ``research/option_overlay_probe.md`` and were never published. This
module renders that write-up to HTML and splices each figure in at the finding it belongs to, so
one page carries the analysis AND its evidence.

THE ONE RULE THAT MATTERS: THE PROSE IS NEVER RETYPED
-----------------------------------------------------
Every sentence on the page comes from the Markdown file. Nothing here paraphrases the analysis, and
there is no second copy to drift. That is the same one-implementation rule the figure builders
already follow (``viz/theme.py`` documents it for ``rebalance_study.py``). The consequence, made
deliberate: if a mapped heading is renamed in the write-up, this module RAISES rather than quietly
publishing a page with a figure in the wrong place.

NO BUILD STEP, NO SERVER, NO CDN
--------------------------------
The page is plain HTML with inline CSS, and the figures are Plotly divs that share the single
``plotly.min.js`` already vendored in ``docs/figures/`` (10-01's stated choice — 4.2 MB committed
once). ``include_plotlyjs=False`` on every figure keeps the page itself small. GitHub Pages serves
``docs/`` off the default branch with no workflow, which is why there is nothing to build.

MARKDOWN VIA markdown-it-py
---------------------------
Decided 2026-09-20 (10-01's blocking checkpoint): ``markdown-it-py`` is DECLARED in
``pyproject.toml`` rather than relied on transitively — the objection ``pricing.py`` records about
scipy applies to any package the repo uses but does not declare. It is pure Python, CommonMark
correct, and handles the write-up's tables, which is precisely where a hand-rolled converter breaks.

Run as a script:  ``python -m pipelines.build_report``
(or let ``python -m pipelines.option_probe_figures`` do both in one pass)
"""

import datetime as _dt
import posixpath
import re

from markdown_it import MarkdownIt

from portutils.utils.config import PROJECT_ROOT
from portutils.viz import theme


# ============================================================================
# WHAT IS PUBLISHED, AND WHERE EACH FIGURE GOES
# ============================================================================

# The write-up this page renders, and the page it writes. `docs/` rather than `outputs/` because
# `outputs/` is gitignored (.gitignore:37) and a published page must be committed to be served.
WRITE_UP = PROJECT_ROOT / "research" / "option_overlay_probe.md"
DOCS = PROJECT_ROOT / "docs"
REPORT_PATH = DOCS / "index.html"

# Which figure is dropped in after which section, keyed by a distinctive SUBSTRING of the heading
# rather than the whole line — headings carry em-dashes and long subtitles that would otherwise make
# this map fragile for cosmetic reasons. A key that matches nothing is an ERROR (see _split_sections):
# silently publishing a figure under the wrong finding is worse than failing the build.
FIGURE_AT = {
    "Finding 2": "iv_smirk",           # the surface's shape — the section about the smirk
    "Finding 3": "put_paths",          # the cost of protection — the ladder valued through time
    "Finding 5": "iv_paths",           # the v1/v2 gap — the two moneyness references overlaid
    "Finding 6": "drawdown_episode",   # struck at the peak and carried
    "Finding 7": "overlay_values",     # the rolled structures through the simulator
    "Finding 8": "real_iv_history",    # SPY's real IV over the probe window, not synthetic
    "Finding 9": "put_paths_market",   # the ladder repriced with real vol level + current spot
}

# Where a source link should point once the page is served from GitHub Pages. A link like
# `../src/portutils/...` resolves inside the repo but to nothing on the published site, so those
# become blob URLs. Branch is a constant because the report is published per branch, and `main`
# would 404 for anything not yet merged.
GITHUB_REPO = "https://github.com/danielhalm1407/Portfolio-Manager"
GITHUB_BRANCH = "feat/16-option-instruments"

# The vendored library, relative to the report (which sits at docs/index.html).
PLOTLY_SRC = "figures/plotly.min.js"


# ============================================================================
# MARKDOWN -> HTML
# ============================================================================

def render_markdown(text):
    """Render CommonMark + tables to an HTML fragment.

    ``html=True`` because the write-up already contains inline HTML (``<br>`` inside table cells,
    ``~~strikethrough~~`` spans); escaping it would publish the markup as literal text.
    """
    md = MarkdownIt("commonmark", {"html": True, "linkify": True}).enable(["table", "strikethrough"])
    return md.render(text)


def rewrite_links(html, source_dir="research"):
    """Point every relative link somewhere that resolves on the PUBLISHED page.

    Links in the write-up are relative to its own directory (``research/``). Once the page is
    served from ``docs/``, three cases need different answers:

    * ``http(s)://`` and in-page ``#anchors`` — already correct, left alone;
    * a target inside ``docs/`` (e.g. ``../docs/figures/x.html``) — rewritten relative to the
      report, so the standalone figure pages keep working with no network;
    * anything else (source files, PAUL plans) — a GitHub blob URL, with any ``#L123`` line anchor
      carried across, because GitHub renders exactly that.
    """
    def _fix(match):
        href = match.group(1)
        # Absolute URLs, mail links and pure fragments are already valid on the published page.
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        # Split the line anchor off before normalising — "#L104" is not part of the path.
        path, _, anchor = href.partition("#")
        anchor = f"#{anchor}" if anchor else ""
        # Resolve the link against the write-up's own directory to get a repo-relative path.
        resolved = posixpath.normpath(posixpath.join(source_dir, path))
        # A target already inside docs/ is served beside the report: make it report-relative.
        if resolved.startswith("docs/"):
            return f'href="{resolved[len("docs/"):]}{anchor}"'
        # Everything else lives in the repo but not on the site — send the reader to GitHub.
        return f'href="{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{resolved}{anchor}"'

    return re.sub(r'href="([^"]+)"', _fix, html)


def _split_sections(html, figure_at=FIGURE_AT):
    """Return the HTML with a placeholder inserted after each mapped section.

    The document is cut at ``<h2>`` boundaries: a figure belongs AFTER the whole section that
    discusses it, not immediately under its heading, so the reader has the argument before the
    evidence. Every key in ``figure_at`` must match exactly one heading — an unmatched key means the
    write-up was edited and this map was not, which raises rather than publishing silently.
    """
    # Cut before each <h2, keeping the delimiter with the section it opens.
    parts = re.split(r"(?=<h2)", html)
    matched = set()
    out = []
    for part in parts:
        out.append(part)
        # Only the heading line is searched for the key, so a "Finding 7" mentioned in body text
        # cannot pull the figure into the wrong section.
        heading = part.split("</h2>", 1)[0] if part.startswith("<h2") else ""
        for key, stem in figure_at.items():
            if key in heading:
                out.append(f"<!--FIGURE:{stem}-->")
                matched.add(key)
    missing = set(figure_at) - matched
    if missing:
        raise KeyError(
            f"headings not found in {WRITE_UP.name}: {sorted(missing)} — the write-up was edited "
            "without updating FIGURE_AT in build_report.py")
    return "".join(out)


# ============================================================================
# THE PAGE
# ============================================================================

# Inline CSS, no stylesheet and no framework: GitHub Pages serves this directory as static files,
# so anything needing a bundler would need CI that does not exist. Palette from theme.py, so the
# page and the figures it carries are one surface.
_CSS = """
  :root {{ color-scheme: dark; }}
  body {{ background: {page_bg}; color: {ink};
         font: 16px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; padding: 3rem 1.25rem 6rem; }}
  /* Prose is capped at a readable measure; the .figure blocks below break out of it. */
  main {{ max-width: 46rem; margin: 0 auto; }}
  h1 {{ font-size: 1.9rem; line-height: 1.25; margin: 0 0 .5rem; }}
  h2 {{ font-size: 1.35rem; margin: 3rem 0 .75rem; border-top: 1px solid {grid};
       padding-top: 1.5rem; }}
  h3 {{ font-size: 1.1rem; margin: 2rem 0 .5rem; }}
  a {{ color: {link}; }}
  code {{ background: rgba(255,255,255,.06); padding: .1em .35em; border-radius: 3px;
         font-size: .9em; }}
  pre {{ background: rgba(255,255,255,.06); padding: 1rem; border-radius: 6px;
        overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ border-left: 3px solid {grid}; margin: 1.5rem 0; padding: .25rem 0 .25rem 1rem;
               color: {muted}; }}
  /* Tables scroll inside their own box rather than forcing the page sideways on a phone. */
  .table-wrap {{ overflow-x: auto; margin: 1.25rem 0; }}
  table {{ border-collapse: collapse; font-size: .92rem; min-width: 100%; }}
  th, td {{ border: 1px solid {grid}; padding: .4rem .6rem; text-align: left;
           white-space: nowrap; }}
  th {{ background: rgba(255,255,255,.05); }}
  /* Figures BREAK OUT of the 46rem prose measure. A chart is read across, not down: at prose
     width the legends collide with a secondary axis and the date axis compresses to unreadable.
     The negative-margin trick centres a block wider than its parent without extra wrappers. */
  .figure {{ width: min(96vw, 1600px);
            margin: 2.5rem 0 2.5rem calc(50% - min(48vw, 800px)); }}
  /* Plotly writes an inline width onto its div; override it so the chart tracks the container
     (paired with responsive:true in the embed, which re-lays-out on window resize). */
  .figure .plotly-graph-div {{ width: 100% !important; }}
  .meta {{ color: {muted}; font-size: .9rem; }}
  header.page {{ max-width: 46rem; margin: 0 auto 2rem; }}
  footer {{ max-width: 46rem; margin: 4rem auto 0; color: {muted}; font-size: .85rem;
           border-top: 1px solid {grid}; padding-top: 1rem; }}
  @media (max-width: 40rem) {{ body {{ padding: 2rem 1rem 4rem; }} h1 {{ font-size: 1.5rem; }} }}
"""


def _figure_divs(figures):
    # Render each figure ONCE to a bare div. include_plotlyjs=False because the page loads the
    # vendored copy a single time below; inlining it per figure would add ~4 MB five times over.
    # config responsive=True registers Plotly's resize handler, so the chart re-lays-out when the
    # window changes rather than keeping the width it was first rendered at.
    return {name: fig.to_html(full_html=False, include_plotlyjs=False, div_id=f"fig-{name}",
                              config={"responsive": True})
            for name, fig in figures.items()}


def build_report(figures=None, write_up=WRITE_UP, out_path=REPORT_PATH, figure_at=None):
    """Render the write-up with its figures inline and write the report. Returns the path.

    ``figures`` is ``{stem: go.Figure}``; when omitted the builders are imported and run here, so
    the module works standalone. The import is deferred to function scope because
    ``option_probe_figures.main()`` calls back into THIS module — deferring breaks the cycle and
    lets the caller pass figures it has already built rather than building them twice.

    ``figure_at`` overrides the heading -> figure map; threaded through rather than read from the
    module global so the mismatch guard can be exercised by a test without monkeypatching.
    """
    figure_at = FIGURE_AT if figure_at is None else figure_at
    if figures is None:
        from pipelines.option_probe_figures import build_all
        figures = build_all()

    # The same dark stamp the standalone pages get: a saved document has no page div behind it, so
    # a transparent figure would let the browser paint white under it (theme.py's docstring).
    for fig in figures.values():
        theme.apply_export_theme(fig)
        # The report page renders a figure wider still than the interactive window, so the legend
        # needs the export gap for the same reason the standalone pages do.
        theme.apply_export_spacing(fig)

    body = _split_sections(rewrite_links(render_markdown(write_up.read_text(encoding="utf-8"))),
                           figure_at=figure_at)
    # Wrap tables so the overflow rule above has something to scroll.
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")

    # Swap each placeholder for its rendered figure. Done after the prose is complete so a figure's
    # own markup can never be touched by the link rewriting or the table wrapping above.
    divs = _figure_divs(figures)
    for name, div in divs.items():
        body = body.replace(f"<!--FIGURE:{name}-->", f'<div class="figure">{div}</div>')

    built = _dt.date.today().isoformat()
    css = _CSS.format(page_bg=theme.PAGE_BG, ink=theme.INK, grid=theme.GRID,
                      muted=theme.MUTED, link=theme.CATEGORICAL[0])
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Option overlay probe — Portfolio Manager</title>
<style>{css}</style>
<script src="{PLOTLY_SRC}"></script>
</head><body>
<header class="page">
  <h1>Option overlay probe</h1>
  <p class="meta">Figures are fully interactive — hover, zoom and legend toggles all work, with no
     server. Generated from
     <a href="{GITHUB_REPO}/blob/{GITHUB_BRANCH}/research/option_overlay_probe.md">research/option_overlay_probe.md</a>
     by <code>python -m pipelines.build_report</code> on {built}.</p>
</header>
<main>
{body}
</main>
<footer>
  Portfolio Manager — thematic-fundamental research engine.
  Vol is a SYNTHETIC surface with a constant ATM level, not market data; see the findings above for
  what that does and does not support.
</footer>
</body></html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main():
    path = build_report()
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB, "
          f"{len(FIGURE_AT)} figures inline, plotly.min.js shared from {PLOTLY_SRC})")
    return path


if __name__ == "__main__":
    main()
