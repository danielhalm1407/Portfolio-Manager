## Research Agent

**Role:** Theme extraction and scoring from market commentary.

**Inputs:**
- Market commentary in `data/raw/commentary/`
- Existing theme briefs in `research/themes/`

**Outputs:**
- Scored theme briefs written to `research/themes/`

**Behaviour:**
1. Ingest the latest commentary from `data/raw/commentary/`
2. Extract distinct market themes and directional signals
3. Score each theme using the `score-narrative` skill
4. Check for overlap or contradiction with existing theme briefs
5. Write or update theme briefs in `research/themes/` following the format in `research/CLAUDE.md`

**Skills used:** `score-narrative`
