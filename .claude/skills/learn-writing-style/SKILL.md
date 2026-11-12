---
name: learn-writing-style
description: Learn from Daniel's own edits to published prose and update the style guide. Use automatically whenever Daniel's hand-written changes to a front-facing write-up become visible — an uncommitted diff or a recent commit touching research/site/*.md or research/validation/*.md that Claude did not write, Daniel saying they have edited, rewritten or reworded a page, or Daniel asking to commit their manual edits — and whenever they ask to update, learn or capture their writing style. Updates .claude/skills/publish-writing-style/STYLE.md.
---

# Learn writing style

Keeps [STYLE.md](../publish-writing-style/STYLE.md) in step with how Daniel actually writes. Run it
BEFORE making any edit of your own to a file they have just changed, so their wording is read as
evidence and not overwritten.

## 1. Collect only Daniel's text

Evidence must be their writing, never Claude's. Sources, in order:

- **Uncommitted changes** to the published write-ups that Claude did not make in this session:
  `git diff -- research/site research/validation/*.md`.
- **Commits since the last processed one.** STYLE.md records `Evidence processed through commit:`.
  List the newer commits with `git log --format='%h %s%n%b' <that-commit>..HEAD -- research/site research/validation/*.md`.
  A commit is Daniel's if it has NO `Co-Authored-By: Claude` trailer, or its subject says
  "hand edits". Read each with `git show <hash> -- <file>` (add `--word-diff=plain` to see
  exactly which words they swapped).
- **Direct instructions** about wording in the conversation ("use an action title", "less
  formal", "don't say we").

What Daniel REMOVED is as informative as what they added: a phrase they replaced is a phrase to avoid.

## 2. Extract the patterns

For each change, note what it shows about:

- voice and person (we / impersonal / passive);
- vocabulary, both words introduced and words removed;
- punctuation (em dashes, colons, commas, semicolons, capitalisation of emphasis words);
- sentence length and how clauses are joined;
- how strongly claims are stated (modals, hedges, scope);
- headings and titles, by level and by type of page;
- Markdown layout (line wrapping, paragraph length, lists versus prose).

Ignore typos and slips. If an edit contains a likely typo (for example "demonstrateted"),
mention it to Daniel in chat and do not record it as style.

## 3. Update STYLE.md

- Add or sharpen a rule only when the edit supports it. Quote the shortest phrase that shows it.
- When new evidence contradicts an existing rule, strike the old rule through (`~~...~~`), note
  the date it was superseded and why, and add the new rule. Never delete a rule outright.
- Keep it short. Merge a new observation into an existing rule before adding a new bullet.
- Add a row to the Evidence log (date, file and commit, what it showed).
- Move `Evidence processed through commit:` to the newest commit you read.

## 4. Report

Tell Daniel in two or three lines which rules changed, so they can correct a misreading. The style
guide is theirs to overrule: if they disagree, their correction is itself evidence and goes in the log.
