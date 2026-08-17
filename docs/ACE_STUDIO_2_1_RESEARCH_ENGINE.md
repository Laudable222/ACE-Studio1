# ACE Studio 2.2 Research Engine

ACE Studio 2.2 adds a research-first layer to the existing BRAIN/LLM workflow.

## New flow

Markdown report → research extraction → economic hypotheses → BRAIN field mapping → experiment → grounded alpha generation → validation → BRAIN simulation → Alpha DNA / field intelligence / failure memory.

## Research Engine screen

Use **Research Engine** in the sidebar.

1. Add a UTF-8 `.md`, `.markdown`, or `.txt` report.
2. Select the report and choose **Analyse selected report**.
3. ACE extracts findings, mechanisms, variables and testable hypotheses. If an LLM key is available, it uses the configured LLM. If not, a local heuristic extraction still ingests the document.
4. In Data Explorer, select the BRAIN fields you want ACE to consider.
5. Click **Map to selected BRAIN fields**. This is a relevance ranking, not a claim that a field is semantically identical to the paper variable.
6. Create an experiment from a hypothesis.
7. Generate candidates for that experiment. The experiment's hypothesis is turned into a grounded generation brief and the normal validator remains the execution gate.
8. Send the resulting expressions to Simulation as usual.

## Research memory

Each simulated expression now contributes to:

- **Alpha DNA**: fields, operators, categories, structure and a simple novelty score.
- **Field Intelligence**: empirical uses, average absolute Sharpe/Fitness, pass/fail counts and successful operators.
- **Failure Memory**: explicit gate failures and their metrics/reasons.
- **Experiments**: the research question/hypothesis and candidate expressions are retained together.

Research memory is deliberately best-effort. If the memory layer fails, the core BRAIN simulation result is still stored.

## Important limitation

The field mapper is a ranking aid. It does not magically prove that a BRAIN field represents the exact construct in a paper. Human review remains part of the research loop.

The LLM is also not trusted with executable truth. Generated expressions are still checked against the selected BRAIN fields and the existing operator validator before simulation.
