# ACE Studio 2.5 Integration/Fix Pass

This build closes the implementation gaps found during the 2.4 code audit.

## Included
- Region-aware BRAIN Data Explorer API and frontend.
- Local BRAIN catalogue ingestion for datasets and datafields.
- Knowledge Vault for hard rules, tips, observations, simulation evidence and lessons.
- Retrieval endpoint for relevant memories. These are supplied to LLM workflows as context; they do not retrain external models.
- Task-aware LLM routing with OpenRouter as the default gateway.
- Alpha Evolution lineage fixes. Child failures are diagnosed from the child simulation, not the root.
- Real time-window mutation for supported `ts_*` expressions.
- Explicit variant execution keys and variant-to-simulation attachment.
- Evolution simulation bridge. ACE can queue a selected variant into the existing simulation engine.
- SQLite WAL/busy timeout for safer concurrent background jobs.
- Windows launcher dependency hashing so pip is not run on every launch.

## API keys
No LLM API key is required to install, start, or test the local application. LLM-dependent actions report that no provider is configured and remain disabled until a key is added. BRAIN-dependent data actions likewise require an active BRAIN session.

For OpenRouter, add the key later from Settings. You can also configure a different provider/model per task.

## Important operating principle
The LLM does not define which BRAIN datasets or fields exist. The BRAIN catalogue does. The LLM receives validated catalogue context.

The Knowledge Vault is persistent application memory, not model fine-tuning.
