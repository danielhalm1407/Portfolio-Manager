---
name: publish-writing-style
description: Write in Daniel's own published-page voice. Use automatically whenever writing, rewriting, editing or reviewing front-facing prose in Portfolio-Manager that ends up on the published site — the write-ups in research/validation/*.md, the hub pages in research/site/*.md, page titles, section headings, card blurbs, figure lead-ins, chart and axis titles, page header/footer notes in the pipelines that build docs/ — and whenever the user asks to write "in my style", "in my voice", or to tidy the language of a page. Not for code comments, commit messages, plans or chat replies.
---

# Publish writing style

Prose that will be published on the site must read as if Daniel wrote it. The style itself lives in
[STYLE.md](STYLE.md), built from their own edits; this file says how to apply it.

## Before writing

1. Read [STYLE.md](STYLE.md) in full. It is short on purpose, and every rule in it came from text
   Daniel wrote or a correction they made.
2. Find the most recent wording Daniel chose for the file you are about to edit. They protect their
   own edits with git commits rather than markers, so their last committed wording is approved text:
   - `git log -p -3 -- <file>` shows recent changes to it; a commit WITHOUT a
     `Co-Authored-By: Claude` trailer, or one whose subject says "hand edits", is theirs.
   - `git diff -- <file>` shows anything they have changed and not yet committed. Treat it as theirs.
   Keep that wording. Change it only where the request requires, and if a number inside it has
   gone stale, say so in chat rather than rewording their sentence.
3. If the file contains `<!-- user:start -->` / `<!-- user:end -->` blocks (an older convention),
   never edit inside them.

## While writing

- Apply STYLE.md's rules for the element in hand: a page title, a section heading, a hub card blurb,
  body prose and a figure lead-in each have their own pattern there.
- Match the register of the surrounding paragraphs in the same file before matching the general
  guide. A page's own voice wins when the two disagree; note the disagreement for the
  `learn-writing-style` skill.
- Sharp before elegant, elegant before plain: cut every word that carries nothing, but where one
  well-chosen phrase replaces a clumsy clause, use it. Never pad to sound formal.

## After writing

- Re-read each changed paragraph against the STYLE.md checklist at the bottom of that file.
- Rebuild the page with its command (table in STYLE.md) so the published HTML matches.
- In the reply, name the sections whose wording changed, so Daniel can review them with
  `git diff` before committing.
