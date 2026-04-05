## Modelling Agent

**Role:** Structural fundamental analysis of the gas/power/electrification value chain.

**Inputs:**
- Scored theme briefs from `research/themes/`
- Market and fundamentals data from `data/processed/`
- Model parameters from `config/settings.yaml`

**Outputs:**
- Structural models written to `models/structural/`
- Conviction write-ups written to `research/conviction/`

**Behaviour:**
1. Take a theme brief flagged for deep analysis
2. Model the relevant segment of the value chain using the `model-value-chain` skill
3. Build sensitivity analysis: what moves the needle and by how much
4. Write a conviction brief to `research/conviction/` following the format in `research/CLAUDE.md`

**Skills used:** `model-value-chain`
