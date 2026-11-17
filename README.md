# OriMem

**把 Obsidian 库变成编程 agent 的长期记忆。** OriMem 是一个本地 MCP 服务加一份通用 Skill，让 Claude Code、Codex 和 DeepSeek Harness（dsh）共用同一个记忆库：跨会话召回项目背景和历史对话，按验收流程把确认结果写回项目页、日记和知识，并自动采集对话建库。

- **一个后端，多家 agent**：同一个 `orimem` MCP 服务和同一份 `orimem` Skill，由安装器分别接入 Claude Code、Codex 和 dsh。其他支持 MCP 的工具可以用 `orimem snippet` 生成的配置手动接入。
- **混合检索**：Markdown 分块 + 多语言 E5 向量（本地推理）+ SQLite FTS5 + 双链，结果先返回短摘要，需要时再取原文证据。
- **可验收的写回**：`prepare_record` → 差异检查 → `commit_record`，带哈希校验、备份、回读和收据，不会静默覆盖你手动改过的笔记。
- **对话采集（默认开启）**：会话结束时钩子在后台增量保存对话，MCP 服务运行期间每 2 分钟也会补扫一次，投影成 `90 归档/Codex佐证` 下的转录页。子代理、工具输出、思考过程、注入的系统上下文和自动化会话都不采集。
- **每日总结（默认关闭）**：未设置时，MCP 服务会在指令里提示 agent 问你一次是否开启。开启后由系统计划任务（Windows 任务计划 / cron）每天调用一次 `claude -p` 或 `codex exec` 写当天日记。
- **全部本地运行**：不上传笔记；模型权重固定版本，下载后会校验 sha256。

## 支持的 agent

| Agent | MCP | Skill | 钩子（即时采集） | 对话来源 |
|---|---|---|---|---|
| Claude Code | `claude mcp add -s user`（找不到 CLI 时写入 `~/.claude.json`） | `~/.claude/skills/orimem` | `~/.claude/settings.json` | `~/.claude/projects/*/<uuid>.jsonl` |
| Codex | `~/.codex/config.toml` 中的 `[mcp_servers.orimem]` | `~/.codex/skills/orimem` | `~/.codex/hooks.json` | `~/.codex/sessions/**/rollout-*.jsonl` |
| DeepSeek Harness (dsh) | profile 的 `cordis.patch.yml` 插入 `dsh-mcp-client` | `~/.dsh/skills/orimem` | 默认不装（dsh 在自己的沙箱里运行钩子，写不到 OriMem 数据目录）；靠 MCP 服务定时扫描采集 | `~/.dsh/sessions/**/session.v4.jsonl.zstd` |
| 其他 MCP 客户端 | `orimem snippet` 输出 JSON 配置 | 手动复制 Skill 目录或 AGENTS.md 片段 | — | — |

## 安装

需要 Python 3.11+。依赖里有 PyTorch 和 transformers，用于本地向量推理，首次安装体积较大。

```bash
pip install git+https://github.com/Wen5555/OriMem.git
```

```bash
orimem init
```

`init` 会依次询问库路径、是否开启对话采集（默认开）和是否开启每日总结（默认“以后再说”），然后：

1. 写入 `~/.orimem/config.toml`；
2. 在库里补齐目录结构和库级规则 `AGENTS.md`（已有内容不覆盖）；
3. 下载嵌入模型 `intfloat/multilingual-e5-small`（固定版本，校验 sha256）；
4. 自动检测已安装的 agent，写入 MCP、Skill 和钩子。每个被修改的文件都会先备份到 `~/.orimem/backups/`。

常用参数：

```bash
orimem init --vault "D:/Obsidian/MyVault" --agents claude,codex,dsh --capture on --daily ask
```

```bash
orimem init --hf-endpoint https://hf-mirror.com
```

```bash
orimem init --model-from "D:/models/multilingual-e5-small"
```

装好后重启对应的 agent，让它重新加载 MCP 和 Skill。dsh 的 profile 开启了热重载时可以不重启。

## 设置

所有设置都在 `~/.orimem/config.toml`，也可以用命令行或 MCP 工具 `config_set` 修改：

| 键 | 默认 | 说明 |
|---|---|---|
| `vault` | `~/.orimem/vault` | Obsidian 库路径 |
| `capture.enabled` | `true` | 是否采集对话建库 |
| `capture.sources` | `["claude","codex","dsh"]` | 采集哪些 agent |
| `capture.since` | 安装时间 | 早于这个时间的旧会话不回填（`orimem capture --backfill` 可以手动回填） |
| `capture.exclude_cwd` | `[]` | 这些目录下的会话永不采集 |
| `daily_summary.enabled` | `"ask"` | `"ask"`：问一次，未回答视为关闭；`true` / `false` |
| `daily_summary.time` | `"22:30"` | 每日总结运行时间 |
| `daily_summary.agent` | 自动 | 用 `claude` 还是 `codex` 跑总结 |
| `layout.project_scopes` | `["10 项目","15 课程"]` | 项目入口所在目录 |
| `recall.hf_endpoint` | 空 | 模型下载镜像 |

```bash
orimem config set capture.enabled false
```

```bash
orimem config set daily_summary.enabled true
```

修改 `daily_summary.*` 时会立即同步系统计划任务：开启时创建，关闭时删除。

## 命令

| 命令 | 作用 |
|---|---|
| `orimem init` | 首次配置 |
| `orimem install / uninstall [--agent claude,codex,dsh] [--dsh-profile 名称]` | 接入或移除 agent；只删除 OriMem 自己加的条目，保留原文件的换行风格 |
| `orimem doctor` | 检查配置、模型、依赖和各 agent 的状态 |
| `orimem capture [--source X] [--backfill]` | 立即增量采集 |
| `orimem sync` / `orimem recall "问题" --scope "10 项目"` | 手动索引 / 检索 |
| `orimem daily status / run [--dry-run] / apply` | 每日总结 |
| `orimem model status / download / import <dir>` | 嵌入模型 |
| `orimem snippet` | 给其他 MCP 客户端用的配置片段 |
| `orimem mcp` | MCP stdio 入口，由 agent 调用 |

## MCP 工具

`task_context`、`recall`、`evidence`、`list_sessions`、`get_session_messages`、`initialize`、`prepare_record`、`commit_record`、`lifecycle`、`health`、`sync`、`archive_history`、`config_get`、`config_set`、`capture_now`。具体用法见 [Skill 说明](src/orimem/skill/orimem/SKILL.md)。

## 库结构

```
00 收件箱/   10 项目/   15 课程/   30 知识/   50 日记/每日|每周/   90 归档/Codex佐证/   95 AI 工作区/
```

写回只允许进入 `layout.folders` 列出的目录。`.obsidian` 和库外文件不会被读取或修改。

## 数据与隐私

- 笔记、数据库、向量和对话转录都只保存在本机：`~/.orimem/` 和你的库里。
- 采集时会剔除工具输出、思考过程、系统注入内容和子代理会话，常见的密钥和令牌格式会被脱敏。
- 卸载：

```bash
orimem uninstall
```

这会移除 MCP、Skill 和钩子，但库和 `~/.orimem` 会保留，需要时自行删除。

## 已知限制

- dsh 已用真实对话（`dsh headless`）验证过 MCP 调用、技能发现、每日询问和采集。dsh 的 headless profile 默认走需要 API Key 的官方接口；使用账号登录的话，需要把模型路由改成 `deepseek-account`。
- 只在 Windows 上做过完整测试；macOS 和 Linux 的 cron 调度及路径逻辑已经写好，但还没有实测。
- 自定义 `ORIMEM_HOME` 时，安装器会在 MCP、钩子和计划任务的命令里写入 `--home <目录>`，不依赖环境变量。

## 开发

```bash
pip install -e ".[test]"
```

```bash
pytest
```

## License

MIT
