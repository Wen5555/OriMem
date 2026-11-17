# dsh-orimem

为 DeepSeek Harness 接入 [OriMem](https://github.com/Wen5555/OriMem)：把长期记忆存在你自己的 Obsidian 库里。

这个插件包会往 dsh profile 里插入两个条目，用的都是 dsh 自带的插件：

- `@deepseek-ai/dsh-mcp-client` 启动 `orimem mcp`，agent 因此获得 `mcp__orimem__*` 系列工具：`recall`、`evidence`、`task_context`、`prepare_record`/`commit_record`、`list_sessions`、`config_get`/`config_set`、`capture_now` 等。
- `@deepseek-ai/dsh-hooks-claude-code` 运行 OriMem 的 `UserPromptSubmit` 和 `Stop` 钩子。每轮对话结束时，钩子会通知 OriMem 服务把这个会话保存进库。

## 前提

插件包只负责把 dsh 接到 OriMem，所以需要先安装 `orimem` 命令，并确保它在 `PATH` 上。

Windows：

```powershell
irm https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.ps1 | iex
```

macOS / Linux：

```bash
curl -LsSf https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.sh | sh
```

`orimem init` 会建好库、下载嵌入模型、写入钩子文件 `~/.orimem/dsh-hooks.json`，并把 `orimem` 技能装进 `~/.dsh/skills`。如果某个 profile 已经加载了这个插件包，`orimem install --agent dsh` 就不会再改那个 profile 的 patch，避免重复注册 MCP 服务。

装好插件包后请重启 DeepSeek Harness，让它读到更新后的 `PATH`。

## 说明

- 钩子文件不存在时，dsh 只会记一条警告并跳过钩子；MCP 服务运行期间仍会每 2 分钟采集一次新会话。
- dsh 会在会话沙箱里运行钩子。OriMem 的钩子只负责通知本机的 OriMem 服务，真正写入是由服务在沙箱外完成的。
- 设置（是否采集、每日总结）保存在 `~/.orimem/config.toml`，也可以直接让 agent 帮你改。

MIT 许可证。问题反馈：https://github.com/Wen5555/OriMem/issues
