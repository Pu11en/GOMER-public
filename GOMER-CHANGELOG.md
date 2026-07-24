# Changelog

Notable changes to this public portfolio release of GOMER.
GOMER is research infrastructure, not a medical device — human clinical review
remains required. This repository contains synthetic data only.

## Roadmap

- Additional synthetic case archetypes (renal, hepatic) for the demo pack
- Surface "fit" dimension scoring more explicitly in the Deliverable
- JSONL/CSV export of the source Trail alongside the HTML report
- Property-based tests over the correction-engine matcher

## 0.1.0 — 2026-07-21

### Added

- Deterministic completed-result selection
- Nearest same-code, same-unit comparison
- Explicit refusal across incompatible units
- Source-linked Claims with **quote verification against corpus text**
- Safety gates and evidence-bounded correction logic
- Deterministic Signal & Story HTML deliverable
- Synthetic demo case + unittest suite (CI-green)

### Boundaries

- Synthetic data only; research infrastructure, not a medical device,
  diagnostic system, or prescribing system.
