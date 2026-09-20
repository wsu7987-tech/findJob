# HANDOFF_Task08A：Smart Capture Domain API / Snapshot / Analysis / Context / MCP 后端

## A. 完成结论

08A 后端验收通过，范围保持在 Domain API、Snapshot/Polling、Analysis、Context、Codex handoff/session 和 MCP backend tools。

- independent Smart Capture 使用 `smart_capture_id`，`workflow_run_id` 为 `null` 时可以完成 Analysis Batch → claim → prompt-written → ACK → context → save。
- linked Smart Capture 的非终态请求进入既有 Workflow service；completed Smart Capture 进入 Smart Capture Domain service，保持同一业务入口和历史兼容路径。
- Smart Capture detail/current 返回固定 snapshot 契约，`state_version` 作为唯一版本字段；terminal snapshot 仍可读取。
- MCP Smart Capture tools 统一接收 `smart_capture_id`，覆盖 state/context、analysis item、session、handoff 和 save。
- completed Smart Capture 的 Manual Analysis 只写后处理 artifact，不改变 lifecycle、parent、current 或自动 Engine/Prefetch 状态。
- 旧 Workflow API、Workflow history 查询和旧 MCP tools 保留为历史/兼容路径；Task09 前 linked production Engine authority 继续由旧 Workflow Engine 持有。

## B. Domain API 清单

业务 router 前缀为 `/fine-job/smart-captures`，应用 `/api` 前缀后的完整地址为 `/api/fine-job/smart-captures`。

| 方法 | 路径 | 作用 | 主要 identity |
|---|---|---|---|
| POST | `/api/fine-job/smart-captures` | 创建并启动 independent Smart Capture | `smart_capture_id` |
| GET | `/api/fine-job/smart-captures/current` | 读取持久化 current 指针 | Smart Capture current |
| GET | `/api/fine-job/smart-captures/{id}` | 读取 detail、候选池、搜索与 snapshot | `smart_capture_id` |
| GET | `/api/fine-job/smart-captures/{id}/context-snapshot?channel=...` | 读取指定 channel 的 Context snapshot | `smart_capture_id + channel` |
| GET | `/api/fine-job/smart-captures/{id}/analysis-items` | 读取 Analysis Items | `smart_capture_id`，可带 `analysis_batch_id` |
| POST | `/api/fine-job/smart-captures/{id}/analysis-batches` | 创建 Manual Analysis Batch | `smart_capture_id` |
| GET | `/api/fine-job/smart-captures/{id}/analysis-items/{task_id}/context` | 读取单 Item 紧凑上下文 | `smart_capture_id + workflow_task_id` |
| POST | `/api/fine-job/smart-captures/{id}/analysis-items/{task_id}/feedback` | 保存 Analysis feedback | `smart_capture_id + workflow_task_id` |
| POST | `/api/fine-job/smart-captures/{id}/analysis-items/{task_id}/save` | 保存正式 Analysis result | `smart_capture_id + workflow_task_id` |
| PATCH | `/api/fine-job/smart-captures/{id}/codex-session` | 绑定 Codex session/runtime | `smart_capture_id + codex_session_ref` |
| POST | `/api/fine-job/smart-captures/{id}/analysis-handoff/claim` | 原子 claim 当前 Analysis batch | `smart_capture_id + analysis_batch_id` |
| POST | `/api/fine-job/smart-captures/{id}/analysis-handoff/prompt-written` | 记录 Prompt 已写入 | `smart_capture_id + analysis_batch_id + handoff_attempt_id` |
| POST | `/api/fine-job/smart-captures/{id}/analysis-handoff/ack-started` | ACK 当前批次已开始 | `smart_capture_id + analysis_batch_id + handoff_attempt_id` |
| POST | `/api/fine-job/smart-captures/{id}/analysis-handoff/release` | 释放当前 handoff | `smart_capture_id + analysis_batch_id + handoff_attempt_id` |
| POST | `/api/fine-job/smart-captures/{id}/analysis-handoff/retry` | 重新 claim 当前 handoff | `smart_capture_id + analysis_batch_id + handoff_attempt_id` |
| PATCH | `/api/fine-job/smart-captures/{id}/analysis-guidance` | 更新当前 Analysis guidance | `smart_capture_id` |

### 业务分流

```text
smart_capture_id
  ├─ workflow_run_id=null + independent/non-terminal  → Smart Capture Domain owner
  ├─ workflow_run_id!=null + status!=completed       → 旧 Workflow service 兼容实现
  └─ status=completed                                → Smart Capture Domain 后处理实现
```

`workflow_run_id` 作为 linked parent/history link 保存；新 independent 运行不创建 Workflow Run 依赖。

## C. Snapshot schema 示例

`GET /api/fine-job/smart-captures/{smart_capture_id}` 和 current 返回的核心 snapshot 形状如下：

```json
{
  "smart_capture_id": "sc_01J08A",
  "source": "boss_capture",
  "workflow_run_id": null,
  "status": "completed",
  "stage": "completed",
  "waiting_reason": "",
  "control_cause": "",
  "state_version": 7,
  "capabilities": {
    "start": false,
    "pause": false,
    "resume": false,
    "retry": false,
    "stop": false
  },
  "progress": {
    "current": 15,
    "total": 15,
    "jobs_collected": 15,
    "details_completed": 15,
    "details_failed": 0
  },
  "result_summary": {
    "jobs_collected": 15,
    "details_completed": 15,
    "details_failed": 0,
    "has_more": false
  },
  "updated_at": "2026-09-21T00:00:02Z"
}
```

版本规则：可观察的 status、stage、waiting_reason、control_cause、capabilities、progress 或 result_summary 发生已提交变化时递增 `state_version`；no-op 保持原版本。恢复读取使用 `smart_capture_id + state_version`。

Context snapshot 额外返回 `context_snapshot_id`、`channel`、`sections`、预算、状态和生成时间；Analysis Item context 复用 Smart Capture 的 `candidate_analysis` shared snapshot，并追加单岗位 JD、配置和 guidance。

## D. MCP Smart Capture identity 调用链

MCP server 注册的 Smart Capture tools 与 `CodexToolService` 通过相同的 `smart_capture_id` 进入 Domain service：

```text
finejob.get_smart_capture_state(smart_capture_id)
  → smart_captures.get_smart_capture

finejob.get_smart_capture_context(smart_capture_id, channel)
  → smart_capture_domain.get_context_snapshot

finejob.list_smart_capture_analysis_items(smart_capture_id, analysis_batch_id)
  → smart_capture_domain.list_analysis_items

finejob.get_smart_capture_analysis_item_context(smart_capture_id, workflow_task_id)
  → smart_capture_domain.get_analysis_item_context

finejob.attach_smart_capture_codex_session(smart_capture_id, ...)
  → smart_capture_domain.attach_codex_session

finejob.claim_smart_capture_analysis_handoff(smart_capture_id, ...)
  → smart_capture_domain.claim_handoff

finejob.mark_smart_capture_analysis_prompt_written(smart_capture_id, ...)
  → smart_capture_domain.prompt_written

finejob.ack_smart_capture_analysis_batch_started(smart_capture_id, ...)
  → smart_capture_domain.ack_started

finejob.save_smart_capture_analysis_item(smart_capture_id, workflow_task_id, ...)
  → smart_capture_domain.save_analysis_item
```

完整 independent 调用顺序：

```text
create analysis batch
→ claim handoff
→ mark prompt-written
→ ACK started
→ list/context
→ save item/result
```

ACK 与 Prompt 重复提交保持原时间戳；独立 Domain save 需要当前 Analysis batch 存在并已收到 started ACK。

旧 `finejob.*workflow*` tools 继续按 `workflow_run_id` 服务历史 Workflow 任务。

## E. 自动测试与启动验证

| 验证 | 结果 |
|---|---:|
| `backend/tests/api/test_fine_job_smart_capture_domain_api.py` | 3 passed |
| Smart Capture API + Codex API | 26 passed |
| `backend/tests/api/test_fine_job_workflow_runs_api.py` | 46 passed |
| `backend/tests/test_db.py` | 13 passed |
| `compileall -q backend/app backend/tests` | 通过 |
| 后端 health/current + 前端 Vite 根页面 | 通过 |

启动验证结果：后端 `/api/health` 返回 `status=ok`；`/api/fine-job/smart-captures/current` 返回成功；前端 `http://127.0.0.1:5173/` 返回 200 且包含 `<div id="app"></div>`；验证结束后 8000/5173 已释放。

组合回归中另有 2 项 pipeline seam 测试被 FineJob Chrome 前置检查阻断，组合结果为 46 passed、2 failed。失败发生在实际浏览器状态检查处，测试未 mock CDP/Chrome，未进入 08A Domain API 逻辑；该环境依赖继续列为回归风险。

## F. 本窗口修改与修复

- `backend/app/services/fine_job/smart_capture_domain.py`
  - 增加 Prompt/ACK 重复提交幂等保护。
  - 独立 Domain save 强制经过当前 handoff 的 started ACK。
- `backend/tests/api/test_fine_job_smart_capture_domain_api.py`
  - 增加重复 ACK 时间戳保持测试。
  - 增加 ACK 前 save 被阻断测试。
- 08A 后端实现文件：
  - `backend/app/mcp/fine_job_server.py`
  - `backend/app/routers/fine_job/smart_captures.py`
  - `backend/app/schemas/fine_job/smart_captures.py`
  - `backend/app/services/fine_job/codex_tools.py`
  - `backend/app/services/fine_job/smart_capture_domain.py`

## G. 边界与遗留风险

- pipeline seam 仍需要 FineJob Chrome/CDP 或测试 mock 才能完成环境级回归。
- Renderer、Workspace、Terminal、Skill transport 留给 08B。
- linked 正式 Engine Cutover、唯一 live execution authority 和 parent completion/outcome 消费留给 Task09。
- 当前测试覆盖 API/service/MCP backend；未执行真实 BOSS 浏览器操作和完整 Electron Codex live handoff。
