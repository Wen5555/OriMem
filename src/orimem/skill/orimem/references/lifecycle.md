# 自动整理与知识形成

用于来源提炼、阶段整理、正式知识合并和日周材料选择。归档、实际阅读、归纳写回、索引和任务使用分别验收。语义判断由当前代理或用户配置的整理任务完成，E5只编码；后台队列本身不代表模型已经读过或归纳。

## 判断目标

先比较已有主题与项目，再阅读必要来源。已确认目标、限制、结果、障碍、下一步与纠正进入唯一项目页；暂停项目只更新记录。每日记录实际推进、交付、决定与变化；周期复盘选择里程碑、原因、跨日趋势、反复问题和未解决事项。普通问答、寒暄、未采纳建议与维护回声跳过正文写入。

长期可复用、来源可核对、适用条件清楚、没有重要未解冲突、已与现有知识比较、可独立解释的方法，可以自动写入或合并 `30 知识`。一份高质量来源可以足够；历史案例未经本轮重跑并不自动否定通用方法，但具体旧结果只能标为历史报告。解释和推断注明局限，不伪造用户已审阅。

有价值但证据或解释不足的候选放 `00 收件箱/AI 草稿`，说明已有依据、候选结论、边界、待核实点和推动转正的证据。新的支持、冲突或纠正到来后重新判断。不要一会话生成一篇文章或用全部待核实替代实际提炼。

## 来源阅读与登记

统一MCP工具 `lifecycle`(action, arguments) 支持 `discover`、`discover_pending`、`sources`、`read`、`ack`、`coverage`、`review`、`route`、`claim`、`finish_job` 和 `health`。新增动作在旧会话缓存的工具目录中可能尚不可见，在新会话重新加载即可，不启动另一套索引。arguments 只保存所选元数据或变更，不整库复制聊天。

`sources` 分页返回当前版本；来源键为 `archive:<session_id>` 或 `pending:<session_id>`。`read` 参数为 `source_key, version(可选), start, max_chars`，块最多24000字符，默认12000。返回 `ack_token,content_sha256,next_start`。实际看过完整返回块后，调用 `ack(token,content_sha256)`，再按 `next_start` 续读。工具输出截断必须补读；不得批量确认未进入上下文的正文。偏移是返回文本的Unicode字符位置，保留原换行；版本哈希绑定原始归档字节或规范暂存表示。

`review` 需要 `source_key,version,disposition,reason,comparison,claims`。处置为 `merged/formal-knowledge/candidate/no-reusable-knowledge/conflict/needs-evidence`，必须真实读全。比较列表使用已读的现有库内路径。每个claim包含 `text,path,heading(完整标题数组),strength,boundary,quote,start,end`；正式知识另有 `promotion_reason`。核验强度选 `user-confirmed/historical-report/inference/verified-artifact`。精确引文须支持主张；哈希校验不代替语义核验。

无复用结果可以只登记元数据。包含主张时先登记为待写回，经共享writer完成正文和索引后才结案。台账不是第二份项目状态；主张和引用用于证据关联，不生成平行项目台账。

## 日周材料与恢复

`route` 记录 `source_key,version,material_kind,quote,start,end,reason,event_time(可空),primary_path(可空),period_significance(可空)`。实际读过引文后使用。材料类型为 `progress/decision/deliverable/obstacle/correction/experience/question/chatter/suggestion/maintenance`。推进类且事件日期有依据才进入日队列；具有周期意义再进入周队列。启用自动记录之前的历史提炼不例行制造旧日报；知识与项目按当前授权合并。日期未知留空，同一事实主要正文只写一次，其它入口引用。

语义提炼由后台登记持久任务、由有执行机会的代理判断；原文保存和连续projection由程序独立执行，不等待语义提炼。`claim(kind,owner,lease_seconds)` 领取有界租约；异常用 `finish_job(job_id,owner,error)` 保留有限重试。归纳完成需要读全和处置，周期任务完成需要对应writer收据，索引任务由哈希与收据对账完成。收据或任务触发均不能证明来源事实正确。

即时、阶段、手动周期与已启用的定时整理（`daily_summary.enabled`）使用同一判断和写回逻辑。周期计划提供 `period.from_time,through,timezone_name,project_scope`，检查完整区间的真实成功覆盖，缺口保留待补。未结束的周期只记录实际范围，不能把阶段覆盖声称为全周完成。

`project_scope` 使用明确库内相对路径列表；限定范围的成功扫描不能推进全局水位或完成其他项目。每个请求项目需要整个时间区间的覆盖，来源范围未知或该项目仍有待补时不能通过。周收据区分范围内周期完成和全部授权来源的整周完成；单条daily材料的收据结案不等于整日扫描完整。字段说明见 [automatic-journaling.md](automatic-journaling.md)。

当前来源修改或缺失会使依赖进入复核队列；这只表示待重新判断，不能自动替用户决定新的事实。持久队列、阶段健康和读取覆盖可以通过 `health` 检查。新对话Hook、召回质量评测与长期无人值守须分别真实验证，不从队列存在推断已完成。

## 目标变化与复核结案

`reconcile_targets` 核对已记录主张的实际标题段落与当前来源；无关段落修改保留仍有效的主张，目标删除或断言改变进入复核。唯一字节相同的已索引目标可迁移关联；多个相同副本不猜测。带稳定session_id的历史佐证改名可恢复来源绑定，不移动实际文件。

`dependency_reviews(limit,offset)` 分页返回待复核依赖及dependency_id。先实际阅读、比较并完成所需新来源的完整ack，再评估结论。

需要替代或撤回时先通过共享writer修改当前断言，回读、生成收据并索引；保留必要历史描述。`resolve_review` 参数为 `dependency_id,decision,reason,receipt_batch,target_path,heading,text,replacements`，decision为retained/replaced/withdrawn，replacements为当前依赖ID列表（撤回时可为空）。目标句必须在指定writer收据和当前索引中；保留或替代还须有已完整阅读、收据绑定的当前支持依赖。替代/撤回不能留下原段落的旧确定断言。

结案保留旧依赖、处置理由、替代关系和原收据，不把处理时间填为未知事实的失效时间。相同结案幂等，不能凭加入队列、无关收据或手改metadata解除阻断。

## 已写回评估的修订与候选转正

先用 `assessment(source_key,version)` 取得原评估和 `assessment_sha256`，实际比较新证据与已有知识。通过共享writer完成正式知识、纠正段落及原候选说明，回读并索引后调用 `revise_assessment`。参数为 `revision_id,source_key,version,expected_assessment_sha256,disposition,reason,comparison,claims,receipt_batch,retirements`。

新claim仍须精确连续来源引文、完整阅读、核验边界和正式知识的promotion_reason；每个目标句必须被本次收据的精确checks核验且与当前正文和索引版本一致。移除的旧依赖逐项提供 `retirements[{dependency_id,path,heading,text}]`，绑定本次收据中的处理说明；旧确定断言需移出原当前标题段落，可以保留到明确历史标题。原候选可保留转正链接及个案待补说明，不删除来源或编造用户审阅。

修订原评估、旧依赖、替代关系和原收据均保留；修订ID同内容重放幂等，不同内容拒绝。正文已写但索引失败时保留原评估，先补索引再结案。

## 候选的新证据监听

候选正文与索引完成后，用 `register_candidate(source_key,version,candidate_path,topics,scope,reason,include_pending)` 登记明确的主题词、库内范围和补证理由。`include_pending` 默认false；暂存来源需明确选择。主题命中只产生复核信号，不证明事实、不自动转正。新版本经归档索引对账或普通消息暂存发现后，进入同一持久 `candidate` 队列；重复版本不重复入队，旧版本变化或来源消失使旧任务失活。

阶段或周期整理先看 `candidate_tasks(limit,offset)`，再 `claim(kind="candidate",owner,lease_seconds)` 领取。通过统一read/ack完整阅读对应证据，排除维护回声、普通问答和候选自身的循环引用，进行语义判断。证据仍不足时调用 `candidate_decision(job_id,owner,decision="keep-candidate",reason,evidence_source_key,evidence_version)`，只记录具体理由；不制造新正文。满足转正条件时先完成上述writer/索引/revise_assessment，再以 `decision="promoted"` 和实际 `revision_id` 结案。没有当前租约、完整证据阅读或转正收据不能完成。

候选监听仅提交任务；当前代理或用户配置的整理任务执行判断，后台不调用外部模型。支持新证据触发与保留候选的测试，不能替代长期自动运行验收。

## Stop阶段任务的消费与结案

即时、阶段和日周整理都先调用 `stage_tasks(scope,limit,offset)`，只取授权项目/课程入口下的短元数据。未采集来源保持awaiting-capture（可用 `capture_now` 补采）；已暂存或归档来源绑定当前source_key/version，读取覆盖与归纳结果不能从Hook触发推断。

使用 `claim(kind="stage",owner,job_id,scope,lease_seconds)` 领取指定范围内的阶段租约；未采集来源不领取、不消耗重试额度，越界项目不返回租约。按统一read/ack、compare、review、route流程实际阅读并判断。需要正文变更时仍走共享writer及索引；无可复用内容只保留具体评估理由。最后调用 `stage_decision(job_id,owner,source_key,version,reason)`，它核对来源当前版本、完整阅读、最终处置、全部主张的writer精确段落收据及当前索引。来源变化、待写主张、租约过期、收据变动或索引未完成均不能结案；同内容重放幂等。失败使用finish_job(error)保留有限重试，不手改任务状态。

stage_tasks只是来源就绪检查，stage_decision只是验收；语义提炼仍由当前代理或用户已配置任务执行，不在短命Hook中调用模型。Hook定义未经过客户端原生信任审阅时不会自动运行，函数/脚本测试不代表真实新对话Hook已经触发。

## 明确纠正的即时阻断与结案

先实际阅读、区分原来就说错了、从某时起改变或尚有争议。向量邻近、最新时间或一次例证撤回不构成纠正关系。沿用同一 `lifecycle`、SQLite和writer，不另建项目状态或事实台账。

新连续来源优先用 `message_source(session_id,event_index,scope)` 选择具体消息，再read/ack。消息版本与其它消息追加解耦，来源编辑仍会使旧依赖待复核；只登记本次实际使用的消息，不为所有消息批量制造语义任务。返回upstream_truncated/completion/completeness；完整ack只证明观察到的接口内容读全，不能恢复上游截去或未公开内容。

识别到明确纠正后，**先登记，再更新派生正文**：`register_correction(correction_id,kind,source_key,version,quote,start,end,targets,reason,scope,effective_at)`。kind为retraction/supersession/dispute；quote须为当前来源精确连续区间且来源已完整ack。scope必须包含纠正来源和所有目标。targets使用 `{dependency_id,impact}`，impact为assertion/example；已有人工项目段落没有依赖时使用 `{path,heading,claim,impact}`，绑定当前原版和标题，不伪造旧来源依赖。ID不可换内容。

已实际核对来源的手写目标可显式增加 `origin:{source_key,version,quote,start,end}` 与 `expected_sha256`。origin须在scope内、完整read/ack，当前精确区间quote与claim相同，目标原字节SHA不变；这只建立调用者明确核对的来源绑定，不创建伪造的knowledge_dependencies。DB消息与旧MD快照需要分别提供版本/区间，不能按相似度或数字编号推定同一来源。

登记立即使受影响断言退出当前检索，正文尚未更新或索引失败也不能恢复；同块内其它不受影响的原文段落通过精确safe区间取证，不整体撤回通用方法。原始消息不改写。supersession的effective_at须有来源依据和时区；未知留空，不拿处理时间补。未来规则在明确生效前不替代旧规则。

通过同一writer更新实际受影响的项目、知识、摘要和日期记录，保存精确checks/收据并索引。已有依赖继续用resolve_review/revise_assessment实际结案；不得手改metadata解除阻断。新规则的claim带有依据的valid_from，旧有效期只按明确生效时间关闭；retraction不把旧错误伪装成此前正确规则。

然后调用 `finish_correction(correction_id,receipt_batch,scope,historical_sections,direct_resolutions)`。所有依赖必须已由同一收据结案，正文与索引哈希当前一致。保留的旧结论逐节列 `historical_sections[{path,heading,text}]` 并由同一收据精确核验；不能把含其它当前主张的整节当历史吞掉。人工正文目标用 `direct_resolutions[{dependency_id,path,heading,text,decision}]`，decision为withdrawn/replaced；它仍要求旧当前断言退出原节、处置说明进入收据，不建立伪造依赖。

不可改写的 `90 归档/Codex佐证` 原始会话历史目标使用decision=`source-retained`，并补 `proof_path,expected_sha256`。原文必须保持登记时的字节SHA，规范消息/归档来源已完整阅读且区间绑定当前；同一原断言必须有显式archive原文件来源门控。proof_path在同scope内，由同一writer收据核验处置段落、原版和索引；不把整段原始聊天复制进事实批次或靠删除原话结案。关闭后原版本/区间继续退出current，historical保留带纠正标注的原话，独立通用方法仍可查。

所有目标已经按同一收据source-retained结案时，重复finish允许证明页追加无关内容：只读核对原收据在state中的固定哈希、已committed队列与原计划postimage/段落，且当前证明段落仍在原标题、当前索引/目标字节及全部来源版本/区间仍匹配。返回idempotent=true、body_writes=0、resolution_writes=0，不新增结案行。首次/未全部结案仍要求证明页整版与收据一致；旧收据不能绕过新结案的当前回读。证明段落被改、原始来源/版本改变、收据或队列篡改、缺失旧结案行或越界均拒绝。

已绑定的归档原文件换SHA也不自动解除语义纠正：先按登记标题定位受影响的消息节，current暂不采用新版本，historical附corrected-origin-version-needs-review/not_current_basis；不同节中的无关消息仍独立可读。原消息标题被改名/移除而无法确认边界时，保守等待该文件复核，不凭新位置或相似度猜身份。这是来源版本待复核，不表示同文件的通用方法已被判错；保存和投影继续运行。

`corrections(scope,limit)` 返回短处置信息。多目标部分写入、文件占用、索引失败或新来源版本变化均保留阻断和可恢复任务，不以局部成功完成全部目标。当前与历史查询、未来时效和源原文分别验收。
