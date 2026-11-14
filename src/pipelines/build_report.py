"""
BUILD REPORT — a research write-up and its figures, published as ONE static HTML page.

WHY THIS EXISTS (Phase 10, Track A / plan 10-01)
------------------------------------------------
``option_probe_figures.main()`` publishes five standalone figure pages and a link list. A reader
following that link list lands on a chart with no argument around it: the findings, the caveats and
the next steps all live in ``research/validation/option_ladder_prices_probe.md`` and were never published. This
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
import html as html_lib
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
WRITE_UP = PROJECT_ROOT / "research" / "validation" / "option_ladder_prices_probe.md"
DOCS = PROJECT_ROOT / "docs"
# docs/probing/ since 13-01's 2026-09-26 amendment. docs/index.html is now the site HUB, which
# links down to this report and to the 13-01 validation report; it is written by
# pipelines/build_site_index.py and must not be overwritten here. Since 2026-11-10 the front door
# links to the put-hedging hub (docs/put-hedging/), and THAT hub links down to this report.
REPORT_PATH = DOCS / "probing" / "index.html"

# Which figure is dropped in after which section, keyed by a distinctive SUBSTRING of the heading
# rather than the whole line — headings carry em-dashes and long subtitles that would otherwise make
# this map fragile for cosmetic reasons. A key that matches nothing is an ERROR (see _split_sections):
# silently publishing a figure under the wrong finding is worse than failing the build.
FIGURE_AT = {
    "reproduces the smirk": "iv_smirk",            # the surface's shape, swept across strikes
    "Protection costs fall": "put_paths",          # the ladder valued through time
    "How the surface is anchored": "iv_paths",     # the two moneyness references overlaid
    "Struck where a rolling hedge": "drawdown_episode",  # struck at the peak and carried
    "rolled book": "overlay_values",               # the three structures through the simulator
    "What implied volatility actually did": "real_iv_history",   # the measured series, not synthetic
    "Repricing with the real volatility": "put_paths_market",    # real level + current spot
}

# Where a source link should point once the page is served from GitHub Pages. A link like
# `../src/portutils/...` resolves inside the repo but to nothing on the published site, so those
# become blob URLs. Branch is a constant because the report is published per branch, and `main`
# would 404 for anything not yet merged.
GITHUB_REPO = "https://github.com/danielhalm1407/Portfolio-Manager"
GITHUB_BRANCH = "master"

# The default page chrome — 10-01's probe report. Each is a `build_report` argument, so a second
# report can replace the ones that are specific to its own evidence and inherit the rest.
DEFAULT_TITLE = "Testing option price dynamics over recent drawdowns — Portfolio Manager"
DEFAULT_HEADING = "Testing option price dynamics over recent drawdowns"

# `{source_link}` and `{built}` are filled by build_report; the rest is prose. Written as a template
# rather than assembled in the page f-string so a caller can replace the sentence without having to
# reproduce the link markup.
DEFAULT_HEADER_NOTE = (
    "Figures are fully interactive — hover, zoom and legend toggles all work, with no server. "
    "Generated from {source_link} by <code>python -m pipelines.build_report</code> on {built}.")

# The footer caveat. SPECIFIC TO 10-01: its figures price off the synthetic surface. 13-01's
# validation pages price off real IBKR implied vol and MUST override this — a caveat that is wrong
# is worse than no caveat, and this string is the reason the function could not serve two reports.
DEFAULT_FOOTER_NOTE = (
    "Portfolio Manager — thematic-fundamental research engine. "
    "Vol is a SYNTHETIC surface with a constant ATM level, not market data; see the findings above "
    "for what that does and does not support.")

# The vendored library, relative to the report (which sits at docs/probing/index.html). ONE copy
# serves the whole site from docs/assets/; a page nested one level deeper (a per-figure page under
# a section's figures/) needs ../../assets/... instead, which is why build_report takes this as an
# argument rather than only reading this constant.
PLOTLY_SRC = "../assets/plotly.min.js"


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


def rewrite_links(html, source_dir="research", page_dir="docs"):
    """Point every relative link somewhere that resolves on the PUBLISHED page.

    Links in the write-up are relative to its own directory (``research/``). Once the page is
    served from ``docs/``, three cases need different answers:

    * ``http(s)://`` and in-page ``#anchors`` — already correct, left alone;
    * a target inside ``docs/`` (e.g. ``../docs/figures/x.html``) — rewritten relative to the
      report, so the standalone figure pages keep working with no network;
    * anything else (source files, PAUL plans) — a GitHub blob URL, with any ``#L123`` line anchor
      carried across, because GitHub renders exactly that.

    ``page_dir`` is the published page's OWN directory, repo-relative. It is not a detail: the
    second case is a path computed relative to the page, so a page at ``docs/probing/index.html``
    and one at ``docs/index.html`` need different answers for the same link. This argument existing
    is what stopped 13-01's move of the probe report under ``docs/probing/`` from silently turning
    the write-up's own ``docs/README.md`` link into a 404 — it was caught by the link check, and the
    fix belongs here rather than in the write-up, which is correct as written and is shared with
    GitHub's own rendering of it.
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
        # A target already inside docs/ is served on the SITE: make it relative to this page's own
        # directory. relpath rather than stripping the "docs/" prefix, which was correct only while
        # the report sat at docs/index.html and produced a link one level too shallow once it moved.
        if resolved.startswith("docs/"):
            return f'href="{posixpath.relpath(resolved, page_dir)}{anchor}"'
        # Everything else lives in the repo but not on the site — send the reader to GitHub.
        return f'href="{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{resolved}{anchor}"'

    return re.sub(r'href="([^"]+)"', _fix, html)


def _split_sections(html, figure_at=FIGURE_AT, source_name=None, levels=(2,)):
    """Return the HTML with a placeholder inserted after each mapped section.

    The document is cut at heading boundaries: a figure belongs AFTER the whole section that
    discusses it, not immediately under its heading, so the reader has the argument before the
    evidence. Every key in ``figure_at`` must match exactly one heading — an unmatched key means the
    write-up was edited and this map was not, which raises rather than publishing silently.

    ``levels`` are the heading levels a section is cut at. The default, h2 only, is every page's
    behaviour before 2026-11-14. A page whose sections nest deeper (the moneyness page, h1 to h6)
    passes all of its levels, so a figure lands at the end of its OWN sub-section, before the
    next heading of any level, rather than at the end of the whole top-level section.
    """
    tags = "|".join(f"h{n}" for n in levels)
    # Cut before each heading at the chosen levels, keeping the delimiter with the section it opens.
    parts = re.split(rf"(?=<(?:{tags})[ >])", html)
    matched = set()
    out = []
    for part in parts:
        out.append(part)
        # Only the heading line is searched for the key, so a "Finding 7" mentioned in body text
        # cannot pull the figure into the wrong section.
        opened = re.match(rf"<({tags})[ >]", part)
        heading = part.split(f"</{opened.group(1)}>", 1)[0] if opened else ""
        for key, stem in figure_at.items():
            if key in heading:
                # A key may map to ONE stem or to a LIST of them (13-02.1): the optimisation
                # page's mechanism section carries one stacked panel per window, in order. A
                # plain string behaves exactly as before, so the other two pages are unaffected.
                for one in ([stem] if isinstance(stem, str) else list(stem)):
                    out.append(f"<!--FIGURE:{one}-->")
                matched.add(key)
    # A key matching MORE than one heading inserts its figures twice with the same div id, and
    # the browser then draws each figure once and leaves the duplicate as an empty box. Raise.
    for key in figure_at:
        hits = [h for h in re.findall(rf"<({tags})[ >](.*?)</\1>", html, flags=re.S)
                if key in h[1]]
        if len(hits) > 1:
            raise KeyError(f"figure key {key!r} matches {len(hits)} headings in "
                           f"{source_name or WRITE_UP.name}; make it unique to one")
    missing = set(figure_at) - matched
    if missing:
        # Name the write-up that was ACTUALLY rendered. This used to report WRITE_UP.name — the
        # module default — which sent a reader to the probe write-up whichever of the three
        # sections had really gone out of sync. Only sections are searched, so a heading that
        # exists but sits at h3 reports as missing too, which is the common case and worth
        # saying: a figure belongs after a whole section, not after a sub-heading inside one.
        raise KeyError(
            f"headings not found in {source_name or WRITE_UP.name}: {sorted(missing)} — either "
            "the write-up was edited without updating its figure map, or the heading exists but "
            f"is not at a split level (sections are split at h{', h'.join(map(str, levels))})")
    return "".join(out)


# ============================================================================
# CONTENTS (2026-11-13)
# ============================================================================

def heading_slug(text, seen):
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens, and a
    ``-1``, ``-2`` suffix on repeats.

    The same rule GitHub applies when it renders the Markdown, so a ``#link`` written into a
    write-up resolves both on GitHub and on the published page. ``seen`` is the running count per
    slug for this page, shared across every heading so repeats are numbered in document order.
    """
    plain = re.sub(r"<[^>]+>", "", text)
    plain = html_lib.unescape(plain).strip().lower()
    # Keep letters, digits, underscores, hyphens and spaces; everything else (×, %, commas,
    # apostrophes) is dropped, as GitHub does.
    slug = re.sub(r"[^\w\- ]", "", plain).replace(" ", "-")
    n = seen.get(slug, 0)
    seen[slug] = n + 1
    return slug if n == 0 else f"{slug}-{n}"


CONTENTS_ID = "contents"
BACK_LINK = f'<p class="back"><a href="#{CONTENTS_ID}">&uarr; Back to contents</a></p>'


def add_contents(body, top=2, depth=2):
    """Give every heading from h``top`` down ``depth`` levels an anchor, put a linked, nested
    Contents list at the top of the body, and add a "Back to contents" link directly below each
    top-level heading and again at the end of its section.

    Generated rather than written into the Markdown, so the list cannot drift from the headings: a
    heading renamed in the write-up renames its contents entry on the next build. Headings below
    the top level are listed nested under their parent but carry no back links of their own,
    since they sit inside a section that already has two.

    The defaults (h2 and h3) are every page's behaviour before 2026-11-14. The moneyness page
    passes ``top=1, depth=6``, because its outline nests six levels below the page title and
    HTML has no h7 (the page title itself is lifted out of the Markdown by ``build_report``).
    """
    seen = {CONTENTS_ID: 1}
    entries = []
    levels = "".join(str(n) for n in range(top, min(top + depth, 7)))

    def _anchor(match):
        level, inner = match.group(1), match.group(2)
        slug = heading_slug(inner, seen)
        entries.append((int(level), slug, re.sub(r"<[^>]+>", "", inner)))
        tag = f'<h{level} id="{slug}">{inner}</h{level}>'
        # The link sits under the top-level heading itself, so a reader who lands mid-page can get
        # back up at once without scrolling to the section's end.
        return tag + BACK_LINK if int(level) == top else tag

    body = re.sub(rf"<h([{levels}])>(.*?)</h\1>", _anchor, body, flags=re.S)

    # The closing link for each section: before every top-level heading except the first, and at
    # the very end. The preamble above the first one belongs to no section and gets none.
    parts = re.split(rf"(?=<h{top} id=)", body)
    if len(parts) > 1:
        body = parts[0] + "".join(p + BACK_LINK for p in parts[1:])

    if not entries:
        return body
    # The nested list, built with a stack of open levels: a deeper heading opens a sub-list under
    # the item before it, a shallower one closes lists back down to its own level.
    items, stack = [], []
    for level, slug, text in entries:
        link = f'<a href="#{slug}">{text}</a>'
        if not stack:
            stack.append(level)
        elif level > stack[-1]:
            items.append("<ul>")
            stack.append(level)
        else:
            items.append("</li>")
            while len(stack) > 1 and level < stack[-1]:
                items.append("</ul></li>")
                stack.pop()
        items.append(f"<li>{link}")
    items.append("</li>")
    while len(stack) > 1:
        items.append("</ul></li>")
        stack.pop()
    toc = (f'<nav class="toc"><h2 id="{CONTENTS_ID}">Contents</h2>\n<ul>'
           + "".join(items) + "</ul></nav>\n")
    return toc + body


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
  /* Deeper levels, for a page whose outline nests below h3 (the moneyness page, 2026-11-14).
     A body h1 is a top-level section of such a page, not the page title, which sits in the
     header outside <main>; it is ruled heavier than an h2 so the outline reads at a glance. */
  main h1 {{ font-size: 1.6rem; margin: 4rem 0 1rem; border-top: 2px solid {grid};
            padding-top: 1.75rem; }}
  h4 {{ font-size: 1.02rem; margin: 1.75rem 0 .5rem; }}
  h5 {{ font-size: .96rem; margin: 1.5rem 0 .5rem; color: {ink}; }}
  h6 {{ font-size: .92rem; margin: 1.25rem 0 .5rem; font-style: italic; color: {ink}; }}
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
  /* Display equations. Typeset in the page's own CSS with unicode symbols rather than by
     loading KaTeX or MathJax: every page here vendors its one copy of plotly.min.js precisely so
     it renders with no network, and a maths library pulled from a CDN would give that up for a
     handful of formulas. Serif, because a formula set in the body sans-serif reads as code. */
  .math {{ font-family: "Latin Modern Math", "Cambria Math", Cambria, Georgia, serif;
          font-size: 1.05rem; line-height: 1.9; text-align: center;
          background: rgba(255,255,255,.04); border-left: 3px solid {grid};
          padding: 1rem 1.25rem; margin: 1.5rem 0; overflow-x: auto; }}
  /* The "where ..." line under an equation — smaller, left-aligned, same block. */
  .math .where {{ display: block; font-family: inherit; font-size: .88rem; text-align: left;
                 color: {muted}; margin-top: .75rem; }}
  .meta {{ color: {muted}; font-size: .9rem; }}
  /* The contents list: a quiet block under the title, not a second heading competing with it. */
  nav.toc {{ margin: 0 0 2.5rem; }}
  nav.toc h2 {{ border-top: none; margin-top: 0; padding-top: 0; }}
  nav.toc ul {{ margin: .25rem 0; padding-left: 1.25rem; }}
  nav.toc li {{ margin: .2rem 0; }}
  nav.toc ul ul {{ font-size: .92rem; }}
  /* Back-to-contents links: small and muted, so they help navigation without reading as prose. */
  p.back {{ font-size: .82rem; margin: .25rem 0 1rem; }}
  p.back a {{ color: {muted}; text-decoration: none; }}
  p.back a:hover {{ text-decoration: underline; }}
  /* Anchored headings stop clear of the top edge when jumped to. */
  h1[id], h2[id], h3[id], h4[id], h5[id], h6[id] {{ scroll-margin-top: 1rem; }}
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


def fill_values(text, values):
    """Replace every ``{{name}}`` in ``text`` with ``values[name]``; raise on any left over.

    Only the double-brace form is a token, so ordinary braces in prose or code are untouched.
    """
    for name, value in (values or {}).items():
        text = text.replace("{{" + name + "}}", str(value))
    left = sorted(set(re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", text)))
    if left:
        raise ValueError(f"unresolved write-up values: {', '.join(left)}")
    return text


def build_report(figures=None, write_up=WRITE_UP, out_path=REPORT_PATH, figure_at=None,
                 plotly_src=PLOTLY_SRC, title=DEFAULT_TITLE, heading=DEFAULT_HEADING,
                 header_note=DEFAULT_HEADER_NOTE, footer_note=DEFAULT_FOOTER_NOTE,
                 source_href=None, extra_html=None, hub_href="../put-hedging/index.html",
                 hub_label="Hedging an equity portfolio with put options", values=None,
                 section_levels=(2,), contents_top=2, contents_depth=2):
    """Render the write-up with its figures inline and write the report. Returns the path.

    SERVES TWO REPORTS (13-01 amendment, 2026-09-26). 10-01's option-overlay probe and 13-01's GFC
    monetisation validation are ONE implementation with different arguments, for the same reason the
    figure builders are: the parts most costly to duplicate are the heading -> figure map's
    raise-on-mismatch guard, the link rewriting and the CSS, and those are exactly the parts that
    would drift if there were two copies. Every argument defaults to 10-01's literal, so calling
    this with no arguments reproduces that page unchanged.

    ``plotly_src``   where the vendored library sits relative to THIS page — one level up for a
                     section index (``../assets/...``), two for a page inside a ``figures/``
                     directory. The single most likely thing to get wrong when a page moves.
    ``title`` / ``heading`` / ``header_note`` / ``footer_note``
                     the page's own prose. ``header_note`` is a template taking ``{source_link}``
                     and ``{built}``; ``footer_note`` must be overridden by any report whose vol is
                     not synthetic, because the default states that it is.
    ``source_href``  the write-up's path for the "generated from" link, relative to the repo root.
                     Defaults to ``write_up``'s own path.
    ``extra_html``   ``{placeholder: html}`` spliced into the rendered body after the figures. The
                     GFC report's summary table arrives this way, so its numbers are GENERATED from
                     the run rather than typed into the Markdown — the same rule as the prose, one
                     level down.
    ``hub_href``     the link back up to the site hub, so a reader who arrives at one section from a
                     shared link can reach the other. ``None`` omits it. Since 2026-11-10 it points
                     at the put-hedging research hub (docs/put-hedging/), not the site front door:
                     all three study pages belong to that piece, and that hub links up in turn.
    ``hub_label``    the text of that link — the name of the hub it leads to.
    ``section_levels`` / ``contents_top`` / ``contents_depth``
                     how deep the page's outline goes: the heading levels figures are placed
                     against, and the levels the contents list covers. Defaults are h2 sections
                     and an h2/h3 contents list; see ``_split_sections`` and ``add_contents``.
    ``values``       ``{name: text}`` substituted for ``{{name}}`` tokens in the write-up BEFORE
                     it is rendered, so a number or a whole Markdown table in the prose is filled
                     from the run that produced it (2026-11-13: "whenever we change the results,
                     our commentary should update"). A token left unresolved RAISES, naming it,
                     because a page printing ``{{spy_return}}`` is a broken page.

    THE WRITE-UP'S OWN TITLE WINS (2026-11-10). If the write-up opens with a ``# `` line, that line
    becomes the page heading and ``<title>`` and is removed from the body, overriding ``title`` /
    ``heading``. Before this, the heading argument AND the Markdown's own h1 were both printed, so
    every page showed its title twice and editing the Markdown title changed only the second one.

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

    # The page's own directory, repo-relative, so links into docs/ resolve from wherever this page
    # is written rather than from an assumed docs/index.html. Same treatment for the write-up's own
    # directory: it defaulted to a hardcoded "research", which was silently correct only because
    # every write-up so far sat directly in research/ — the GFC write-up sits one level deeper, at
    # research/validation/, and a link written relative to THAT (e.g. "../../docs/...") needs the
    # matching source_dir or it resolves against the wrong base and 404s past the link check.
    page_dir = out_path.parent.relative_to(PROJECT_ROOT).as_posix()
    source_dir = write_up.parent.relative_to(PROJECT_ROOT).as_posix()
    text = write_up.read_text(encoding="utf-8")
    # The write-up's own "# " line is the page title (see the docstring): lifted out so it is
    # printed once, by the header below, and so editing it in the Markdown is the whole change.
    first, _, rest = text.partition("\n")
    if first.startswith("# "):
        heading = first[2:].strip()
        title = f"{heading} — Portfolio Manager"
        text = rest
    text = fill_values(text, values)
    body = _split_sections(rewrite_links(render_markdown(text),
                                         source_dir=source_dir, page_dir=page_dir),
                           figure_at=figure_at, source_name=write_up.name,
                           levels=section_levels)
    # Wrap tables so the overflow rule above has something to scroll.
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")

    # Swap each placeholder for its rendered figure. Done after the prose is complete so a figure's
    # own markup can never be touched by the link rewriting or the table wrapping above.
    divs = _figure_divs(figures)
    for name, div in divs.items():
        body = body.replace(f"<!--FIGURE:{name}-->", f'<div class="figure">{div}</div>')

    # GENERATED HTML that is not a figure — the GFC report's summary table. Spliced here, after the
    # figures, for the same reason they are: its markup must not be walked by the link rewriting or
    # the table wrapping above, both of which would find real anchors and a real <table> inside it.
    # A placeholder that matches nothing is NOT an error, unlike a figure's: a caller may hand the
    # same dict to a write-up that has not asked for that block.
    for placeholder, extra in (extra_html or {}).items():
        body = body.replace(placeholder, extra)

    # The contents list and the back-to-contents links. Done LAST, once the figures and generated
    # tables are in place, so each section's closing link lands after its figures rather than
    # between the prose and the evidence.
    body = add_contents(body, top=contents_top, depth=contents_depth)

    built = _dt.date.today().isoformat()
    css = _CSS.format(page_bg=theme.PAGE_BG, ink=theme.INK, grid=theme.GRID,
                      muted=theme.MUTED, link=theme.CATEGORICAL[0])

    # The "generated from" link points at the write-up ON GITHUB, never at a relative path: the page
    # is served out of docs/ and the Markdown is not published, so a relative link would 404. Same
    # reasoning as rewrite_links above, which does this for every link inside the prose.
    source_rel = source_href or write_up.relative_to(PROJECT_ROOT).as_posix()
    source_link = f'<a href="{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{source_rel}">{source_rel}</a>'
    note = header_note.format(source_link=source_link, built=built)

    # The link back UP to the hub. A hub that only points down is half a hub: a reader arriving here
    # from a shared link would otherwise have no way to discover the other section.
    up = f'<p class="meta"><a href="{hub_href}">&larr; {hub_label}</a></p>' if hub_href else ""

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
<script src="{plotly_src}"></script>
</head><body>
<header class="page">
  {up}
  <h1>{heading}</h1>
  <p class="meta">{note}</p>
</header>
<main>
{body}
</main>
<footer>
  {footer_note}
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
