# `docs/` — the static, published surface

Everything in here is **generated** and **committed**. It is the GitHub Pages root: with Pages
pointed at *"Deploy from a branch → main → /docs"*, `index.html` becomes the site's front page
and `figures/` its content.

## What is on the page (added 2026-09-20, plan 10-01)

`index.html` is the **narrative report**, not a link list: the whole of
[`research/option_overlay_probe.md`](../research/option_overlay_probe.md) — the question, all seven
findings, the pricing call-chain section, the caveats and the scoped-but-unbuilt next steps — with
each figure embedded inline at the finding it belongs to (the smirk under Finding 2, the valued
ladder under Finding 3, the v1/v2 vols under Finding 5, the drawdown episode under Finding 6, the
rolled structures under Finding 7).

Two properties are deliberate and worth keeping:

- **The prose is never retyped.** [`build_report.py`](../src/pipelines/build_report.py) RENDERS the
  Markdown; it does not paraphrase it. Edit the write-up, rebuild, and the page follows. There is no
  second copy to drift.
- **A renamed heading fails the build.** The heading → figure map raises rather than publishing a
  figure under the wrong finding. If you rename a `## Finding N` heading, update `FIGURE_AT`.

The five standalone per-figure pages under `figures/` stay: the write-up links to them, and they are
what the "Published figures" section points at.

## Why here and not `outputs/`

`outputs/` is gitignored ([`.gitignore:37`](../.gitignore)) — correct for scenario runs, which are
scratch. A published page has to be committed to be served, so it needs a directory git actually
tracks. `docs/` is also the one folder GitHub Pages will serve from the default branch without a
workflow, and there is no CI in this repo yet.

## Regenerating

```bash
python -m pipelines.option_probe_figures      # figures/ + the report at index.html, one pass
python -m pipelines.build_report              # the report alone, reusing the built figures
```

Or run cell 10 of [`research/option_overlay_probe.py`](../research/option_overlay_probe.py), which
calls the same `main()`. The figure builders live in
[`src/pipelines/option_probe_figures.py`](../src/pipelines/option_probe_figures.py) and are shared
with the research cells, so what is shown inline and what is published cannot drift apart.

## What "static" buys, and what it costs

The pages are **fully interactive with no server**: hover, zoom, pan, legend toggling and range
selection are all client-side. That is `fig.write_html`, not Dash — Dash callbacks are HTTP POSTs
to `/_dash-update-component`, so a Dash app cannot be published this way. Anything needing Python
at request time (live IBKR reads, secrets, the rebalancer control) is Track B and is not
publishable here at all.

The report page itself is ~365 KB: it carries the five figures' JSON and loads the shared library
with one `<script src="figures/plotly.min.js">`, rather than inlining it per figure.

**`plotly.min.js` is 4.1 MB and is committed.** That is the price of
`include_plotlyjs="directory"`, which 10-01 chose so the pages carry no CDN dependency and work
offline. It is emitted **once** and shared by every figure, so adding figures costs ~30-100 KB
each, not 4 MB each. If the vendored copy is ever judged too heavy for the repo, switching to
`include_plotlyjs="cdn"` in `main()` is a one-word change and takes `docs/` to roughly 270 KB — at
the cost of needing the CDN reachable when someone opens the page.

## Theming

`theme.apply_export_theme(fig)` is applied immediately before `write_html` and is **not
optional**. A figure built for the VS Code interactive window has a transparent background and
relies on the host to supply a dark surface; a standalone file has no such host, so the browser
paints its own white and a dark figure becomes unreadable. See the docstring at the top of
[`src/portutils/viz/theme.py`](../src/portutils/viz/theme.py).

## Status

Pages is **not yet enabled** on the repository — that is a settings change, not a code change.
Until it is, these files render correctly when opened locally in a browser.
