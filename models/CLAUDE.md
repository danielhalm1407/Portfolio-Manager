# models/ — Claude Instructions

## Structure
- `thematic/` — narrative-to-tilt scoring logic
- `structural/` — gas/power/value-chain structural models
- `allocator/` — portfolio construction and risk logic

## Conventions
- Document input/output specs and units for every model
- Models should be reproducible given the same inputs
- Keep model parameters in `config/settings.yaml`, not hardcoded
