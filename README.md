# GOMER

**Evidence-first infrastructure for agent-run clinical workups.**

GOMER is a local-first engineering prototype that separates patient-specific Chart evidence from external medical knowledge, retrieves evidence before reasoning, preserves source Trails, and renders bounded Deliverables.

## What this repository proves

- deterministic completed-result selection;
- nearest same-code, same-unit comparison;
- explicit refusal across incompatible units;
- source-linked Claims and safety gates;
- evidence-bounded correction logic;
- deterministic Signal & Story HTML.

## Try the synthetic demo

```bash
python3 examples/demo-case/demo.py --check
python3 -m unittest discover -s tests -v
```

Open `examples/demo-case/output/report.html` after the demo runs.

## Architecture

```text
Goal → Playbook → Chart + Corpus → Evidence Pack → Claims + Trails → Deliverable
```

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Safety boundary

This public repository contains synthetic data only. GOMER is research infrastructure, not a medical device, diagnostic system, or prescribing system. Human clinical review remains required.

## Current scope

This is a portfolio release for technical review. It is not a hosted service, one-click installer, or production clinical deployment.

## Work with Drew

For clinical software, agent infrastructure, or technical product-video work, contact [Pu11en on GitHub](https://github.com/Pu11en).

## License

Apache 2.0. See [LICENSE](LICENSE).
