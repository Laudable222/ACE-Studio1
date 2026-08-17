# ACE Studio 2.4 — Alpha Evolution Engine

Failed alphas are retained as research lineage rather than discarded.

## Workflow

1. A simulated alpha fails the gate.
2. The researcher opens **Alpha Evolution** and enters the BRAIN alpha ID.
3. ACE diagnoses the dominant failure mode.
4. ACE creates an **Alpha Family** and preserves the failed alpha as the parent node.
5. `Propose next variants` creates controlled mutations. Expression, parameter/lookback, settings, and repair proposals are recorded separately.
6. Proposed variants are **not silently simulated** and are **never auto-submitted**.
7. When a proposed expression is later simulated through ACE, the simulation result is automatically attached to the matching proposed lineage node.
8. A failed child can become the parent of the next generation. A passing child is retained as a successful family member and can proceed to the existing Submission Manager.
9. A branch can be closed when the research direction is exhausted.

## Guardrails

- 30 variants per family by default.
- 3 generations maximum.
- Controlled mutations prefer changing one dimension at a time.
- Validation failures create a targeted repair proposal instead of random rewrites.
- Failure history is persistent.
- No automatic submission.
- The local database is the source of lineage history.

## Failure diagnosis

ACE currently classifies failures into signal strength, fitness, turnover, correlation, validation, and general gate failure. The diagnosis controls which mutation families are proposed.

## Important limitation

The current engine proposes and records variants. It deliberately leaves the final simulation invocation to the existing Simulation screen so that no large, uncontrolled simulation burst occurs. Once a proposed expression is simulated, the result is automatically linked back to its family.
