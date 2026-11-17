# Operations

The MCP server name is `orimem`; the CLI entry point is `orimem`.

- `health`: counts of bodies/chunks/vectors/relations, model version, source coverage and errors. Capturers are only inputs; do not assert from historical reports that current engineering work is done.
- `sync`: reconciles readable Markdown and recomputes incrementally; does not change body text. A rename shows up in the index as the old path invalidated and the new path added; cached vectors can be reused.
- `config_get` / `config_set`: view and change runtime configuration, such as `capture.enabled` (default `true`) and `daily_summary.enabled` (default `"ask"`, i.e. ask the user once whether to enable it, treated as off until agreed; can be set to `true`/`false`). Before changing, explain the impact, and set it according to the user's decision.
- The backend shares the model and database over a random port on local 127.0.0.1; the random auth field in the runtime state is only for local communication and must not be copied, printed or written into notes. The retrieval service stops after 30 minutes idle; the next MCP request restarts it and reconciles. Capture and projection are run independently by `orimem capture` and do not depend on the retrieval service being alive; use `capture_now` when an immediate capture is needed.
- Switching to a new model requires an explicit maintenance request, a pinned model file and hash. A model version change creates a new vector generation; on generation mismatch, semantic queries are refused.
- The database can rebuild the knowledge index from body text; history pagination mappings and the source ledger are re-migrated from retained authorized inputs. Do not delete original sources or the only body text to clean up the index.

Backups and vectors contain private data and stay on this machine only. Report any exclusions and existing source truncation per the migration manifest; do not claim to have migrated content that could not be read.

Continuous saving: health.history_write_policy=continuous-projection-with-legacy-snapshots; capture_versions/capture_snapshots store the observed source text, and capture_checkpoints are committed in the same transaction as projection/source-index jobs. last_projection and last_source_index report results separately; pending pagination state does not mean the continuous transcript is complete or distilled. Generated body text is encoded once from canonical messages, manual annotations continue to be indexed; repeated input writes no body text and adds no vectors.

`orimem capture` can perform saving and writer projection without a generation model/embedding. Rolling back code must not roll back newly added source DBs; database restores are rehearsed in isolation first, and a target that is neither the original nor the expected version is a conflict.

Capture is impossible while shut down/asleep; after resuming, missed capture is filled from existing inputs and checkpoints; source truncation is kept. A capture process that is running, or a recent stage reporting ok, only proves the observation window at that time; it does not mean login autostart, failure restart, sleep and long-term unattended operation have been verified.
