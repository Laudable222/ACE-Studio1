# ACE Studio 2.6 Final Corrections

This build incorporates the pre-final audit corrections.

## Alpha Evolution
- Exact `execution_key` and `variant_id` now travel from variant to simulation result.
- Simulation results persist the complete execution configuration.
- Child variants inherit the parent settings and only apply explicit mutation deltas.
- A child is diagnosed from its own simulation result, not the family root.
- Evolution simulation errors transition the variant out of `running` and preserve the error.
- The Evolution UI can explicitly queue a selected variant for simulation.
- Expression-based simulation-result matching was removed from the evolution attachment path.

## Data Catalogue
- Data Explorer synchronizes region/delay/universe, datasets and selected fields into the global research context.
- Fields from multiple selected datasets are merged and de-duplicated by dataset/field/region/delay.
- Catalogue ingestion preserves dataset-to-field relationships.

## LLM
- Research, alpha generation, critique, strategy, discovery and related services use task-aware routing.
- A monthly approximate token budget defaults to 1,000,000 and can be changed with `ACE_LLM_MONTHLY_TOKEN_BUDGET`.
- Usage is stored locally and exposed through `/api/settings/llm/usage`.
- LLM API keys remain optional until actual AI calls are made.

## Reliability and security
- Core module failures are now fatal instead of being silently skipped.
- API key files are restricted to user-only permissions where the OS permits.
- SQLite remains WAL-enabled with a busy timeout and additive startup migrations for new columns.
- Initial research context is aligned with the current IND/TOP1000 workflow.

## Verification
- Backend imports and route registration pass without an LLM API key.
- Evolution lineage was tested with synthetic simulation results, including settings inheritance and exact execution attachment.
- Frontend dependency installation/build still requires `npm ci` on the target machine because the packaged environment does not contain a complete npm dependency tree.
