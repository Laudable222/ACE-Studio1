# ACE Studio 2.9 — Research Intelligence + OpenRouter + Alpha Replication

This release builds on the 2.8 production-audit baseline.

## Research Markdown pipeline

- Deterministic Markdown intermediate parser preserves section titles, line numbers, bullets, numbered lists, tables, code blocks and equation-like lines.
- Document metadata is extracted when explicitly labelled: title, authors, year, source/journal and DOI.
- Research analysis distinguishes source-supported findings from model inference.
- The research LLM is not asked to invent BRAIN field IDs.
- LLM analysis remains separate from alpha-expression generation.

## OpenRouter model catalogue

- OpenRouter model discovery now uses the live `/api/v1/models` catalogue (and the user-filtered `/models/user` catalogue when a key is available).
- Up to 1,000 text models can be listed.
- Settings includes model search and refresh.
- New OpenRouter models can appear without an ACE code release.

## Alpha Replication

New `/replication` screen and `/api/replication/preview` endpoint.

Workflow:

1. Paste a source-region alpha.
2. Specify source and target region/delay/universe.
3. ACE parses the expression and identifies non-universal datafields.
4. Target fields are searched in BRAIN or the local verified catalogue.
5. Exact matches are preferred.
6. If an exact field is absent, ACE ranks verified equivalents using field descriptions, categories and identifiers.
7. Optional research-model review can rank the verified candidates. It cannot invent a field.
8. Every candidate is syntax/operator validated before it can be sent to Simulation.
9. A candidate is only considered a successful replication after target-region simulation.

The replication feature deliberately does not treat a text replacement such as `IND -> GBR` as sufficient.
