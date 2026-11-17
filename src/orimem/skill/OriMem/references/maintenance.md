# 运行维护

MCP 服务名为 `orimem`；命令行入口为 `orimem`。

- `health`：正文／分块／向量／关系数量，模型版本，来源覆盖与错误。采集器仅为输入，不能据历史报告断言当前工程已完成。
- `sync`：对账可读 Markdown，增量重算；不改正文。重命名在索引中表现为旧路径失效、新路径加入，缓存向量可复用。
- `config_get` / `config_set`：查看和修改运行配置，如 `capture.enabled`（默认 `true`）与 `daily_summary.enabled`（默认 `"ask"`，即询问用户一次是否启用，未同意前视为关闭；可设 `true`/`false`）。修改前说明影响，按用户决定设置。
- 后端通过本机127.0.0.1随机端口共享模型与数据库，运行状态的随机鉴权字段只用于本机通信，不复制、输出或写入笔记。检索服务空闲30分钟停止，下一次 MCP 请求重新启动并对账。采集与投影由 `orimem capture` 独立执行，不依赖检索服务存活；需要立即补采时用 `capture_now`。
- 修改新模型须显式维护请求、固定模型文件与哈希。模型版本变化会建立新向量代际；代际不匹配时拒绝语义查询。
- 数据库可从正文重建知识索引；历史分页映射与来源台账从保留的已授权输入重新迁移。不要删除原来源或唯一正文来清理索引。

备份和向量包含私人资料，只保留本机。任何排除和既有来源截断按迁移清单报告，不能宣称迁移了无法读取内容。

连续保存：health.history_write_policy=continuous-projection-with-legacy-snapshots；capture_versions/capture_snapshots保存观察原文，capture_checkpoints与projection/source-index任务同事务提交。last_projection和last_source_index分别报告结果，pending分页状态不表示连续转录完成或已提炼。生成正文从canonical消息编码一次，手工注释继续索引；重复输入不写正文、不增加向量。

`orimem capture` 无需生成模型/embedding 即可执行保存与 writer 投影。回滚代码不得回滚掉新增来源DB；数据库恢复先隔离演练，目标不是原版或预期版则冲突。

关机/休眠期间无法采集，恢复后按已有输入与检查点补采；来源截断保持。采集进程正在运行或近期阶段 ok 只证明当时的观察窗口，不等于登录自启、故障重启、休眠及长期无人值守运行已验证。
