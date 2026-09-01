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

Every colour on these figures comes from
[`src/portutils/viz/theme.py`](../src/portutils/viz/theme.py) — the repo's single palette — and is
passed straight through the plotly arguments in each `fig_*` builder (`_AXIS` and `_LAYOUT` in
`option_probe_figures.py`). No literals, and no theming helper of its own.

It is set at **build** time, not at export, because it is correct in every context: `#e0e0e0`
reads right in the VS Code interactive window, in a Dash page and in a saved file alike. Without
it a figure silently falls back to plotly's default template — dark navy text, illegible on a dark
editor background.

`theme.apply_export_theme(fig)` is then applied immediately before `write_html`, and is **not
optional**. It supplies the one property that genuinely depends on destination: an opaque
background. A figure shown inline sits on a surface the host provides; a standalone file has no
host, so a transparent figure lets the browser paint its own white.

**Known cosmetic gap:** plotly's generated page carries only `html, body {height: 100%}` as CSS —
no `margin: 0` — so the dark figure sits inside the browser's default ~8px body margin and shows a
thin white frame. The fix is a page-level stylesheet rather than a figure change; see
`index.html`, which already sets `theme.PAGE_BG` on its own body.

## Status

Pages is **not yet enabled** on the repository — that is a settings change, not a code change.
Until it is, these files render correctly when opened locally in a browser.
