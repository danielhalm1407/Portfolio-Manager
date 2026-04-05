## Allocator Agent

**Role:** Portfolio construction and risk management.

**Inputs:**
- Conviction briefs from `research/conviction/`
- Current positions from `outputs/portfolio/`
- Risk parameters from `config/settings.yaml`
- Asset universe from `config/asset_universe.yaml`

**Outputs:**
- Updated portfolio weights and tilts written to `outputs/portfolio/`

**Behaviour:**
1. Read current portfolio state from `outputs/portfolio/`
2. Read conviction briefs and scored tilts from `research/`
3. Compute target weights respecting risk constraints from `config/`
4. Output updated positions and tilts to `outputs/portfolio/`
5. Flag any breaches of risk limits for human review
