# Installing OriMem — guide for AI agents

You are an AI coding agent (Claude Code, Codex, DeepSeek Harness, Cline, …) asked to install OriMem for the user. OriMem gives agents a long-term memory in a local Obsidian vault through MCP. Follow these steps in order. Talk to the user in their language. Do not edit agent config files by hand: `orimem` does that itself and keeps backups. Never use `sudo`/administrator rights.

## 1. Ask the user once

Ask all of these in a single message and offer the defaults, so the user can just say "defaults":

1. **Vault folder.** Default: `~/.orimem/vault`. They can also point to an existing Obsidian vault. OriMem only adds folders and never rewrites existing notes.
2. **Folder language.** `zh` (Chinese) or `en` (English). Default: the language the user writes to you in.
3. **Conversation capture.** Default **on**: each finished turn of Claude Code / Codex / dsh is saved into the vault as a transcript note.
4. **Search mode.** Default **hybrid**, which downloads a ~490 MB multilingual embedding model once. **lexical** downloads nothing and uses full-text search only; it can be switched later.

Do not ask about the daily summary. OriMem asks the user itself, once, in a later session.

## 2. Install the `orimem` command (no prompts)

macOS / Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.sh | ORIMEM_INIT_ARGS=skip sh
```

Windows. This works from PowerShell, cmd and Git Bash:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -Command '$env:ORIMEM_INIT_ARGS="skip"; irm https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.ps1 | iex'
```

What the installer does:

- installs `uv` if it is missing;
- installs OriMem (~130 MB) into an isolated environment with its own Python;
- switches to mirrors automatically in mainland China.

If `orimem` is not found afterwards in your current shell, call it by full path:

- macOS / Linux: `~/.local/bin/orimem`
- Windows: `%USERPROFILE%\.local\bin\orimem.exe`
- Any platform: `uv tool dir --bin` prints the folder.

## 3. Configure

Fill in the user's answers:

```bash
orimem init --yes --vault "<vault folder>" --lang <zh|en> --capture <on|off> --daily ask --skip-model
```

- Add `--lexical` if the user chose lexical mode.
- `init` detects Claude Code, Codex and DeepSeek Harness. For each one it finds, it registers the MCP server, the `orimem` skill and the capture hooks. To limit this, add `--agents claude,codex`.
- The output is JSON. Keep the `obsidian.next_step` field for step 6.

## 4. Download the model (hybrid mode only)

```bash
orimem model download
```

The download is ~490 MB and can take several minutes. Give the command a long timeout (10 minutes or more) or run it in the background, and wait for it to finish. OriMem tries huggingface.co and then hf-mirror.com automatically.

If it still fails, either:

- tell the user, who can retry later with the same command; or
- switch to lexical mode with `orimem config set recall.mode lexical`.

## 5. Verify

```bash
orimem doctor
```

Check these fields:

- `recall_mode` is what the user chose. With `hybrid`, `embedder` must be `onnx`.
- `agents_detected` lists the agents you expected.
- `vault_exists` is `true`.

If any command printed `{"error": ...}`, show that message to the user. Do not work around it by editing files.

## 6. Tell the user

- **Restart** Claude Code / Codex / DeepSeek Harness so they load OriMem. That includes this session, if it is one of them.
- **Obsidian** is optional. Relay `obsidian.next_step` from step 3: either how to install Obsidian, or which folder to open as a vault. Later, `orimem open` opens the vault.
- **Next steps:**
  - ask an agent something like *"What did we decide about X last week?"* or *"Record that X is done"*;
  - change any setting with `orimem config set <key> <value>`;
  - remove everything with `orimem uninstall` and then `uv tool uninstall orimem-mcp`.
