# Daniel's published-page style

Built from Daniel's own writing and corrections. Rules are stated as observed, each with the
evidence it rests on. The `learn-writing-style` skill keeps this file current; newer evidence
overrides older evidence, and a superseded rule is struck through rather than deleted.

Evidence processed through commit: `316f5a8` (2026-11-12)

## The overall voice

- **Formal, measured and professional.** It reads like an investment research note, not a blog
  and not a lab notebook.
- **Sharp and to the point.** Each sentence makes one claim. No preamble, no throat-clearing,
  no restating what the reader has just read.
- **Elegant where it is cheap.** A precise, slightly elevated phrase is preferred to a long plain
  clause ("growth under the guardrails of diversification and hedging"), but never at the cost of
  clarity. Not overcomplicated.
- **Claims are measured, not overstated.** Results are said to "contribute", "evidence" or
  "demonstrate", and a modal is used where the evidence is one window or in-sample ("could
  contribute an additional seven percentage points", not "was worth seven points").

## Voice and person

- **Impersonal subjects in narrative prose.** The analysis, the study or the research is the
  subject: "A preceding analysis in pricing the hedge demonstrated...", "It also served to
  exhibit...", "This research contains three studies...". Evidence: 2026-11-12 hand edit replaced
  both "We previously established..." openers with impersonal ones.
- ~~Open recap paragraphs with "We previously established..."~~ (2026-10-19 preference,
  superseded by the 2026-11-12 edit above).
- **"We" survives in figure lead-ins**: "Below, we compare...", "Below, we show...". This is a
  standing preference (2026-10-19), not yet contradicted.
- Never "I". Never address the reader as "you".

## Vocabulary

Words and phrases Daniel reaches for. Prefer these to plainer or more casual equivalents.

- Linking: "Furthermore,", "For instance,", "In addition,", "Of particular focus ... are ...",
  "A key defining principle supporting this outcome is ...".
- Evidence verbs: "demonstrated", "served to exhibit", "evidenced", "investigates", "establishes".
- Precision phrases: "with respect to", "at one or more points along its path", "relative to SPY",
  "across market environments", "of returns" (states the unit of a gain explicitly).
- Portfolio language: "sustained returns", "portfolio tilts", "higher conviction", "risk impact
  across the wider portfolio", "guardrails of diversification and hedging", "drawdown-adjusted".
- Avoid: casual intensifiers ("really", "huge", "super"), hype ("powerful", "game-changing"),
  and coined jargon a reader would have to decode.
- British spelling throughout: optimisation, monetisation, penalised, behaviour, colour.

## Sentence structure and punctuation

- Medium-length sentences, one clause added at most. Subordinate clauses go after the main claim,
  not in front of it.
- **Commas and full stops, not em dashes.** Daniel's own text uses no em dashes; when they rewrote
  a sentence they joined it with "and", "yet" or a comma instead. Avoid "—" in their voice. Use a
  colon only to introduce a list or a figure.
- "yet" for a contrast inside one sentence ("can reach multiples of its initial value ..., yet
  still expire worthless").
- A question can be folded into a sentence that lists what a section addresses: "Of particular
  focus in this section are the cost of protection when using puts, which structure should carry
  it, and how far out of the money should the put be struck?"
- Numbers stay exact and carry their unit: "14% of notional a year", "0.90× put",
  "seven percentage points of returns". Words for small counts in prose ("three studies").
- **One paragraph per line in Markdown.** Daniel does not hard-wrap prose. Do not re-wrap their
  paragraphs, and write new paragraphs on a single line.

## Titles and headings, by type

- **Page titles** (the `# ` line): a descriptive gerund or noun phrase naming the subject, sentence
  case. "Optimising put option moneyness for drawdown-adjusted returns", "Hedging an equity
  portfolio with put options", "Option hedge structures compared over the GFC".
- **Section headings** (`## `): **action titles** that state the section's takeaway as a full
  claim, sentence case, no full stop. "Monetisation on a drawdown reverses the ordering of strikes",
  "Hedging costs return in calm markets, and deeper strikes cost more", "Additional analysis is
  needed to validate the strategy's out-of-sample performance and the robustness of its optimal
  parameters". Never a label ("Results", "Why this exists", "What this establishes, and what it
  does not"). Evidence: 2026-10-19 preference and the 2026-10-24 retitle request.
- Methodological sections may use a descriptive heading where there is no single takeaway
  ("Design of the empirical optimisation analysis", "Reproducing this analysis").
- **Sub-headings** (`### `): also claims where possible ("Each strike is allocated the same premium
  budget").
- **Chart and axis titles**: Capitalised (title case). 2026-10-19 preference.

## Element patterns

- **Hub card blurb**: opens with a verb-led fragment that names what the piece does
  ("Investigates the effectiveness of using put options to offset drawdowns in an underlying
  equity index."), then the questions it focuses on, then one sentence on its scope or structure.
  Short paragraphs, one idea each.
- **`Data:` line** under a card: terse, factual, keeps REAL / IN-SAMPLE in capitals so the
  evidence base is visible before a click.
- **Project description**: states the aim, then the defining principle, then what the site
  publishes. Example: the opening of research/site/portfolio_manager_hub.md.
- **Recap of earlier studies**: impersonal subject, links the earlier page on its descriptive name
  ("A preceding analysis in [pricing the hedge](...) demonstrated that ..."), gives one concrete
  number as an example ("For instance, ...").
- **Figure lead-in**: one sentence of context before every figure, "Below, we compare ..." or
  "In the figure below, ...", saying what is held fixed and what varies.

## Checklist before handing text back

1. Does every `##` heading state a takeaway, not a topic?
2. Is the subject of each narrative sentence the analysis, not "we" (figure lead-ins excepted)?
3. Any em dashes in new prose? Replace with a comma, "and", "yet" or a full stop.
4. Any claim stronger than one in-sample window supports? Soften with "could" or name the scope.
5. Any word that could go without losing meaning? Cut it.
6. British spelling? Numbers exact, with units?
7. Paragraphs on a single line, and Daniel's committed wording left as they wrote it?

## Rebuilding the pages

| Write-up | Command (from the repo root) |
|---|---|
| research/site/*.md (both hubs) | `python -m pipelines.build_site_index` |
| research/validation/option_ladder_prices_probe.md | `python -m pipelines.build_report` |
| research/validation/option_hedge_structures_probe.md | `python -m research.validation.option_hedge_structures_probe --publish` |
| research/validation/option_moneyness_optimisation.md | `python -m pipelines.option_grid_sweep --publish-only` |

## Evidence log

| Date | Source | What it showed |
|---|---|---|
| 2026-10-19 | Corrections in session | Action-title headings; a sentence of context before each figure; "we previously established" openers; capitalised chart and axis titles |
| 2026-10-24 | Retitle request | "What this establishes, and what it does not" replaced by a full action title |
| 2026-11-12 | Hand edit, research/site/portfolio_manager_hub.md (in 8ab7f78) | Formal project description; "guardrails", "conviction", "risk impact across the wider portfolio"; verb-led card blurb; question folded into a sentence |
| 2026-11-12 | Hand edit, research/validation/option_moneyness_optimisation.md (316f5a8) | Impersonal subjects replace "we"; "For instance,", "Furthermore,"; "demonstrated", "served to exhibit", "evidenced"; hedged modal "could contribute"; no em dashes; unwrapped paragraphs |
