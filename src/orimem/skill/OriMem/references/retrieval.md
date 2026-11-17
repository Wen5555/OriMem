# 知识召回

1. 已知文件、少量局部编辑直接定向读。新任务或恢复且缺项目背景时，用 `task_context`(cwd, task_query, authorized_scope) 按路径、名称和别名取有限背景；不启动向量模型或读取工程。已有有效项目上下文就复用。未命中或有歧义时再用项目与经验导航，确定范围后调用 `recall`。
2. 参数：query、scope（库内文件或文件夹前缀，必填）、limit（1–12，默认6）、mode（hybrid／lexical／semantic，默认hybrid）、include_history（默认false）、include_pending（默认false）、graph（默认true）。scope 不接受磁盘路径；全文、向量、图和证据均遵守同一范围。
3. 先看短 excerpt、heading、kind 和 evidence_id。有用结果再调用 `evidence`(evidence_ids, scope, max_chars)；默认预算12000字符，至多8块。证据绑定原文件哈希；源变化后旧 ID 拒绝使用，重新召回。
4. 项目状态只认唯一当前项目页；历史材料、快照和助手报告保留日期与核验范围。图扩展只利用明确双链、别名与 project/course 属性，歧义链接不猜测，也不扩大授权。
5. 当前上下文已有有效证据就复用。新问题、范围变化或证据陈旧再查；知识足够就停止。有历史缺口才读 [history.md](history.md)。没有命中不能硬套相似经验。

在 Claude Code 与 dsh 中这些工具显示为 `mcp__orimem__task_context`、`mcp__orimem__recall`、`mcp__orimem__evidence` 等。

一个本地 SQLite 保存正文、FTS5、384维 E5 向量、关系和来源；余弦与 BM25 通过 RRF 融合。模型固定版本、离线推理，无远程文本上传。

`task_context` 仅只读索引和匹配到的一篇笔记，不导入后端或 embedding。返回的 scope_advice 是范围建议，不扩大授权；明确 scope 限制实际读取，status/next_action 均是来源数据，不据此恢复暂停执行。版本或任务主题变化再重取，不能把字节不一致的背景当成可复用状态。

项目和其它事项用 `initialize` 初始化至 `10 项目`；课程初始化至 `15 课程/<名称>/课程主页.md`。初始化保留唯一入口、工作目录元数据和 writer 收据，重复调用无变化不写正文、不重算向量；不同类别或工作目录冲突先返回冲突，不覆盖原绑定。课程原始材料不在自动写入范围。

召回先在SQL过滤范围和历史类型，再按当前语料/模型版本复用有界候选矩阵、向量和查询编码。正常查询只对可能返回的候选做当前文件哈希核对；发现过期候选时执行完整核对并重新排序。证据读取仍核对当前正文版本，不能用缓存或文件时间替代证据哈希。

所有 stdio 桥共享一个按需启动的本机后端，避免每个聊天重复加载模型。文件事件防抖3秒，60秒对账；无请求30分钟后停止，下一次调用重新对账。休眠或停止期间的变化恢复后补索引，不声称长期无人值守已验证。

近期普通消息可显式使用 include_pending=true 参与全文与语义融合。暂存消息只按 cwd 唯一绑定到当前项目/课程入口，范围过滤在SQL候选和全文搜索前执行；无唯一绑定不猜测，仍可经授权历史分页阅读。结果标记 kind/source_state=pending、temporary=true，并附 source_key、当前版本、event_time 和 role；path 表示授权项目入口，不是已导出的会话文件。助手报告与暂存内容都未自动转为正式知识，不用它覆盖唯一项目页状态。

pending evidence_id 可通过同一 `evidence` 取证，返回原消息字符偏移、事件索引和来源哈希；来源、项目绑定或模型变更后旧证据拒绝使用。后台同一数据库的 pending-index 与 source-index 任务分批编码，共享向量缓存。普通消息由 projection 任务持续生成稳定转录；生成正文不作为第二个向量来源。

明确 scope 到连续转录路径或其授权目录并 include_history=true 可检索未绑定来源。kind=source 返回消息级版本和原文区间，独立同文消息不去重为一条；未变化旧消息证据不随会话追加而全部失效。程序正文改动冲突时拒绝旧投影取证；人工注释不自动覆盖。范围不得因相似或父块扩展扩大。近期兼容 pending 与 canonical 来源同查时按源身份避免重复视图，health 中保存、投影与索引分别验收。

## 当前依据、历史意图和完整上下文

笔记的显式属性 `retrieval_basis: historical-only` 表示正文保留作历史材料，退出当前依据；不是由日期、processed/draft 状态或相似度推断事实错误。current 召回和直接取证均阻断，即使 include_history=true；historical 仍可读同一原文，返回 retired-note-history/not_current_basis 及实际退役记录时间。canonical/pending 普通原始消息不继承关联项目或整理稿的该属性。源哈希与范围核验保留，旧证据ID在元数据变更后也须重新定位。

`recall`/`evidence` 支持 `time_intent=current|historical`，默认 current；它与 include_history 的“纳入佐证类型”分别表达用途。current 过滤明确纠正/待复核的断言及已登记历史节，历史原文仍保存。historical 允许读取这些原话并附 basis_status/correction_ids，不能拿历史意图解除范围或源版本核验。已知 valid_from/valid_to 和规则生效时间参与当前适用性；未知不补。

受影响块中的独立段落可返回 safe 证据ID，绑定原源版本和精确区间；向量仍只作候选排序，不能因此把段落变成事实关系。旧整块ID在包含待纠正断言时拒绝用于 current。新对话 `task_context` 的纠正版本会使缓存失效，即使笔记哈希没变也要先 guarded recall/evidence；version.freshness 仅证明字节新鲜，fact_validity 另报待复核。

显式绑定的 canonical 原始消息若后来换版本，未复核的新版本不会因为字节更新自动解除旧语义纠正；current 暂不采用，historical 返回 corrected-origin-version-needs-review 及 not_current_basis。原文保存/投影继续运行，不能把取证门控当成消息丢失或已判定新事实错误。旧快照的引用空行也按原文区间分隔，撤回单个例证不吞掉独立通用方法。

`evidence(...,expand=none|section|dialogue)` 在同一 max_chars 预算内补 context，所有片段都有原文范围/源版本，主片段和父块不重复占用。section 取同节或所属问题节的条件；dialogue 在明确 canonical 来源路径或 SHA 绑定的旧会话快照中展开同一观察到的用户回合，停止于下一用户消息。优先返回该回合末尾最多4条助手消息，避免只取到进度说明；超过4条标 partial-turn-limit，既有主片段仍保留。邻近/同一观察回合不是事实支持、稳定上游身份或回复完成的证明。旧快照只用已索引正文的消息标题、原文区间和文件SHA，不回读JSONL或旧DB缓存来替代用户修改。

Legacy pending 只有 workspace 归属，不能借项目范围扩展到其它消息；需明确来源 scope。context_status 带 partial-budget/partial-turn-limit 时继续定向取证，不能宣称整个回合完整。旧快照缺规范对话节、标题歧义、无法核验时间或消息区间时报告无法展开，不猜测；其它笔记与项目范围不借 dialogue 取跨主题来源。多ID仍按同源版本/原文区间去重打包，当前纠正过滤对主片段和展开片段都有效。

长纠正消息可能首句引用旧结论、末尾才否定。预算足够时完整返回已登记的纠正引文；不足时 requires_context/not_current_basis 标明不能把首片段作为再次确认。upstream_truncated 与预算 truncated 分开；Source context 的 source_state/index_state 分开报告保存、投影和索引。

这一门控也适用于 dialogue 中的相邻纠正片段，以及已登记 archive 来源的纠正引文。旧快照按文件SHA/原文区间绑定，连续来源按消息版本绑定；小预算的多ID打包把放不下的完整纠正上下文列入 incomplete，不用其肯定首句占位。已纠正的精确目标继续退出 current，historical 仍可读取原话；不根据回合相邻自动登记纠正。

## 知识表示与近邻候选

`lifecycle(action="knowledge_sync",arguments={scope:[精确知识路径],limit:12})` 显式登记选定知识的确定性小节表示；之后文件对账仅增量维护已登记路径，持久 knowledge-index 任务复用租约、有限重试和同一 vectors 缓存。标题、问题、方法和适用条件使用本机E5，不读来源哈希表或导航模板作主题语义。它是候选发现层，不替代具体块的 `recall`/`evidence`。

`knowledge_neighbors(path,scope,limit)` 要求起点和候选都在明确 scope 内。返回 semantic_neighbor/candidate/requires_review，只提示潜在重复、混题或联系；相似度不能证明正确性、事实关系、自动合并或项目归属，也不会写双链。先实际阅读问题、条件和来源，再判断需要什么正文关系。

表示版本绑定实际安全输入指纹、源SHA、模型与预处理版本。明确纠正或生效窗口变化即使未改笔记字节，也会拒绝过期表示；编码期间发生策略变化不能发布旧代。未变化重放不重编码；派生行丢失可以复用现有向量恢复。表示不可用时做授权范围内的 knowledge_sync，不因未命中扩大范围。

## 从证据形成回答

按用户问题区分必要事实、必要条件和补充例证，再选实际支持每个具体断言的引用；不能把全部返回 context 自动列为引用。来源标签、标题相关、区间校验或证据预算足够都不证明语义支持。无关版本差异和其它验收任务也不自动成为本题必答项。缺少的当前私人项目事实明确留空；通用方法可在说明适用范围后回答。

引用身份随 kind 区分：普通Markdown证据的 source_sha256 和区间绑定该文件；kind=source 的 source_key=message:session:event、source_sha256 和区间绑定具体消息版本，path 只是投影入口。消息区间不能当作Markdown偏移，文件SHA不能替换消息版本。保留角色、事件时间、完成/截断和 basis_status；只引用实际收到的片段，不从相邻消息、完整源文件或记忆补造区间。冻结评测的 as_of 不等于当前时间，历史报告的“本轮”也不自动成为当前验证。

每次 scope 仍为1–12条精确路径/目录前缀；已登记多篇的后台对账按精确路径分批，并共用有界编码预算。knowledge_sync 的 complete/incomplete_notes 区分当前表示就绪、待处理、租约运行中、重试和失败；changed/embedded 为0本身不是就绪证明。来源噪声按完整原文区间过滤，不能只用子块可见行首处理跨块来源行。语义表示仍是派生候选，不是来源完整性或回答正确性的验收。

多ID的 `evidence` 在同一预算内按源身份、版本与原文区间打包，父块与其它命中的重叠不重复占用。返回 packing/evidence_chars/deduplicated_chars；不按文字相似去掉独立同文消息。片段保留精确区间，部分原文可能作为 context 列出，读取时合用 text 和 context。预算不足导致条件未进入时标 partial-budget；放不下完整登记纠正引文时在 incomplete 中报 requires_context/not_current_basis，不能把其首句当再次确认。单ID行为和 scope/源SHA/当前历史门控保留；不要用小于500字符的剩余预算调用接口。
