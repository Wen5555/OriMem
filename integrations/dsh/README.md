# dsh-orimem

[OriMem](https://github.com/Wen5555/OriMem) for DeepSeek Harness: long-term memory kept in your own Obsidian vault.

This bundle adds two entries to your dsh profile, both using dsh's own plugins:

- `@deepseek-ai/dsh-mcp-client` starts `orimem mcp`, so the agent gets the `mcp__orimem__*` tools: `recall`, `evidence`, `task_context`, `prepare_record`/`commit_record`, `list_sessions`, `config_get`/`config_set`, `capture_now` and others.
- `@deepseek-ai/dsh-hooks-claude-code` runs OriMem's `UserPromptSubmit` and `Stop` hooks. When a turn ends, the hook asks the OriMem service to save that session into the vault.

## Requirements

The bundle only connects dsh to OriMem; the `orimem` command must already be installed and on `PATH`.

Windows:

```powershell
irm https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.ps1 | iex
```

macOS / Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.sh | sh
```

`orimem init` creates the vault, downloads the embedding model, writes the hooks file `~/.orimem/dsh-hooks.json` and installs the `orimem` skill into `~/.dsh/skills`. When a profile already loads this bundle, `orimem install --agent dsh` leaves that profile's patch alone, so the MCP server is never registered twice.

Restart DeepSeek Harness after installing the bundle, so that it sees the updated `PATH`.

## Notes

- If the hooks file is missing, dsh logs a warning and skips the hooks. The MCP server still captures new sessions every 2 minutes while it is running.
- dsh runs hooks in the session's sandbox. OriMem's hook only notifies the local OriMem service, which writes outside the sandbox.
- Settings (capture on/off, daily summary) live in `~/.orimem/config.toml`; you can also just ask the agent to change them.

MIT licensed. Issues: https://github.com/Wen5555/OriMem/issues
