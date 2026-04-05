---
glob: notebooks/**/*.ipynb
---

When working with notebooks:
- Follow the conventions in `notebooks/CLAUDE.md`
- Exploration notebooks go in `notebooks/exploration/` — messy is fine
- Report notebooks go in `notebooks/reports/` — must be reproducible (restart kernel and run all before saving)
- If analysis in exploration proves useful, extract the logic into `src/portutils/` rather than leaving it in the notebook
