# Changelog

All notable changes. Versions follow [Semantic Versioning](https://semver.org/); the Python package uses
the equivalent PEP 440 form (`1.0.0rc1` = `1.0.0-rc.1`).

## 1.0.0-rc.1

First release candidate for 1.0. From 1.0 on, config keys, MCP tool names and schemas, CLI commands and the
on-disk formats below are kept compatible; changes go through migrations.

### New
- New projects get a project page automatically (`projects.link = "auto"`); older conversations of that folder
  are imported as evidence; linked sessions keep the page up to date (`projects.record`).
- `orimem onboard scan / import / status` brings conversations from before the install in, by folder.
- Personal profile note at the vault root, shared with every agent (`profile.share`).
- Language layouts (zh/en) with every folder configurable (`layout.*`); English skill and English agent texts.
- Lexical-only mode without the embedding model (`recall.mode = "lexical"`).
- 8 core MCP tools by default (`mcp.tools`, `mcp.extra`); transcript format checks; async Stop hooks.
- `orimem doctor` and `orimem init` print a readable summary (`--json` for the full data); `orimem prune`.

### Fixed
- The first search after a fresh install failed because the index had not been built yet.
- Starting `orimem mcp` before `orimem init` (plugin / registry entry) captured every past conversation;
  without a config OriMem now never captures, and an empty `capture.since` never backfills.
- The shared service now follows config changes immediately; settings are type-checked
  (`"false"` is false; lists must be lists); `journal_from` is read-only.
- Hook commands are quoted correctly for paths with spaces (Windows) and shell characters (POSIX).
- `~/.orimem` and the service key are private to the user on Linux/macOS.
- Agents can no longer write the evidence folder; profile edits need `profile_update: true`.
- Backups, receipts and batch records are pruned daily (`maintenance.*`).
- Symlinked/junctioned home or vault folders no longer block every write.
- A crash between writing and registering a transcript note, or a lost `~/.orimem`, no longer leaves the
  session in a permanent "managed projection edited" conflict.
- `orimem init` no longer crashes without a terminal; `orimem uninstall` removes the daily schedule;
  cron jobs keep the PATH and never replace an unreadable crontab.
- Upgrades refresh OriMem's own agent entries and vault scaffolding automatically.

### Formats
- `orimem.db` carries `PRAGMA user_version = 1`; adapter mirrors too. Newer schemas are refused.
- Evidence notes: language-neutral `<!-- orion:continuous:* -->` markers delimit the generated region;
  frontmatter `type: codex-history` (stable kind id for any agent), `projection: continuous-v1`,
  `source_platform`, `source_adapter`. Headings follow the vault language.

### Removed
- Single-user legacy maintenance code paths in the writer; `recall.model` (unused).

## 0.1.3
Projection race fix, lexical mode, language layouts, format checks, core tools, async capture.

## 0.1.2
MCP server starts without the embedding model.

## 0.1.1
First PyPI release (`orimem-mcp`), MCP Registry entry.

## 0.1.0
Initial public release.
