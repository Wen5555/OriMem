---
type: note
---

# 欢迎使用 OriMem

这个库由 OriMem 维护，Claude Code、Codex、DeepSeek Harness 等 agent 通过 MCP 在这里检索和记录。你可以像普通 Obsidian 库一样浏览、编辑。

## 从这里开始

- 项目页：`{projects}` —— 每个项目一页，agent 会在确认结果后增量更新状态与下一步。
- 每日记录：`{daily}` —— 开启每日总结后，每天自动整理。
- 对话佐证：`{evidence}` —— 采集到的对话转录，只作证据，不要手改程序转录区。
- 知识：`{knowledge}`；待整理：`{inbox}`。
- 规则：[[AGENTS]] 说明 agent 在本库中的读写边界。

## 常用命令

- `orimem doctor`：检查模型、钩子、采集和 Obsidian 状态。
- `orimem config get layout`：查看目录布局；`orimem config set layout.daily "日记/每日"` 可改名。
- `orimem open`：在 Obsidian 中打开本库。

可以删除这篇笔记，OriMem 不会重新创建。
