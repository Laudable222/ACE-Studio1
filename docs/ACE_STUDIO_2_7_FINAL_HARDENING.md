# ACE Studio 2.7 — Final Hardening

This build implements the final pre-build corrections found in the 2.6 deep sweep.

## Remote updates
Automatic GitHub/remote updates are disabled. `run.bat` and `run.sh` never fetch or overlay code from a remote repository. The update token and update URL are not shipped. This protects local enhancements from being silently overwritten.

## LLM routing and budgets
Every task-aware LLM call passes through `TaskLLM`. Idempotent cached calls also use the task router. A persistent reservation table prevents concurrent jobs from oversubscribing the monthly token budget. Cache hits consume no new tokens.

## Knowledge Vault
Relevant memories can be retrieved into research and alpha-generation prompts. Memories are explicitly advisory and cannot override hard datafield/operator rules.

## Evolution
Only a tested failed variant can become a parent. Exact `sim_result_id`, `variant_id`, and `execution_key` preserve provenance. Diagnosis uses the exact stored simulation and its persisted gate thresholds. Validator-repair variants require an explicit repair pass before they can be simulated.

## Data catalogue
Field deduplication preserves dataset relationships. Cross-region field/dataset comparisons are delay-aware. Data APIs report both requested and effective BRAIN configuration when an unsupported combination is corrected.

## Submission Manager
Queue records reference exact simulation results. Multiple configurations of the same alpha can coexist. Submission quota reservations count `submitting` records so concurrent submission requests cannot exceed the local daily limit.

## Security
No GitHub Personal Access Token is included in the build. API keys remain in the user's local `~/secrets/ace_keys.json` with restrictive permissions.
