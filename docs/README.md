# `docs/` — the static, published surface

Everything in here is **generated** and **committed**. It is the GitHub Pages root: with Pages
pointed at *"Deploy from a branch → main → /docs"*, `index.html` becomes the site's front page
and `figures/` its content.

## Why here and not `outputs/`

`outputs/` is gitignored ([`.gitignore:37`](../.gitignore)) — correct for scenario runs, which are
scratch. A published page has to be committed to be served, so it needs a directory git actually
tracks. `docs/` is also the one folder GitHub Pages will serve from the default branch without a
workflow, and there is no CI in this repo yet.

## Regenerating

```bash
python -m pipelines.option_probe_figures      # writes figures/ and index.html
```

Or run cell 9 of [`research/option_overlay_probe.py`](../research/option_overlay_probe.py), which
calls the same `main()`. The figure builders live in
[`src/pipelines/option_probe_figures.py`](../src/pipelines/option_probe_figures.py) and are shared
with the research cells, so what is shown inline and what is published cannot drift apart.

## What "static" buys, and what it costs

The pages are **fully interactive with no server**: hover, zoom, pan, legend toggling and range
selection are all client-side. That is `fig.write_html`, not Dash — Dash callbacks are HTTP POSTs
to `/_dash-update-component`, so a Dash app cannot be published this way. Anything needing Python
at request time (live IBKR reads, secrets, the rebalancer control) is Track B and is not
publishable here at all.

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
