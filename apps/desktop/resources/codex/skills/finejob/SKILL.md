---
name: finejob
description: 通过 FineJob MCP 编排可组合的求职业务节点，完成岗位采集与筛选、目标数量的岗位评估或投递建议、打招呼预览、代聊草稿与受控发送。当用户要求在 FineJob 中执行岗位或沟通任务时使用。
---

# FineJob 业务协作

## 按结果组织任务

先把用户目标整理成完成契约，再选择需要执行的业务节点。完成契约至少包含：

- 最终要得到的业务结果。
- 目标数量及计数口径。
- 指定策略或允许选择的策略范围。
- 是否需要新采集、是否允许复用历史岗位。
- 是否包含真实对外动作。
- 用户给出的停止条件和附加要求。

不要把自然语言目标套入固定任务模式。根据当前 FineJob 状态组合节点；前置结果已经存在时跳过对应节点，缺少前置时插入能产生该结果的节点。每完成一个节点，使用其正式输出重新判断下一节点。

岗位采集、岗位筛选、目标数量的 JD 或投递建议任务，必须先完整阅读 [岗位任务节点](references/job-task-nodes.md)，再调用相关工具。

## 启动与状态

每次会话先调用一次 `finejob.get_capabilities`，只让与当前目标相关的能力状态参与判断。

- 岗位采集、筛选、JD 获取和建议生成只检查 FineJob 专用浏览器及对应工具。
- BOSS 执行器、执行权限和队列只影响真实打招呼动作。
- 工具返回任务或动作资源后，使用 `finejob.get_operation_status` 查询权威状态。
- 返回 `awaiting_confirmation` 时，说明需要用户确认的具体选择并等待。
- 返回版本冲突时，重新读取对应资源，再基于最新版本继续。
- 业务结果以 MCP 返回的资源 ID、版本、状态和 FineJob 已保存记录为准。

## 节点编排原则

1. 只选择实现当前完成契约必需的节点。
2. 同一会话复用已经读取且仍有效的策略、上下文和资源标识。
3. 以正式保存成功的唯一业务记录计数，不以终端文字、尝试次数或队列创建数量计数。
4. 节点失败时先保留已经完成的结果；还有安全且符合目标的后续路径时继续选择节点。
5. 达到完成标准、遇到停止条件或需要新的用户授权时结束当前编排。
6. 最终汇报完成数量、各结果状态、正式资源标识、未完成原因和可继续的节点。

列表首页、一次下滑或一个搜索组合处理完成只代表采集节点完成。目标数量尚未达到时，当前页候选岗位为零也要继续判断 `continuation_available`、`has_more` 和剩余搜索组合，不能把“本页全部排除”作为整个任务的完成条件。

## 单岗位评估与沟通

单个既有岗位可以使用 `finejob.search_jobs` 定位，再读取当前岗位资料。岗位详情缺失时先执行详情获取节点。保存评估后，按用户目标决定是否创建打招呼预览或请求受控执行。

真实对外动作必须由用户目标明确包含，并继续经过 FineJob 的内容分类、版本、登录态、页面身份、确认和队列校验。创建待确认项、审批通过或动作入队均代表各自业务状态；只有执行结果为成功时才算真实发送完成。

## 代聊

1. 使用 `finejob.list_chat_sessions` 定位会话。
2. 使用 `finejob.get_chat_context` 读取最新入站消息、会话版本、候选人上下文和草稿状态。
3. 需要回答事实时调用 `finejob.list_profile_questions`，按当前岗位、岗位族、通用的优先级使用已确认版本。
4. 使用当前 `session_version` 与 `latest_message_id` 保存草稿。
5. 用户目标包含真实发送时，再使用草稿文本版本和最新会话版本请求受控发送。

## Job Hunt Refresh Workflow

页面提交求职数据更新任务时，输入只包含已经持久化的 `run_id`。严格执行以下顺序：

1. 调用 `finejob.get_job_hunt_refresh_run`，读取 `scope_id`、`selected_since_time`、`workflow_options`、当前状态和已完成进度。聊天列表已经在 Scope Discovery 阶段同步完成。
2. 仅当 `refresh_chat_messages=true` 时，调用 `finejob.refresh_job_hunt_chat_batch(run_id)`。该工具会先复用自动代聊“更新信息 / 更新聊天列表”能力准备 BOSS 聊天页，再按 Run 中未完成或可重试的 session 范围调用 `BossChatBatchManager`。返回非终态 `chat_batch` 时，用 `finejob.get_operation_status` 读取权威状态；任务结束后再次调用 `finejob.refresh_job_hunt_chat_batch(run_id)` 完成 Run Item 持久化。重复本步骤，直到工具返回已无可处理聊天项。
3. 聊天批量覆盖的岗位不再逐个调用岗位刷新。仅当 `refresh_related_jobs=true` 且 `scope.counts.extra_jobs > 0` 时，补充处理 `extra_jobs`。调用 `finejob.list_job_hunt_refresh_items(item_type="related_job")`，FineJob Service 会过滤或跳过聊天批量已覆盖的历史岗位 item；按返回顺序调用 `finejob.refresh_job_hunt_related_job`。返回非终态 `capture_task` 时，用 `finejob.get_operation_status` 读取权威状态；任务结束后再次调用原岗位 item 工具完成持久化。应用重启后旧任务状态不可读取时，直接再次调用原岗位 item 工具，由 Service 根据持久化岗位状态恢复，再继续下一项。
4. 如果 `workflow_options` 中启用了 `analyze_conversations`、`generate_missing_suggestions`、`generate_reply_drafts` 或 `generate_followup_recommendations`，数据补充项全部结束后只调用一次 `finejob.prepare_job_hunt_refresh_analysis(run_id)`。该工具会执行结构化状态读取、确定性事实锚定和旧任务状态同步，并返回本 Run 的统一分析任务清单。
5. 在同一个 Codex CLI 会话内，按 `prepare` 返回的 `conversation_items` / `job_evaluation_items` 中的 `context_arguments` 调用 `finejob.get_job_hunt_refresh_analysis_item_context` 读取单个 item 上下文。该读取只取资料，不代表第二次 AI 执行。
6. 基于本次会话读取到的 item 上下文，一次性完成本次勾选的 AI 结果。结果必须按 `session_id` / `job_id` 独立，不交叉使用不同聊天、岗位或 JD 的事实。
7. 调用 `finejob.save_job_hunt_refresh_analysis(run_id, analysis_result)` 保存同一次分析结果；结果体积过大时可以分批保存，中间批传 `final_batch=false`，最后一批传 `final_batch=true`。不得重新调用 `prepare`，不得把投递建议、回复草稿、跟进建议拆成多次独立 AI 任务。
8. 如需核对分析保存、跳过和当前正式 evaluation 对应明细，调用 `finejob.list_job_hunt_refresh_analysis_items(run_id, item_type)`。`finejob.list_job_hunt_refresh_items` 只用于聊天和岗位数据补充 item。
9. 如果未启用分析相关工作流，或分析结果已经保存完成，调用 `finejob.complete_job_hunt_refresh_run`，再读取一次 Run 并汇总。

恢复同一个 `run_id` 时，聊天批量工具和列表工具只处理 `pending`、中断遗留的 `running` 和可重试的 `failed` item。聊天批量已覆盖的 legacy 岗位 item 由 FineJob Service 标记为 skipped，`succeeded` 或不可重试的 `skipped` item 不再执行。

工作流约束：

- 不修改 `selected_since_time`，不扩大 Run 中已保存的处理范围。
- 不执行用户未勾选的工作流。
- 不直接读写数据库；会话去重、消息去重、岗位关联和 JD 写入均交给 FineJob Service。
- 单个会话或岗位失败后继续处理其他 item，最终由 Run 汇总为完成或部分失败。
- AI 分析只在当前 Codex CLI 会话中完成；后端工具负责 prepare/save/complete 和代码规则校验。
- 推进建议只保存为 `attention_status` / recommendation，不创建正式待执行任务。
- 回复草稿只保存和展示，不发送，不创建发送动作，不调用 BOSS 发送接口。

## 边界

- 调用 `finejob.get_capabilities` 返回的已注册工具，不复制 FineJob 已有采集、筛选、评估或路由逻辑。
- 不创建或修改策略，除非用户明确要求管理策略。
- 不把候选人档案冗余字段当成额外前置校验；以 FineJob 策略和上下文工具的正式返回为准。
- 不绕过待确认、敏感操作授权、版本校验和平台状态。
