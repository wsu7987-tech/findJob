# HANDOFF_Task02：通用父子身份、SQLite 原子创建与执行权过渡

执行日期：2026-09-18

## A. 已完成

1. 驾驶舱创建链在一个 `BEGIN IMMEDIATE` SQLite 写事务中完成 parent、baseline task、linked Smart Capture、child relation 和 current pointer 的身份落库。
2. 创建前在同一写锁内检查 Smart Capture current slot 与 custom collection capacity；事务失败时统一由数据库连接上下文回滚。
3. 新增通用 `fj_workflow_children` relation，Smart Capture 以 `child_type=smart_capture`、`child_ref=smart_capture_id` 关联；父层通过稳定的 children service/API 读取投影。
4. relation 持久化 `sequence`、状态摘要、控制字段、capabilities、result summary、时间戳、relation `state_version` 与 `child_state_version`。
5. `fj_smart_captures.execution_config_json` 保存 linked/independent 共用的完整 SmartCaptureExecutionConfig，覆盖搜索、候选目标、投递目标、JD/detail、Analysis/Codex、Context budget、guidance 和 stop policy。
6. independent Smart Capture 继续只创建自身记录和 current，不创建 Workflow Run、`fj_workflow_children` relation 或 parent orchestration event。
7. Workflow 创建请求增加可选 `idempotency_key`；重复提交返回原 parent、原 Smart Capture、原 relation 和原 current，数据库唯一索引提供持久化约束。
8. Task09 前继续保留单一旧 Workflow Pipeline 生产执行路径；Task02 没有启动第二套 Smart Capture Engine，也没有迁移 Pipeline 表 owner。

## B. 事务边界说明

事务调用链：配置校验 → `BEGIN IMMEDIATE` → current/capacity 检查 → INSERT parent → INSERT baseline search combination/task → INSERT pending linked Smart Capture 与完整 execution config → INSERT child relation → UPSERT current slot → COMMIT → 后续才允许旧 Workflow Engine 启动外部 BOSS side effect。

`fj_workflow_children` 不是 Smart Capture lifecycle 的写入源。Smart Capture 状态、配置和执行快照归 `fj_smart_captures`；linked 状态变更在同一状态更新事务内投影到 relation。外部 BOSS/Codex/browser 调用不在身份创建事务内。

## C. Child relation schema / 约束

- 表：`fj_workflow_children`。
- parent FK：`workflow_run_id → fj_workflow_runs(id) ON DELETE CASCADE`。
- 稳定身份唯一约束：`(workflow_run_id, child_type, child_ref)`，并创建 `idx_fj_workflow_children_identity`。
- `sequence > 0`，`state_version > 0`，`child_state_version >= 0`。
- `child_state_version` 保存 parent 已接受的 child lifecycle 版本；`state_version` 保存 relation projection 自身版本，两者独立递增。
- `child_ref` 对 Smart Capture 指向 `fj_smart_captures.id`；反向导航通过 `fj_smart_captures.workflow_run_id` 查询校验。
- 已有数据库由 `Database._ensure_workflow_children_schema()` 原地补齐表、字段、索引，并回填可确认的 linked Smart Capture relation。

## D. 幂等创建证明与失败 rollback

- 相同 `idempotency_key` 在提交前回读原 parent；并发重试在 `BEGIN IMMEDIATE` 写锁内再次回读，最终只使用一组 parent/child/relation/current。
- `idx_fj_workflow_runs_idempotency_key` 对非空 key 提供唯一约束；事务回滚后 key 不残留，后续请求可以重新创建。
- rollback 测试故意让 child relation INSERT 失败，确认 parent、baseline task、search combination、Smart Capture、relation 和 current 均不存在。
- independent active Smart Capture 阻塞驾驶舱创建时，parent 事务整体失败，数据库中没有 parent 或 linked child 孤儿。

## E. 测试

完整 Task02 相关后端回归：

```text
pytest -q backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/api/test_fine_job_workflow_runs_api.py backend/tests/test_db.py
62 passed, 2 failed, 2 warnings
```

新增 Task02 核心测试复测：

```text
pytest -q backend/tests/api/test_fine_job_workflow_runs_api.py::test_independent_active_capture_rejects_cockpit_without_orphan_parent backend/tests/api/test_fine_job_workflow_runs_api.py::test_workflow_create_retry_returns_one_parent_child_current_identity backend/tests/api/test_fine_job_smart_captures_api.py::test_linked_parent_and_child_roll_back_together_when_relation_insert_fails
3 passed, 1 warning
```

失败项均为既有 Workflow 基线：`test_create_workflow_run_exposes_real_search_context_snapshot` 的旧断言未包含已存在的 `analysis_policy.enabled`；`test_pause_preserves_running_capture_and_resume_allows_auto_advance` 仍返回 `pending`。Task01 Handoff 已记录该 Workflow contract/resume 基线问题。本次新增的 Task02 测试全部通过。`git diff --check` 通过；pytest cache 目录存在既有权限警告。

## F. Authority / Owner 状态

- Current owner：持久化 `fj_smart_capture_current` 指针指向当前 Smart Capture；不通过最近时间、历史 Workflow Run 或 route 参数猜测 current。
- Child lifecycle/config/execution owner：`fj_smart_captures`，包括完整 `execution_config_json`。
- Parent relation/projection owner：`fj_workflow_children`，父层从 relation 读取 child 顺序、状态摘要、capabilities 和结果摘要。
- Pipeline data owner：Task02 保留现有 Workflow/Pipeline 表及其历史兼容路径，尚未执行 Task05/06 的逐表 Smart Capture owner 迁移。
- Production execution authority：仍为一套旧 Workflow Pipeline Engine，入口保持 `workflow_runs.advance_deep_job_search` 及现有旧 callback；没有对同一 live child 双启动。
- History compatibility：旧 `workflow_run_id`、Workflow 查询/API、旧 Analysis/Prefetch/Handoff 读取路径继续保留；新 relation API 提供 parent child 稳定读取。

## G. 偏差 / 未决风险

1. 幂等键为可选字段；调用方需要在一次业务创建的重试中复用同一个 key，驾驶舱 UI 的统一 key 生成留到后续 UI 任务。
2. Child outcome event、event 幂等消费和父子控制状态机尚未在 Task02 启用，按任务顺序交由 Task03/Task10 完成；当前 Task02 只提供持久化 relation 与 lifecycle projection。
3. 现有 Workflow contract/resume 两个基线失败仍待既定后续任务处理；本窗口没有扩大范围修复。
4. 前端桌面端未执行新增构建回归；Task01 Handoff 已记录当前 Vitest/TypeScript 环境问题。

## H. 下一窗口 Handoff

1. Task03 从 `fj_smart_captures` 读取 child lifecycle，并把 parent pause/resume/cancel 映射到 child command。
2. parent relation 只保存 projection，不直接写 Smart Capture status。
3. parent cancel 映射 child `stop`，child terminal lifecycle 仍使用 `stopped`。
4. `failed` 继续保持 terminal，`retry=false`；父层 failed decision 使用 skip/end 两条路径。
5. interrupted、paused、waiting_for_user 使用 E 契约的 canonical reason 与 control cause。
6. 处理 parent/child callback 竞态，确保 parent cancel/pause 优先级稳定。
7. 后续 child event 必须携带 relation identity、transition_id、event_id 和 child state version。
8. event 消费使用 `(child_relation_id,event_id)` 幂等键，并拒绝不递增的 child version。
9. 继续保留当前旧 Workflow Engine authority，Task09 前不接入第二套 live Engine。
10. Task04 负责 seam/characterization 与 Cutover guard，Task05/06 再做 Pipeline owner/schema/query 迁移。
