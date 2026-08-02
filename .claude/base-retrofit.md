# BASE v2 — evaluated, not installed, and how to retrofit it

**Status: not installed, deliberately.** Written 2026-08-01, at the point PAUL was adopted. Nothing
in this repo depends on BASE. `.paul/paul.toml` already carries the fields BASE would read, so
adopting it later is a short job rather than a migration.

---

## What BASE actually is

A single Rust binary, roughly 20 MB, that maintains a queryable knowledge graph of your workspace and
injects relevant slices of it into Claude Code automatically.

| Piece | What it is |
|---|---|
| Store | Oxigraph, embedded RDF triple store |
| Query language | SPARQL |
| Persistence | NQuads — plain text, so it diffs and versions |
| Code understanding | tree-sitter AST extraction, 35+ languages |
| Config | TOML (`domains.toml`, `base.toml`, `operator.toml`) |
| Visualisation | `base dashboard` — HTTP server compiled into the binary |

It graphs code structure, projects, tasks, people and decisions together, so a query can cross from
"this function" to "the decision that introduced it".

### Two words that collide with other tools

- **"Workspace"** here means *the project directory containing `.base/`*. It is **not** a VSCode
  `.code-workspace` file, and **not** an Obsidian vault. Same word, unrelated meaning.
- **The graph is not Obsidian-like in origin.** Obsidian's graph is a by-product of `[[wikilinks]]`
  you write by hand between notes. BASE's is *extracted* — from source ASTs and structured TOML — and
  is queried programmatically. Context injection is the purpose; the picture is a side-effect. The
  resemblance is visual only.

---

## The four hooks

`base install` wires these into `~/.claude/settings.json`:

| Hook | What it does |
|---|---|
| **SessionStart** | Syncs domains, ingests projects, runs signals. Merges the global and workspace graphs into one store so queries span both. |
| **UserPromptSubmit** | *"Matches keywords against domains, injects rules from graph."* A read, not a write — nothing is logged here. |
| **PreToolUse** | Injects an AST file map before a source read/edit. Intercepts grep with a graph hint. Injects domain rules for matched paths. Injects a markdown extraction contract on `.md` writes, so authored markdown becomes graph-aware. |
| **PostToolUse** | Updates access timestamps. Injects section-specific AST context when only part of a file was read. |

All hooks **fail open**: on error they log to stderr and exit with empty stdout. Claude is never
blocked by a hook failure.

---

## Why it is not installed

Three reasons, in order of weight. Note what is *not* on this list: hook conflict.

### 1. UserPromptSubmit duplicates CARL

BASE's UserPromptSubmit "matches keywords against domains, injects rules from graph". That is the
same job `carl-hook.py` already does — same notion of a domain, same keyword gating, same injection —
from a different store. (That hook lives in the *home* Claude config, `~/.claude/hooks/carl-hook.py`,
not in this repo — it is registered globally and fires in every project.)

The overlap is exact, and confirmable from BASE's docs without installing anything. Its `[[domain]]`
schema maps field-for-field onto CARL's:

```toml
[[domain]]
name = "BACKEND"
mode = "triggered"                                    # ↔ CARL state / always_on
prompt_keywords = ["api", "endpoint", "database"]     # ↔ CARL recall
file_keywords = ["use crate", "impl", "async fn"]     # no CARL equivalent
paths = ["src/api/", "src/db/"]                       # no CARL equivalent
rules = ["Always validate inputs at API boundaries"]  # ↔ CARL rules — literally the same
query = "backend-context"                             # no CARL equivalent (SPARQL)
query_format = "list"
```

BASE domains carry **plain behavioural rule text**, not just graph metadata. So BASE is a superset:
same three concepts (name, trigger keywords, injected rules) plus file-content matching, path
matching and graph queries.

Running both means two independent domain-matching rule injectors, and a decision about which owns
rules. This repo's CARL domains (GLOBAL, DEVELOPMENT, TOOLING in `~/.carl/carl.json`; PAUL in
`.carl/carl.json`) would sit alongside BASE's `domains.toml` doing the same work.

**BASE's own docs assume CARL is present and working.** `docs/multi-tool-hook-bible.md` lists
*"CARL domain injection won't work via hooks"* (Cursor) and *"CARL can't inject context on prompt
submit"* (Windsurf) as **gaps on other editors** — i.e. losses relative to Claude Code, where CARL
functions. No migration path from CARL to BASE is documented, and no guidance exists on avoiding
duplicate injection. The interaction is simply undefined.

The author's public position is that they complement — *CARL handles rules and decisions, BASE
handles workspace data and project tracking* — and each works without the other.

**PreToolUse is genuinely additive.** AST file maps and grep-with-graph-hint have no CARL equivalent,
and are the strongest argument for adopting BASE.

### 2. `base install` rewrites shared files

It writes to `~/.claude/settings.json` and appends a CLI reference to `CLAUDE.md`. Both are files
other tooling depends on. Back them up first.

### 3. The two sides describe the integration differently

BASE **does** document it — `docs/paul-graph-integration.md`, plus `parallel-paul-protocol.md` and
`markdown-ontology-protocol.md`. But it does not work the way PAUL's templates imply.

**BASE never reads `paul.toml` or `ledger.toml`.** It scans workspace files against the include
patterns in `.base/base.toml` and routes by filename:

- `.paul/phases/*-PLAN.md` and `*-SUMMARY.md` → the dedicated PAUL extractor (`paul_md.rs`)
- `.paul/PROJECT.md`, `STATE.md`, `ROADMAP.md` → the generic frontmatter extractor

It pulls typed RDF entities out of the markdown: YAML frontmatter (`phase`, `plan`, `subsystem`,
`tags`, `depends_on`, `affects`) and markdown tables (Decisions, File Changes, Acceptance Criteria
Results).

Conversely, PAUL's claim that `tags` in `paul.toml` "creates `hasDomain` edges" is not corroborated
on BASE's side — step 7 below is cosmetic until proven otherwise.

### The extraction contract, and how this repo satisfies it

`paul_md.rs` is **BASE's** Rust source (`.rs`), compiled into the `base` binary — not a script you
run, and nothing to do with PAUL itself. It parses three pipe-delimited tables, each with a header
row and a separator row, and turns each row into a typed RDF entity:

| Table heading in the SUMMARY | Entity produced | Fields |
|---|---|---|
| `## Decisions Made` | `Decision` | `description`, `rationale`, `impact`, `fromPlan` |
| `## Files Created/Modified` | `FileChange` | `filePath`, `changeType`, `purpose`, `fromPlan` |
| `## Acceptance Criteria Results` | `AcceptanceCriteriaResult` | `criterion`, `status`, `fromPlan` |

**Why `FileChange` matters more than it looks.** A graph is nodes joined by edges. `fromPlan` +
`filePath` is the edge tying a *plan* node to a *source file* node — the line drawn between them in
the dashboard, and what makes "which plan last touched `ibkr_requests.py`?" answerable. Without that
table, the phase records and the codebase are two unconnected islands in the graph; frontmatter
`key-files:` is metadata hanging off the plan, not a relationship between two things.

**Status: satisfied.** The migration originally recorded file changes only as `key-files:`
frontmatter, leaving `## Files Created/Modified` absent from all 11 SUMMARY files. That was corrected
on 2026-08-02 — every SUMMARY now carries the table in PAUL's own column shape (`| File | Change |
Purpose |`), positioned per the template between Accomplishments and Decisions Made. 55 rows total;
every `filePath` verified to exist on disk, every `changeType` is `Created` or `Modified`, and every
`purpose` is substantive rather than a bare "Modified" — BASE stores `purpose` verbatim on the
entity, so an empty one produces a dead edge.

If you add SUMMARY files by hand later, keep that table. `/paul:unify` generates it from the
template, so the normal loop maintains it for you.

### What is *not* a reason: hook conflict

Claude Code's hook schema is an array of matchers, each holding an array of hooks. All of them fire
and their outputs concatenate. This repo ran two PostToolUse hooks side by side —
`post-edit-lint.sh` and `mirror-plan.py` — until `mirror-plan.py` was retired on 2026-08-01. Adding
BASE's hooks alongside CARL's would work mechanically.

---

## Retrofit runbook

Installing PAUL before BASE cost nothing. The only BASE-dependent step in `/paul:init` is
`select_tags`, which populates one array — step 7 below.

1. **Download** `base-windows-x86_64.zip` from the **v0.10.3** release of
   [`ChristopherKahler/base`](https://github.com/ChristopherKahler/base/releases). Prebuilt — no Rust
   toolchain required, and none is installed on this machine.
2. **Back up `~/.claude/settings.json` and `CLAUDE.md`.** `base install` writes to both.
3. **Extract** to `~/.local/bin/base` and run `base install`.
4. **Diff `~/.claude/settings.json`.** Confirm `carl-hook.py` still runs on UserPromptSubmit. Then
   read one injected prompt in full and decide whether CARL or BASE owns domain rules — they overlap
   (see reason 1).
5. **From the repo root:** `base scaffold` → creates `.base/` with `domains.toml`.
6. `base domain list` → note the domain names.
7. **Optional — hand-edit `tags` in `.paul/paul.toml`** to those names, lowercased. This is the
   `select_tags` step skipped at init, but note that per `docs/paul-graph-integration.md` **BASE
   never reads `paul.toml`**, so this is cosmetic unless a later version changes that. The
   `carl_v2_*` MCP tools cannot do it either — they always target `~/.carl/carl.json`.
8. `base sync --ast`, then `base project list` → confirm **Portfolio-Manager** appears. Ingestion
   comes from `.paul/phases/*-PLAN.md` / `*-SUMMARY.md` via `paul_md.rs` and from the frontmatter of
   PROJECT/STATE/ROADMAP — all of which already exist and are correctly shaped. If nothing appears,
   check `.base/base.toml` include patterns cover `.paul/`.
9. `base dashboard` → confirm the project node renders.
10. **Consider `.base/` in `.gitignore`** after seeing the diff. NQuads are text, so versioning the
    graph is defensible — a judgement call once you have seen how noisy it is, not a default.

---

## Seeing the graph inside VSCode

**Simplest, no extension:** run `base dashboard`, then `Ctrl+Shift+P` → **"Simple Browser: Show"** →
paste the localhost URL. It renders in an editor tab. The dashboard is a force-directed graph — code
blue, projects green, people orange, decisions yellow — plus a kanban board and a live WebSocket feed
of hook events.

**RDF extensions** — these read BASE's actual store:

| Extension | Fit |
|---|---|
| [Linked Data Extension](https://marketplace.visualstudio.com/items?itemName=Elsevier.linked-data) | **Best fit.** Visualises an RDF graph from a file, converts between JSON-LD / Turtle / RDF-XML / **NQuads**, runs SPARQL directly on the file with no triple-store to install. NQuads is BASE's format. |
| [Zazuko RDF Sketch](https://marketplace.visualstudio.com/items?itemName=Zazuko.vscode-rdf-sketch) | N3/Turtle only — needs conversion first. |
| [Mentor](https://marketplace.visualstudio.com/items?itemName=faubulous.mentor) | Full RDF IDE with SPARQL notebooks. Heavier than needed for viewing. |

**Markdown-link graph extensions are a different thing** — [Markdown Links](https://marketplace.visualstudio.com/items?itemName=tchayen.markdown-links),
[Obsidian Visualizer](https://marketplace.visualstudio.com/items?itemName=khuongduy354.obsidian-visualizer),
[InfraNodus](https://marketplace.visualstudio.com/items?itemName=infranodus.infranodus-graph-view), Foam.
They graph `[[wikilinks]]` between markdown files and have no connection to BASE.

**They would render almost nothing in this repo.** The plans, READMEs and `.paul/` records use
standard relative `[text](path)` links, not wikilinks. The RDF route is the one that would actually
show this project.

**None of these run in Rust.** VSCode extensions are JavaScript/TypeScript with a webview, typically
D3 or vis.js for rendering. Only BASE itself is Rust — a Rust binary cannot be a VSCode extension
host; at most it would be a language server the extension talks to.

---

## Related decisions

Logged in CARL (`~/.carl/carl.json`, domain `TOOLING`) — search with
`carl_v2_search_decisions("base")` or `("paul")`:

- `tooling-001` — adopt PAUL, reject GSD and SEED
- `tooling-002` — PAUL owns `.paul/` decisions; CARL stays rules-only
- `tooling-005` / `tooling-006` — PAUL's CARL domain is project-scoped with `always_on: true`
- `tooling-007` — PAUL installed, 12 legacy plans migrated
- `tooling-008` — this document
