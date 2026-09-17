# 任务 08A：Smart Capture Domain API / Snapshot / Analysis / Context / MCP 后端工具

> v0.2.2.4 封版拆分：本任务必须同时阅读并遵守 `E_状态_子任务_事件与实时传输契约.md`。本任务只迁**后端业务 API 与 MCP backend tools**，不改 renderer 自动交接/Workspace/Terminal。Task 09 前仍不做 production Engine Cutover。

## 已确认决策 / 本任务主要完成事项

- JD / Analysis / Codex 数据归 Smart Capture Domain；independent `workflow_run_id=null` 必须完整可用。
- 新运行链以 `smart_capture_id` 为 identity；linked `workflow_run_id` 只作 parent/history link。
- 历史 Workflow Run API 可保留只读/legacy compatibility，不得成为 independent 的运行依赖。
- Smart Capture snapshot 只使用 `state_version`，不新增 Smart Capture `control_state`。

## 必须实现的 Domain API

业务 router 路径统一为 `/fine-job/smart-captures`；应用全局 `/api` prefix 由既有 server/client wrapper 负责，禁止文档/实现再出现无 FineJob namespace 的 `/smart-captures/...` shorthand 或双 `/api`。必须提供以 `smart_capture_id` 为业务 identity 的能力：

- `GET /api/fine-job/smart-captures/current`；
- `GET /api/fine-job/smart-captures/{id}`；
- current Context snapshot by channel；
- Analysis Items list；
- Manual Analysis Batch create；
- Analysis Item context / feedback / save；
- Codex session attach 的后端业务接口；
- Handoff claim / prompt-written / ACK started / release / retry；
- analysis guidance。

## Snapshot / Polling 强制契约

返回至少：

```text
smart_capture_id
source
workflow_run_id | null
status
stage
waiting_reason
control_cause
state_version
capabilities { start, pause, resume, retry, stop }
progress
result_summary
updated_at
```

- independent 无 Workflow ID 可持续刷新；
- parent mirror / Smart Capture detail / inspected historical run 是三个状态源；
- history 查询不能切换 current/polling/control；
- 任何会改变 snapshot 可观察字段（至少 status/stage/waiting_reason/control_cause/capabilities/progress/result_summary）的提交都使 `state_version` 单调递增；断线恢复按 `smart_capture_id + state_version`；
- terminal snapshot 可读取；
- Smart Capture 自身不新增 `control_state`。

## MCP / CodexToolService 后端工具

建立 Smart Capture identity 的 FineJob tools，等价覆盖：

- get Smart Capture state/context；
- ACK current analysis batch started；
- list analysis items；
- get item context；
- save item/result；
- 必需的 handoff/session 后端操作。

旧 Workflow tools 可为历史/旧任务保留；新 independent 运行不能回退要求 `workflow_run_id`。

## OFF completed 后处理

Manual Analysis 以 `smart_capture_id` 为主 identity：

- 不修改 completed lifecycle；
- 不修改已推进 parent；
- 不重复 child-completed；
- 不重新占 current；
- 不自动重启 Prefetch/Engine。

## 禁止事项

- 不修改 renderer `workflowCodexHandoff.ts` / App controller / Workspace / Terminal（留给 08B）；
- 不正式 Cutover；
- 不复制 Workflow API 形成第二套业务逻辑；应复用/抽取 shared service。

## 自动测试（强制）

1. independent 无 workflow id：Analysis create → claim → prompt-written → ACK → list/context/save；
2. linked 走同一 service；
3. Smart Capture snapshot/polling 无 Workflow ID；
4. MCP Smart Capture identity 全链；
5. Workflow history tool 仍可读旧任务；
6. claim race/release/retry 语义不退化；
7. completed OFF Manual Analysis 不回滚 lifecycle/parent。

## 用户手测

本任务以 API/service tests 为主，不要求完整桌面 Codex live handoff。

## 高级模型复核

可与 08B/09 合并复核。
