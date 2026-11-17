<div align="center">

<img src="assets/icon.png" width="96" alt="OriMem icon">

# OriMem

**Long-term memory for coding agents, kept in your own Obsidian vault.**

One local MCP server and one skill, shared by Claude Code, Codex and DeepSeek Harness —
recall project context across sessions, write confirmed results back with receipts, and capture every conversation automatically.

[![CI](https://github.com/Wen5555/OriMem/actions/workflows/ci.yml/badge.svg)](https://github.com/Wen5555/OriMem/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)
![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**English** · [简体中文](README.zh-CN.md)

</div>

<!-- mcp-name: io.github.Wen5555/orimem -->

---

## Why OriMem

Coding agents forget everything when a session ends, and each agent keeps its history in its own private format. OriMem gives them **one shared, human-readable memory**:

- 🗂️ **Your vault is the memory.** Plain Markdown in an Obsidian vault you can read, edit and back up. The index is just a cache.
- 🔎 **Hybrid recall.** Multilingual E5 embeddings + SQLite FTS5 + wiki-links. Short snippets first, exact evidence on demand, so context stays small.
- ✅ **Verified write-back.** `prepare_record → diff → commit_record` with hash checks, backups, read-back and receipts. It never silently overwrites a note you edited.
- 🎙️ **Automatic capture.** Hooks save each Claude Code / Codex / dsh session as a transcript note when a turn ends. Tool output, reasoning, injected system context, sub-agents and automations are left out.
- 📅 **Daily summary (opt-in).** The agent asks you once; if you agree, a scheduled job writes the day's journal through the same verified writer.
- 🔒 **Local only.** No cloud calls, no telemetry. The embedding model is pinned and checksum-verified, and runs on CPU through ONNX Runtime (no PyTorch needed).

## Quick start

**Let your agent install it** (recommended). Paste this into Claude Code, Codex, DeepSeek Harness or any coding agent:

```text
Install and set up OriMem for me by following https://raw.githubusercontent.com/Wen5555/OriMem/main/llms-install.md
```

The agent asks you four questions (vault folder, folder language, capture, search mode), installs without further prompts, connects every agent it finds, verifies with `orimem doctor` and tells you what to restart. The steps it follows are in [llms-install.md](llms-install.md).

**Or run the installer yourself:**

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.ps1 | iex
```

**macOS / Linux**

```bash
curl -LsSf https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.sh | sh
```

The installer sets up [uv](https://github.com/astral-sh/uv) if needed, installs OriMem into an isolated environment (about 130 MB, Python provided by uv), then runs `orimem init`. `init` asks a few questions — vault path, folder language (Chinese or English, from your system locale), conversation capture (default on), daily summary (default "ask me later") — downloads the 490 MB embedding model, and connects every agent it finds. Restart your agents afterwards.

No Obsidian yet? Not required: the vault is a plain Markdown folder. `init` prepares it for a good first open (welcome note, new notes to the inbox, daily notes to the daily folder) and tells you how to install or add it; `orimem open` opens it later. Short on disk or offline? `orimem init --lexical` skips the model and uses full-text search only (switch back any time with `orimem config set recall.mode hybrid`).

| What | Size |
|---|---|
| OriMem environment (onnxruntime, numpy, tokenizers, …) | ~130 MB |
| Embedding model (`model.onnx` + tokenizer) | ~490 MB (none with `--lexical`) |
| uv + Python 3.12, only if not already installed | ~70 MB + ~70 MB |
| **Typical total** | **~0.6 GB** (~0.75 GB on a bare machine; ~0.13 GB lexical-only) |
| Optional PyTorch backend (`orimem-mcp[torch]`) | +1 GB or more |

Then just talk to your agent:

> *"What did we decide about the export feature last week?"*
> *"Record that the WebGPU renderer is done and the next step is drag-to-reorder layers."*

<details>
<summary>Other ways to install</summary>

```bash
uv tool install orimem-mcp
```

```bash
pipx install orimem-mcp
```

The PyPI distribution is `orimem-mcp`; the command it installs is `orimem`. To run the MCP server without installing it first, use `uvx --from orimem-mcp orimem mcp`.

```bash
orimem init
```

Non-interactive: `orimem init --yes --vault ~/Notes --agents claude,codex`. Behind a slow network, the installer switches to mirrors automatically when PyPI is unreachable (`ORIMEM_MIRROR=cn` forces it).
</details>

## Supported agents

| Agent | MCP server | Skill | Hooks (capture on turn end) | Transcripts read from |
|---|---|---|---|---|
| **Claude Code** | `claude mcp add -s user orimem` | `~/.claude/skills/orimem` | `~/.claude/settings.json` | `~/.claude/projects/*/<uuid>.jsonl` |
| **Codex** | `[mcp_servers.orimem]` in `~/.codex/config.toml` | `~/.codex/skills/orimem` | `~/.codex/hooks.json` | `~/.codex/sessions/**/rollout-*.jsonl` |
| **DeepSeek Harness (dsh)** | `dsh-mcp-client` entry in the profile's `cordis.patch.yml` | `~/.dsh/skills/orimem` | `dsh-hooks-claude-code` bridge | `~/.dsh/sessions/**/session.v4.jsonl.zstd` |
| Any other MCP client | `orimem snippet` prints the JSON | copy the skill folder | — | — |

**Claude Code users** can also install it as a plugin (MCP server, skill and hooks in one step; still needs the `orimem` command):

```text
/plugin install orimem --marketplace Wen5555/OriMem
```

On older Claude Code versions: `/plugin marketplace add Wen5555/OriMem`, then `/plugin install orimem@orimem`.

**DeepSeek Harness users** can also add the [`dsh-orimem`](integrations/dsh) bundle from the community plugin market (or `dsh plugin --profile <name> add dsh-orimem-<version>.tgz` from a [release](https://github.com/Wen5555/OriMem/releases)); it needs the `orimem` command from the quick start.

`orimem install` and `orimem uninstall` only add or remove OriMem's own entries, and every edited file is backed up first. Marker blocks in TOML/YAML files are removed byte for byte; JSON files keep all other content.

## How it works

```mermaid
flowchart LR
    subgraph Agents
        CC[Claude Code]
        CX[Codex]
        DS[DeepSeek Harness]
    end
    CC & CX & DS -- "MCP (stdio)" --> M[orimem mcp]
    CC & CX & DS -. "Stop hook" .-> H[orimem hook]
    M --> S[(local service<br/>loopback only)]
    H -- "capture this session" --> S
    S --> I[(SQLite: chunks · E5 vectors<br/>FTS5 · links · ledger)]
    S <--> V[/"Obsidian vault<br/>(Markdown = source of truth)"/]
    S -- "every 2 min" --> T[agent transcripts]
```

- **Recall:** `task_context` binds the current project; `recall` returns ranked snippets; `evidence` fetches exact source text with offsets and hashes.
- **Write-back:** the agent proposes a complete note change; OriMem checks the expected hash, backs up, writes, re-reads, verifies the claimed text and issues a receipt.
- **Capture:** per-agent parsers mirror ordinary user/assistant turns into one pipeline, which stages them and projects stable transcript notes into the vault's evidence folder. The Stop hook only hands the session to the running service (falling back to a detached capture process) and is registered as `async` for Claude Code and Codex, so it never delays a turn; dsh runs hooks synchronously, where it returns in a fraction of a second. Context hooks (session start, prompt submit) stay synchronous because their output is the injected project context. Every parse records the client/format version and checks the transcript shape; unsupported formats are not captured and drift shows up in `orimem doctor`.

## Configuration

Settings live in `~/.orimem/config.toml`. Change them with `orimem config set <key> <value>` or ask your agent, which uses the `config_set` tool.

| Key | Default | Meaning |
|---|---|---|
| `vault` | `~/.orimem/vault` | Obsidian vault path |
| `capture.enabled` | `true` | Capture conversations into the vault |
| `capture.sources` | `["claude","codex","dsh"]` | Which agents to capture |
| `capture.since` | install time | Older sessions are not back-filled (`orimem capture --backfill` does it on request) |
| `capture.exclude_cwd` | `[]` | Never capture sessions started in these folders |
| `daily_summary.enabled` | `"ask"` | `"ask"` = ask once, off until answered · `true` · `false` |
| `daily_summary.time` | `"22:30"` | When the daily job runs |
| `daily_summary.agent` | auto | `claude` or `codex` runs the summary |
| `recall.mode` | `"hybrid"` | `hybrid` = vectors + full text (needs the model) · `lexical` = full text and links only, no model |
| `recall.hf_endpoint` | auto | Model download endpoint (falls back to hf-mirror.com) |
| `mcp.tools` | `"core"` | `core` publishes 8 everyday tools · `all` adds the advanced ones |
| `mcp.extra` | `[]` | With `core`: individual advanced tools to publish, e.g. `["health"]` |
| `layout.language` | system locale | `zh` or `en` folder preset |
| `layout.<role>` | preset | Override one folder: `inbox`, `drafts`, `projects`, `courses`, `areas`, `knowledge`, `daily`, `weekly`, `archive`, `evidence`, `legacy`, `workspace`; also `course_home`, `weekly_note`, `knowledge_index` |
| `layout.extra_writable` | `[]` | Further folders automatic write-back may touch |

## Reference

<details>
<summary><b>CLI</b></summary>

| Command | What it does |
|---|---|
| `orimem init [-y]` | First-time setup |
| `orimem install / uninstall [--agent claude,codex,dsh] [--dsh-profile NAME]` | Connect or disconnect agents |
| `orimem init [-y] [--lang zh\|en] [--lexical]` | (re)run setup with a folder language / without the model |
| `orimem doctor` | Check config, model, embedder, layout, Obsidian, transcript formats, dependencies and agents |
| `orimem open` | Open the vault in Obsidian (or say how to install/add it) |
| `orimem capture [--source X] [--session ID] [--backfill]` | Capture now |
| `orimem sync` · `orimem recall "query" --scope "10 项目"` | Index / search from the shell |
| `orimem daily status / run [--dry-run] / apply` | Daily summary |
| `orimem model status / download [--torch] / import DIR` | Embedding model |
| `orimem config get / set / path` | Settings |
| `orimem snippet` | MCP JSON + AGENTS.md snippet for other clients |
| `orimem --home DIR …` | Use another data directory (written into generated agent commands) |
</details>

<details>
<summary><b>MCP tools</b></summary>

Published by default (8):

| Tool | Purpose |
|---|---|
| `task_context` | Bind the current folder/task to a project entry |
| `recall` | Hybrid (or lexical) search, snippets |
| `evidence` | Exact source text for result ids |
| `list_sessions` · `get_session_messages` | Browse captured conversations |
| `prepare_record` · `commit_record` | Verified write-back |
| `config_set` | Record a setting the user decided (e.g. the daily summary) |

Advanced (`orimem config set mcp.tools all`, or list some in `mcp.extra`):

| Tool | Purpose |
|---|---|
| `initialize` | Create a project/course entry with a workspace binding |
| `lifecycle` | Source reading, candidates, corrections |
| `health` · `sync` · `archive_history` | Maintenance |
| `config_get` · `capture_now` | Read settings, capture now |
</details>

<details>
<summary><b>Vault layout</b></summary>

`init` picks the Chinese or English preset from your locale (`--lang` to choose); every folder can be renamed with `orimem config set layout.<role> "<path>"` (existing notes are not moved). `orimem config get layout` shows the resolved layout.

```text
English preset                    Chinese preset
00 Inbox/ (AI Drafts/)            00 收件箱/ (AI 草稿/)      inbox, knowledge candidates
10 Projects/                      10 项目/                   one page per project
15 Courses/<course>/Course Home   15 课程/<课程>/课程主页
30 Knowledge/                     30 知识/                   reusable knowledge
50 Journal/Daily · Weekly         50 日记/每日 · 每周
90 Archive/Conversations/         90 归档/对话佐证/          captured transcripts
95 AI Workspace/                  95 AI 工作区/              agent work notes
```

Automatic writes are limited to projects, courses (homepage only), knowledge, daily/weekly journals, AI drafts, the AI workspace and the evidence folder (plus `layout.extra_writable`). `.obsidian` is only written once, when `init` creates a vault Obsidian has never opened; files outside the vault are never touched. Vaults set up by 0.1.x keep `90 归档/Codex佐证`.
</details>

## FAQ

**Do I need Obsidian?** No. Agents read and write the Markdown folder directly; Obsidian is the optional UI. `orimem doctor` shows whether it is installed and whether the vault is added.

**Can I run without the embedding model?** Yes: `orimem config set recall.mode lexical` (or `init --lexical`). Recall then uses SQLite full-text search and links only; set `hybrid` and run `orimem model download` + `orimem sync` to get vectors back.

**Do the hooks slow my agent down?** Capture runs in the background (`async` Stop hook in Claude Code and Codex). The session-start and prompt hooks take about a quarter of a second because they inject project context.

**Do I need a GPU or PyTorch?** No. Embeddings run on CPU through ONNX Runtime. `orimem[torch]` plus `orimem model download --torch` enables the optional PyTorch backend, which produces identical vectors.

**Can I use my existing vault?** Yes. Point `vault` at it. OriMem only adds the folders it needs and never rewrites existing notes outside the verified write-back flow.

**I'm in mainland China.** Both installers detect an unreachable PyPI and switch to the Tsinghua PyPI mirror, the npmmirror Python builds and hf-mirror.com.

**How do I turn capture off for a project?** `orimem config set capture.exclude_cwd '["D:/work/secret"]'`. To turn it off everywhere: `orimem config set capture.enabled false`.

**How do I remove everything?** `orimem uninstall`, then `uv tool uninstall orimem-mcp`. Your vault and `~/.orimem` stay until you delete them.

## Roadmap

- [x] Configurable / English vault layout
- [x] Full-text-only mode without the model
- [ ] More agents (Cursor, Gemini CLI, OpenCode)
- [ ] Optional quantized ONNX model for low-memory machines

## Contributing

```bash
git clone https://github.com/Wen5555/OriMem && cd OriMem
```

```bash
uv venv && uv pip install -e ".[test]"
```

```bash
uv run pytest
```

A manual end-to-end checklist for each agent is in [docs/TESTING.md](docs/TESTING.md). Issues and pull requests are welcome.

## License

[MIT](LICENSE)
