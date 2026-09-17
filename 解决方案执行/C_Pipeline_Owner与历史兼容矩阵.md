# C. Pipeline Owner / 历史兼容矩阵（v0.2.2.4）

> 核心不是“删 workflow_run_id”，而是让 Smart Capture 成为新业务 owner，同时保留历史 Workflow Run 查询能力。

| 数据/能力 | 新业务 owner | linked workflow_run_id | independent workflow_run_id | 历史 Run 按 ID |
|---|---|---|---|---|
| Search / combinations | smart_capture_id | 可保留关联 | null | legacy 可兼容查 |
| Candidate pool / discovery | smart_capture_id | 可关联 | null | linked 历史可解析 |
| BOSS batch | smart_capture_id | 可关联/审计 | null | 历史只读兼容 |
| JD / analysis item | smart_capture_id | 可关联 | null | 同上 |
| Analysis Batch | smart_capture_id | 可关联 | null | 同上 |
| Codex handoff | smart_capture_id + analysis_batch_id | 可历史关联 | null | 旧 handoff 不丢 |
| Codex session | smart_capture/analysis domain | 可记录 parent link | null | legacy 可读 |
| Prefetch Batch/Item | smart_capture_id | 可关联 | null | 旧 prefetch 不丢 |
| Candidate Reservation | smart_capture_id | 可关联 | null | active unique 保留 |
| Context Snapshot | smart_capture_id + channel | 可历史关联 | null | 旧 Workflow Context 仍可读 |
| Completion progress | smart_capture_id | parent 消费 summary | null | 历史结果可读 |
| Parent orchestration | workflow_run_id | 本体 | 不存在 | 可历史读 |

> 逐表迁移要求：如果 discovery、analysis item、feedback 仍通过 `workflow_task_id` 或 `workflow_run_id NOT NULL` 间接依赖 Workflow，必须建立 Smart Capture domain task/analysis identity 或等价 owner 结构；仅增加 `smart_capture_id` 不算完成。

## 迁移原则

1. 原地升级，不清库。
2. 能可靠回填 linked → 回填；不能可靠回填 → legacy read adapter，不伪造。
3. 新 independent Pipeline 在 `workflow_run_id=null` 下完整运行。
4. linked `workflow_run_id` 仅 parent/history link；新业务函数优先 Smart Capture owner。
5. 双 owner 过渡只能有一个权威：新业务状态按 Smart Capture。
6. 历史 Workflow ID 是 inspected read，不可反向成为 current owner。
7. 旧 tests 的 prefetch/reservation/handoff 语义要迁移/兼容，不降断言。
8. completed Smart Capture 的后处理 Analysis 仍以 Smart Capture/history artifact 关联，但不改变 lifecycle completion。
9. Context/handoff/prefetch/reservation 的 owner-specific unique/index 必须在 SQLite migration 后用 PRAGMA 和真实 independent insert 验证。

## 历史 ID 特别验收

当前 linked Smart Capture 父 Run=A，输入历史 Run=B：B 可展示历史；current/parent/Codex control 仍 A；不切 SSE/polling；清 B 无需恢复 A。

## Parent/Child Relation Authority（v0.2.2.4 封版）

- Smart Capture Pipeline 数据与 lifecycle/config/execution 以 `smart_capture_id` / Smart Capture 专属实体为业务权威；
- `fj_workflow_children` 是 parent orchestration relation/projection 的 canonical relation；
- `fj_smart_captures.workflow_run_id` 只保留 linked 导航/历史反规范化 link，不取代 `fj_workflow_children`；
- parent 不通过 relation 表反写 Smart Capture lifecycle。
