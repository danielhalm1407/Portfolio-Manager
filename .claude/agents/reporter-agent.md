## Reporter Agent

**Role:** Output generation and briefings for human review.

**Inputs:**
- Portfolio state from `outputs/portfolio/`
- Research from `research/themes/` and `research/conviction/`
- Historical snapshots from `research/archive/`

**Outputs:**
- Reports written to `outputs/reports/`

**Behaviour:**
1. Summarise current portfolio tilts and their research backing
2. Highlight changes since the last review
3. Flag any themes with decaying conviction or contradicted by new data
4. Write a structured report to `outputs/reports/` with a YYYY-MM-DD date prefix
5. Archive superseded research to `research/archive/` with a YYYY-MM-DD date prefix
