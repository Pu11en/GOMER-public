# Architecture

GOMER separates patient-specific evidence from external medical knowledge and requires evidence before agent reasoning.

```text
Goal
→ Playbook
→ Chart retrieval + Corpus retrieval
→ Evidence Pack
→ Claims with Trails
→ Deliverable
```

## Boundaries

- **Case:** one isolated clinical workspace.
- **Chart:** patient-specific records.
- **Corpus:** external guidelines, papers, labels, and protocols.
- **Playbook:** one bounded clinical workflow.
- **Evidence Pack:** the smallest source-backed input needed for the goal.
- **Claim:** one evaluated assertion.
- **Trail:** the proof path back to source evidence.
- **Deliverable:** a human-facing report or other output.

The synthetic demo exercises deterministic Chart comparison and report generation only. It does not connect to a live patient system, model provider, or messaging service.
