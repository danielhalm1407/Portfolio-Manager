"""
BUILD SITE INDEX — the published site's front door, docs/index.html, and the put-hedging
research hub one level below it, docs/put-hedging/index.html.

WHY THIS EXISTS (13-01 amendment, 2026-09-26, Task 4 step 5)
-------------------------------------------------------------
Before this module, docs/index.html WAS 10-01's option-overlay probe report — the site's front
page was one piece of analysis, so 13-01's validation report was reachable only by knowing to
type the path ``/validation/`` and no report linked back to anything else. The probe report moved
to ``docs/probing/`` to make room for this: a HUB that links to every section, and that every
section links back up to.

TWO HUBS, NOT ONE (2026-11-10)
--------------------------------
The site front page used to list the three option studies directly, which made the put-hedging
work impossible to share on its own: the only link that reached all three was the project's
front door, and that page is about the whole project. There are now two levels —

* ``docs/index.html`` — the Portfolio Manager hub, one card per research PIECE;
* ``docs/put-hedging/index.html`` — the put-hedging piece's own hub, one card per study. This is
  the standalone link. The three study pages link back up to it rather than to the front door
  (``build_report``'s ``hub_href`` default), and it links up to the front door in turn.

THE PROSE LIVES IN MARKDOWN, NOT HERE (2026-11-10)
----------------------------------------------------
Each hub's text was a Python string in this file, so editing a sentence meant editing code. Both
are now Markdown write-ups under ``research/site/`` — the same arrangement as the three study
pages, so every page on the site is edited the same way. The write-up's first ``# `` line is the
page heading; each ``## [Title](link)`` opens a card; a paragraph that starts ``Data:`` is set as
the muted caveat line, so what data a section rests on — synthetic surface vs real IBKR implied
vol, the distinction a reader most needs before trusting a number — stays visible on the hub
BEFORE a single click, per the 2026-09-26 amendment's AC-7.

NO FIGURES, SO NONE OF build_report's FIGURE MACHINERY APPLIES
-----------------------------------------------------------------
These pages carry no Plotly figures — they are a paragraph or two plus a card per section. Only
``build_report``'s Markdown renderer and link rewriter are reused, so a link written in a hub
write-up resolves the same way as one written in a study write-up. ``theme.page_css()`` is still
the shared stylesheet, so the hubs and the pages they link to are one surface rather than several
that happen to share a colour.

Run as a script:  ``python -m pipelines.build_site_index``  (rebuilds BOTH hubs)
"""

import re

from portutils.utils.config import PROJECT_ROOT
from portutils.viz import theme

from pipelines.build_report import render_markdown, rewrite_links

DOCS = PROJECT_ROOT / "docs"
INDEX_PATH = DOCS / "index.html"
# The hub write-ups. research/site/ rather than research/validation/, because neither is a piece
# of validation evidence — they are the site's own navigation prose.
SITE_WRITE_UPS = PROJECT_ROOT / "research" / "site"

# The repo link in the footer — same constants build_report.py already carries, so a link to the
# source repo and a link to a source file (were one ever added here) would agree on the branch.
GITHUB_REPO = "https://github.com/danielhalm1407/Portfolio-Manager"
GITHUB_BRANCH = "master"

# ============================================================================
# THE SITE MAP. One entry per HUB page: the write-up it renders, where it is written, and the
# link back up a level (``None`` for the front door, which has nothing above it). The cards
# themselves — title, the question each section answers, what data it rests on (the distinction
# AC-7 asks to be visible before a click), and its link — are in each write-up, so a new section
# is a new ``##`` block in Markdown and a new research piece is one entry here plus its write-up.
# ============================================================================
HUBS = [
    {
        "write_up": SITE_WRITE_UPS / "portfolio_manager_hub.md",
        "out_path": INDEX_PATH,
        "up": None,
    },
    {
        "write_up": SITE_WRITE_UPS / "put_hedging_hub.md",
        "out_path": DOCS / "put-hedging" / "index.html",
        "up": ("../index.html", "Portfolio Manager"),
    },
]


def build_hub(write_up, out_path, up=None):
    """Render one hub write-up to its page. Returns the path."""
    text = write_up.read_text(encoding="utf-8")
    # The first "# " line is the page's heading AND its <title>. Taken out of the Markdown before
    # rendering so the heading is not printed twice — once by the page chrome below, once by the
    # body.
    first, _, rest = text.partition("\n")
    if not first.startswith("# "):
        raise ValueError(f"{write_up.name} must start with a '# ' heading line")
    heading = first[2:].strip()
    # The front door is titled by the project name alone; any other hub carries it as a suffix,
    # matching the study pages' "<heading> — Portfolio Manager" titles.
    title = heading if heading == "Portfolio Manager" else f"{heading} — Portfolio Manager"

    # Links in the write-up are written relative to research/site/ — so they also work in GitHub's
    # own rendering of the Markdown — and rewritten here relative to the page being written.
    page_dir = out_path.parent.relative_to(PROJECT_ROOT).as_posix()
    source_dir = write_up.parent.relative_to(PROJECT_ROOT).as_posix()
    body = rewrite_links(render_markdown(rest), source_dir=source_dir, page_dir=page_dir)
    # A paragraph starting "Data:" is a card's caveat line: set it in the muted note style, as the
    # hand-rolled cards always were, without asking the write-up to carry HTML.
    body = re.sub(r"<p>Data:", '<p class="note">Data:', body)

    # A couple of rules on top of theme.page_css(): cards read better with a rule between them
    # and a heading sized as a link title rather than a section break (link-list styling,
    # borrowed from the two sub-index pages' own <li> spacing, is too thin a treatment for
    # something meant to be the FIRST thing read).
    extra_css = (
        f"h2{{border-top:1px solid {theme.GRID};padding-top:1.25rem;margin:1.75rem 0 .4rem;"
        "font-size:1.15rem}"
        "h2 + p{margin-top:.3rem}"
        "p.meta{margin:0 0 1rem}"
    )
    # The link back UP a level, so a reader who arrives at this hub from a shared link can still
    # reach the rest of the project.
    up_html = (f'<p class="note meta"><a href="{up[0]}">&larr; {up[1]}</a></p>\n' if up else "")
    source_rel = write_up.relative_to(PROJECT_ROOT).as_posix()
    html = (
        "<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>"
        f"<style>{theme.page_css(max_width='40rem')}{extra_css}</style></head><body>\n"
        f"{up_html}"
        f"<h1>{heading}</h1>\n"
        f"{body}\n"
        f"<p class=\"note\">Every page here is generated and committed, with no server: "
        f"<a href=\"{GITHUB_REPO}/tree/{GITHUB_BRANCH}\">source on GitHub</a>. This page is "
        f"generated from <a href=\"{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{source_rel}\">"
        f"{source_rel}</a> by <code>python -m pipelines.build_site_index</code>.</p>\n"
        "</body></html>\n"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def build_site_index(hubs=HUBS):
    """Write every hub page. Returns their paths."""
    return [build_hub(**hub) for hub in hubs]


def main():
    paths = build_site_index()
    for path in paths:
        print(f"wrote {path} ({path.stat().st_size / 1024:.1f} KB)")
    return paths


if __name__ == "__main__":
    main()
