"""
BUILD SITE INDEX — the published site's front door, docs/index.html.

WHY THIS EXISTS (13-01 amendment, 2026-09-26, Task 4 step 5)
-------------------------------------------------------------
Before this module, docs/index.html WAS 10-01's option-overlay probe report — the site's front
page was one piece of analysis, so 13-01's validation report was reachable only by knowing to
type the path ``/validation/`` and no report linked back to anything else. The probe report moved
to ``docs/probing/`` to make room for this: a HUB that links to every section, and that every
section links back up to.

NO FIGURES, SO NONE OF build_report's MACHINERY APPLIES
---------------------------------------------------------
This page carries no Plotly figures and renders no Markdown write-up — it is one short paragraph
plus a card per section, hand-rolled the same way ``option_probe_figures.write_index`` and
``option_ladder_probe.export_gfc_analysis``'s summary index used to be, before those two grew a
per-section write-up of their own. ``theme.page_css()`` is still the shared stylesheet, so this
page and the two it links to are one surface rather than three that happen to share a colour.

ONE ENTRY PER SECTION, SO A THIRD SECTION IS ONE DICT ENTRY
-------------------------------------------------------------
``SECTIONS`` below is the whole site map. Each entry states the question that section answers and
what data it rests on — synthetic surface vs real IBKR implied vol is the distinction a reader
most needs before trusting a number, and it is visible on this page BEFORE a single click, per the
2026-09-26 amendment's AC-7.

Run as a script:  ``python -m pipelines.build_site_index``
"""

from portutils.utils.config import PROJECT_ROOT
from portutils.viz import theme

DOCS = PROJECT_ROOT / "docs"
INDEX_PATH = DOCS / "index.html"

# The repo link in the footer — same constants build_report.py already carries, so a link to the
# source repo and a link to a source file (were one ever added here) would agree on the branch.
GITHUB_REPO = "https://github.com/danielhalm1407/Portfolio-Manager"
GITHUB_BRANCH = "feat/16-option-instruments"

# ============================================================================
# THE SITE MAP. One entry per published section: title, the question it answers, what data it
# rests on (the distinction AC-7 asks to be visible before a click), and its link.
# ============================================================================
SECTIONS = [
    {
        "title": "Option overlay probe",
        "href": "probing/index.html",
        "data": "synthetic vol surface (constant ATM level), plus a real-IV follow-on",
        "blurb": (
            "How does a protective put, put spread and collar price and behave through one "
            "probe window — the pricer's own mechanics, the smirk, term structure, and the "
            "cost of carrying a hedge, worked out step by step against a synthetic implied-vol "
            "surface. Findings 1-9."
        ),
    },
    {
        "title": "Option monetisation over the GFC",
        "href": "validation/index.html",
        "data": "REAL implied vol from IBKR, one window (2007-10 to 2009-03), IN-SAMPLE",
        "blurb": (
            "Can a monetisation policy be seen DECIDING on real data — closing a hedge at a "
            "drawdown trigger, banking the gain, and waiting for cheaper insurance before "
            "re-opening? Four structures, one policy, every fill marked on the equity path, "
            "against a blind-roll control."
        ),
    },
]


def build_site_index(sections=SECTIONS, out_path=INDEX_PATH):
    """Write the hub page linking every section. Returns the path."""
    cards = "\n".join(
        f'    <li class="card">\n'
        f'      <h2><a href="{s["href"]}">{s["title"]}</a></h2>\n'
        f'      <p>{s["blurb"]}</p>\n'
        f'      <p class="note">Data: {s["data"]}</p>\n'
        f'    </li>'
        for s in sections
    )
    # A couple of rules on top of theme.page_css(): cards read better as a plain list with no
    # bullet and a rule between them (link-list styling, borrowed from the two sub-index pages'
    # own <li> spacing, is too thin a treatment for something meant to be the FIRST thing read).
    extra_css = (
        "ul{list-style:none;padding:0;margin:2rem 0 0}"
        f"li.card{{border-top:1px solid {theme.GRID};padding:1.25rem 0}}"
        "li.card:first-child{border-top:none}"
        "li.card h2{margin:0 0 .4rem;font-size:1.15rem}"
        "li.card p{margin:.3rem 0}"
    )
    html = (
        "<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Portfolio Manager — option research</title>"
        f"<style>{theme.page_css(max_width='40rem')}{extra_css}</style></head><body>\n"
        "<h1>Portfolio Manager</h1>\n"
        "<p>A thematic-fundamental research engine that turns qualitative market narratives "
        "into portfolio tilts, feeds those tilts into a disciplined allocator, and executes "
        "the result against a real Interactive Brokers account. This corner of the project — "
        "the two sections below — is the structural commodity/hedging extension: pricing and "
        "testing option overlays as a portfolio hedge, first on a synthetic vol surface to "
        "prove the mechanics, then on real implied vol to see the policy decide.</p>\n"
        f"<ul>\n{cards}\n</ul>\n"
        f"<p class=\"note\">Every page here is generated and committed, with no server: "
        f"<a href=\"{GITHUB_REPO}/tree/{GITHUB_BRANCH}\">source on GitHub</a>.</p>\n"
        "</body></html>\n"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main():
    path = build_site_index()
    print(f"wrote {path} ({path.stat().st_size / 1024:.1f} KB, {len(SECTIONS)} sections)")
    return path


if __name__ == "__main__":
    main()
