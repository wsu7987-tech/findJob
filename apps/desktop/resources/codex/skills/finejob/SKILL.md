---
name: finejob
description: 通过 FineJob MCP 完成岗位检索、岗位评估、打招呼预览、代聊草稿与受控发送。
---

# FineJob 业务协作

## 启动检查

每次会话先调用 `finejob.get_capabilities`，确认浏览器、BOSS 执行器、聊天发送和工具版本状态。能力未就绪时，向用户说明返回的业务原因和需要完成的 FineJob 操作。

## 岗位评估流程

1. 使用 `finejob.search_jobs` 定位岗位。
2. 使用 `finejob.get_job_context` 读取岗位详情版本、候选人上下文、简历版本和既有评估。
3. 使用返回的 `candidate_profile_context` 进行评估；旧资料尚未迁移时再调用 `finejob.get_resume_facts`。
4. 保存评估时携带读取到的岗位和候选人上下文版本。
5. 需要沟通时调用 `finejob.create_greeting_preview`，再用预览版本调用 `finejob.request_greeting_execution`。

## 代聊流程

1. 使用 `finejob.list_chat_sessions` 定位会话。
2. 使用 `finejob.get_chat_context` 读取最新入站消息、会话版本、已脱敏候选人对话上下文和草稿状态。
3. 需要选择回答版本时调用 `finejob.list_profile_questions`，按当前岗位、岗位族、通用的顺序选择已确认版本。
4. 使用当前 `session_version` 与 `latest_message_id` 调用 `finejob.save_chat_reply_draft`。
5. 使用草稿的 `text_version`、当前会话版本与最新消息 ID 调用 `finejob.request_chat_send`。

## 状态与确认

- 工具返回任务或动作资源后，使用 `finejob.get_operation_status` 查询权威状态。
- 返回 `awaiting_confirmation` 时，提示用户在 FineJob 的待确认卡片中确认或拒绝。
- 返回版本冲突时，重新读取对应上下文并基于最新事实生成结果。
- 岗位详情缺失时使用 `finejob.collect_job_detail`，完成后再读取岗位上下文。
- 业务结果以 MCP 返回的资源 ID、版本和状态为准。

## 边界

- 使用 `finejob.get_capabilities` 返回的已注册工具完成业务调用。
- 敏感操作授权由 FineJob 设置页管理。
- 真实发送继续经过 FineJob 的登录态、页面身份、会话版本、队列和平台状态校验。
