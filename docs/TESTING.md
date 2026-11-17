# OriMem 自测清单

这份清单按 agent 分开，用于验证钩子、采集、MCP 和每日总结。测试时建议使用独立的数据目录和测试库，不要和你正在使用的记忆系统混在一起。

## 0. 准备

```bash
pip install git+https://github.com/Wen5555/OriMem.git
```

```bash
orimem --home D:/orimem-test/home init --vault D:/orimem-test/vault --agents none --daily ask
```

如果本机已经有模型副本，可以加 `--model-from <目录>` 免去下载。

接下来每一节都先用 `install` 接入，测完再用 `uninstall` 移除。`--home` 会写进生成的 MCP 和钩子命令，所以测试数据只会落在 `D:/orimem-test`。

在测试库里放一篇笔记，方便验证召回：

```text
D:/orimem-test/vault/10 项目/测试项目.md
---
type: project
---
# 测试项目
## 当前状态
下一步是给导出功能加进度条。
```

```bash
orimem --home D:/orimem-test/home sync
```

## 1. Claude Code

```bash
orimem --home D:/orimem-test/home install --agent claude
```

这一步会在 `~/.claude/settings.json` 的 SessionStart、UserPromptSubmit、Stop 下各**追加**一条 OriMem 钩子，已有的钩子保持不动；同时注册 MCP 服务 `orimem`，并把技能 `orimem` 复制到 `~/.claude/skills`。

| 检查项 | 操作 | 期望结果 |
|---|---|---|
| MCP | 新开会话，问“测试项目下一步是什么” | 调用 `mcp__orimem__task_context` 或 `recall`，回答“给导出功能加进度条” |
| 每日询问 | 新开会话 | agent 在会话中问一次是否开启每日总结；回答后 `orimem --home … config get daily_summary` 显示 `true` 或 `false` |
| Stop 钩子采集 | 对话一轮后结束回合，等 5 秒 | `D:/orimem-test/home/sources/capture-log.jsonl` 出现一条 `"claude": {"scanned": 1, "captured": 1 …}`；`vault/90 归档/Codex佐证/<月份>/claude-<会话ID>-continuous.md` 出现 |
| 不采集的内容 | 打开上面那份转录 | 里面没有工具输出、思考过程和 `<system-reminder>` 之类的注入文本 |

```bash
orimem --home D:/orimem-test/home uninstall --agent claude
```

卸载后核对 `settings.json`，应当只少了 OriMem 那几条钩子，其余内容不变。

## 2. Codex

```bash
orimem --home D:/orimem-test/home install --agent codex
```

这一步会在 `~/.codex/config.toml` 末尾加一个 `# >>> orimem >>>` 标记块，在 `~/.codex/hooks.json` 里追加钩子（Windows 上通过 `commandWindows` 用 PowerShell 执行），并把技能复制到 `~/.codex/skills/orimem`。新的钩子可能要在 Codex 里审阅并信任之后才会生效。

| 检查项 | 操作 | 期望结果 |
|---|---|---|
| MCP | 新会话问测试项目下一步 | 调用 `recall` 等工具并给出正确回答 |
| Stop 钩子采集 | 结束一轮 | `capture-log.jsonl` 出现 `"codex": {"scanned": 1, "captured": 1 …}`，转录页以 Codex 会话 ID 命名 |
| 自动化排除 | 跑一次 Codex 自动化任务 | 自动化会话不出现在转录里 |

```bash
orimem --home D:/orimem-test/home uninstall --agent codex
```

## 3. DeepSeek Harness（dsh）

```bash
orimem --home D:/orimem-test/home install --agent dsh --dsh-profile desktop
```

这一步会在指定 profile 的 `cordis.patch.yml` 末尾插入 `dsh-mcp-client`（服务名 `orimem`）和 `dsh-hooks-claude-code` 桥接两个条目，并把技能复制到 `~/.dsh/skills/orimem`。桌面版开启了热重载时可以不重启；没生效就重启 dsh。

| 检查项 | 操作 | 期望结果 |
|---|---|---|
| 技能发现 | 问“可用技能里有没有 orimem” | 有 |
| MCP | 问测试项目下一步 | 调用 `mcp__orimem__recall`，答案正确 |
| 每日询问 | 新会话随便问一句 | 回答末尾问一次是否开启每日总结（这条提示来自 MCP 服务指令，不依赖 SessionStart） |
| Stop 钩子 | 一轮结束后查看会话日志里的 `hook/result` | `Stop` 和 `UserPromptSubmit` 的 `exitCode` 都是 0 |
| 采集 | 等 5 秒 | `capture-log.jsonl` 出现 `"dsh": {"scanned": 1, "captured": 1 …}`，转录页名为 `dsh-<uuid>-continuous.md`，只包含你输入的内容和模型的最终回答，没有运行时上下文或技能列表 |

dsh 通过当前会话的 shell 和沙箱来执行钩子。OriMem 的钩子自己不写任何文件，而是通知后台服务去采集，所以在 workspace-write 模式下也能工作。不过，如果 dsh 的沙箱在某个工作区根本起不来（例如报 `SetNamedSecurityInfoW failed`），钩子就会被拒绝执行，这时只能靠后台服务每 2 分钟一次的扫描来补采。

```bash
orimem --home D:/orimem-test/home uninstall --agent dsh --dsh-profile desktop
```

卸载后 `cordis.patch.yml` 会逐字节还原。

## 4. 每日总结（可选）

```bash
orimem --home D:/orimem-test/home daily run --dry-run
```

这一步只会打印将要执行的 `claude -p` 或 `codex exec` 命令和提示词，不会真正执行。

```bash
orimem --home D:/orimem-test/home config set daily_summary.enabled true
```

执行后会创建系统计划任务 `OriMem-DailySummary`；把这个值设为 `false` 时会删除它。真正跑一次要调用 agent，会产生费用：

```bash
orimem --home D:/orimem-test/home daily run
```

结果写在 `D:/orimem-test/home/daily-summary.log` 和 `vault/50 日记/每日/<日期>.md`。

## 5. 清理

```bash
orimem --home D:/orimem-test/home service-stop
```

然后删除 `D:/orimem-test`。

## 遇到问题时请记录

- `orimem --home … doctor` 的输出；
- `home/sources/capture-log.jsonl` 的最后几行；
- 钩子的 stderr：Claude 用 `--debug` 查看，dsh 在会话日志的 `hook/result.stderrSummary` 里。
