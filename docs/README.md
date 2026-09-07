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

## Theming — the figure carries colour, the page carries the surface

Each `fig_*` builder ends by calling `theme.apply_dark_theme(fig)` — the repo's one figure theme,
lifted out of `PanelBuilder` on 2026-08-31 so anything can reuse it. It sets ink (`theme.INK`),
gridlines (`theme.GRID`, white at 10%) and **transparent backgrounds**.

The figure therefore never paints its own surface. The *host* does, in all three places it is
shown:

| context | surface comes from |
|---|---|
| VS Code interactive window | the editor's own background |
| a Dash page | the page div (`theme.PAGE_BG`) |
| **these static pages** | the `<style>` in the page shell, also `theme.PAGE_BG` |

That last one is why `write_figure_page()` exists rather than `fig.write_html()`. `write_html`
emits a document whose only CSS is `html, body {height: 100%}` — no background and no `margin: 0`
— and offers no hook to add any. So the shell is written here instead: `to_html(full_html=False)`
returns the plotting div, and eleven lines of HTML around it set the body to `theme.PAGE_BG` with
the margin zeroed.

The payoff: **the published figure is the same object as the one shown inline**, with nothing
stamped on at export. It also removes the thin white frame the old route left around every figure.

`theme.apply_export_theme` still exists and is still correct for anything writing HTML the plain
way — `pipelines/rebalance_study.py:440` does — it just is not needed here.

## Status

Pages is **not yet enabled** on the repository — that is a settings change, not a code change.
Until it is, these files render correctly when opened locally in a browser.
