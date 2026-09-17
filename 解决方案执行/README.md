# FineJob Codex Luna 任务书 v0.2.2.4

本版本基于以下材料重新整理：

- `规划任务书.md`：完整产品沟通与已确认决策；
- `findjob_codex_taskbook_v0.2.2.3.zip`：v0.2.2.4 的直接文档基线；
- `findjob_codex_taskbook_v0.2.zip` / `v0.1.zip`：用于回看任务拆分演进；
- `findJob-task-cockpit-phase1.zip`：用于再次核对当前源码依赖与迁移风险。

> 目标分支：`task-cockpit-phase1`
>
> 核心原则：**目标产品模型高于当前坏代码；但现有已经正确实现的能力默认保留，通过迁移/重新接线解决 ownership，而不是靠删除功能完成重构。**

## v0.2.2.4 相对 v0.2.2.3 的封版小修

1. **hard failure 后的父层决策已确认**：`child_failed_waiting_decision` 与 `child_cancelled_waiting_decision` 共享同一决策集合——跳过该 child 继续后续编排 / 结束父任务，二选一。此前版本把这一点列为"Luna 遇到时报告，不擅自发明"的开放项，本版由产品方确认后封死；`failed` 仍是 hard terminal，同一 child 仍不可 retry，skip 只是父编排跳过该 child。
2. 受影响文件：`E_状态_子任务_事件与实时传输契约.md`、`B_父子任务控制状态矩阵.md`、`03_Luna_父子控制_失败中断与卡点状态机.md`、`12_Luna_新驾驶舱编排UI与快递式时间线.md`、`Codex_Luna_每窗口通用提示词_v0.2.2.4.md`、`16_Sol_Terra_高风险复核清单_v0.2.2.4.md`、总览第 9 节。
3. 不新增任务、不改变产品模型/页面范围/00→16 执行顺序；A/B/C/D/E、总览、通用提示词、高风险复核统一到 v0.2.2.4。

## v0.2.2.3 相对 v0.2.2.2 的封版小修

1. `failed` 固定为 hard terminal，同一 Smart Capture `retry=false`；所有可恢复错误统一落 `interrupted`/其他非终态。
2. `fj_workflow_children` 增加/等价持久化 `child_state_version`；parent 除 `(child_relation_id,event_id)` 幂等外，只接受更高 child lifecycle version。
3. Parent orchestration outcome event 明确只属于 linked child；independent 不建隐藏 relation/Workflow，不伪造 `child_relation_id`。
4. Smart Capture `state_version` 的递增范围封死：所有改变 snapshot 可观察字段的提交都单调递增。
5. Task 09/10 completion 边界再明确：09 只按既定规则接通最小 linked outcome，10 封严完整 OFF/ON policy。
6. Smart Capture API 路径统一为 FineJob router `/fine-job/smart-captures`（完整 HTTP `/api/fine-job/...`），移除 `/smart-captures/...` shorthand。
7. Parent mirror 控制统一 `pause/resume/cancel`；parent `cancel` 由后端映射 child `stop`。
8. A/B/C/D/E、总览、通用提示词、高风险复核及版本血缘统一到 v0.2.2.3。

## v0.2.2.2 相对 v0.2.2.1 的封版小修

1. snapshot 版本字段只保留整数 `state_version`，不再允许第二套 `revision`。
2. Smart Capture capabilities 固定 `{start,pause,resume,retry,stop}`；parent `cancel` 映射为 child `stop`，Smart Capture 不提供 `cancel` capability。
3. 08A / 08B 都显式依赖 `E_状态_子任务_事件与实时传输契约.md`。
4. Child outcome event 增加稳定 relation identity：`child_relation_id/child_type/child_ref`；parent 以 `(child_relation_id,event_id)` 幂等消费。
5. `waiting_for_user` 固定为人工阻塞 canonical lifecycle；legacy `waiting_next_batch + waiting_reason` 只允许 migration/read mapping。
6. Task 09 前置纠正为 `04～08B`。
7. Task 09 Cutover 前必须先打通最小 completion/outcome 闭环，Task 10 再封严完整 OFF/ON completion policy 与 OFF completed 后处理。
8. 修正版本血缘与所有 v0.2.2.2 交叉引用。

## v0.2.2.1 相对 v0.2.2 的封版小修

1. Smart Capture lifecycle 终态统一为 `completed/stopped/failed`；`cancel` 是父层/业务动作，不新增 `cancelled` status；父等待态统一 `child_cancelled_waiting_decision`。
2. 固定双层 authority：`fj_smart_captures` 是 child lifecycle/config/execution 权威；`fj_workflow_children` 是 parent relation/projection 权威。
3. Task 02 即落完整 `SmartCaptureExecutionConfig`（允许 `execution_config_json`），解决 Task 02/05 顺序冲突。
4. 补齐 Task 09 Cutover 后的 `ChildAdapter.start → SmartCaptureService.start` 启动契约，防止 pending child 无启动入口。
5. Smart Capture snapshot 固定单一整数 `state_version` 与 `capabilities {start,pause,resume,retry,stop}`，Smart Capture 自身不新增 `control_state`。
6. Task 04 只固定 snapshot contract/guard，不提前实现 Task 08A。
7. 原 Task 08 拆为 `08A Domain API/MCP backend` 与 `08B Renderer transport/Workspace/Skill`。
8. Reservation 本轮维持 active `job_id` 全局唯一；Smart Capture 历史浏览 UI 明确延期，不引入 ID route。

## v0.2.2 相对 v0.2.1 的补齐

1. 增加集中版本变更/复核说明，明确补执行契约而不改变已确认产品模型。
2. 新增 `E_状态_子任务_事件与实时传输契约.md`，固定状态存储、通用 child relation、控制来源、事件幂等、snapshot/polling、Cutover 旧入口和 OFF 后处理边界。
3. 把 Pipeline Schema 迁移从“增加 owner 字段”补成逐表 owner/FK/unique/index/SQLite table rebuild 验收，确保 independent 无 Workflow ID 也能持久化完整 Pipeline。
4. 把父子状态从“或等价 reason”补成 `control_state / waiting_reason / control_cause / state_version` 契约，防止各模块自行解释状态。
5. 把旧 Workflow live execution callback、resume+advance、App-level controller 等具体入口列为 Cutover 后的硬清理项。
6. 把完整 Smart Capture Execution Config、Smart Capture snapshot/polling 和 completed OFF 后处理 API 边界写死。

## v0.2.1 相对 v0.2 的重点修复

1. 增加 **Smart Capture Domain API / Codex transport** 独立任务，覆盖当前仍以 `workflow_run_id` 为唯一入口的 Analysis、Context、Handoff、MCP Skill、桌面自动交接控制器。
2. 明确 **迁移期间唯一执行 authority**：在正式 Cutover 前，不能同时让旧 Workflow Engine 与新 Smart Capture Engine 对同一 child 启动 BOSS/JD/Analysis/Prefetch。
3. 把 v0.2 的 Pipeline 大任务继续拆开：
   - Schema / Migration；
   - Repository / Query owner；
   - Search/Candidate/BOSS；
   - 08A Analysis/Context/Handoff Domain API + MCP backend；
   - 08B Codex renderer transport + Workspace/Terminal/Skill；
   - Prefetch/Reservation/Handoff + 最终 Engine Cutover。
4. 把 **Current Smart Capture slot** 与 **采集执行容量（Smart Capture vs 自定义采集）** 分成两个概念，并要求后端统一互斥。
5. 父子创建事务明确使用 SQLite 写事务/锁（`BEGIN IMMEDIATE` 或封装等价物），外部 BOSS/Codex side effect 必须在事务提交后发生。
6. 补齐 child `interrupted` / `failed` 的父级等待语义，禁止失败时自动越过 child。
7. 固化 child pause 的目标语义：**child pause → parent `waiting_child_paused`**；parent pause 才是真正的父 `paused`。
8. 固化 OFF 模式 completed 的终态不可回滚：完成后的人工 Manual Analysis 可以保留，但不能把 Smart Capture 改回 running/waiting，更不能重新阻塞已经继续的 parent。
9. 驾驶舱前端编排状态从第一期就按可扩展 child collection 建模，而不是写死一个 `enableSmartCapture` boolean。
10. 新增 `D_执行权切换与API调用链矩阵.md`，作为高风险迁移的核心护栏。

## 版本任务映射

| v0.2 | v0.2.1 |
|---|---|
| 00 基线 | 00 基线 |
| 01 Current/互斥 | 01 Current + 采集容量互斥 |
| 02 父子身份 | 02 原子创建 + 执行权过渡 |
| 03 状态机 | 03 状态机 + failed/interrupted |
| 04 Owner Seam | 04 Seam + Cutover Guard |
| 05 数据 Owner | 05 Schema Migration + 06 Repository Owner |
| 06 Pipeline Engine | 07 Search/Candidate/BOSS + 08A Domain API/MCP + 08B Renderer/Skill + 09 Prefetch/Cutover |
| 07 完成条件 | 10 完成条件 |
| 08 共享表单 | 11 共享表单 |
| 09 驾驶舱 UI | 12 驾驶舱 UI |
| 10 BossCapture Current | 13 BossCapture Current |
| 11 Workflow 区 | 14 Workflow 区 |
| 12 集成回归 | 15 集成回归 |
| 13 高风险复核 | 16 高风险复核 |

## 每个 Luna 新窗口建议提供

1. `00_总览_已确认决策与执行顺序_v0.2.2.4.md`
2. `00_版本变更与复核结论_v0.2.2.4.md`
3. `Codex_Luna_每窗口通用提示词_v0.2.2.4.md`
4. 当前编号任务文件
5. 任务相关矩阵：`A/B/C/D/E`
6. 上一个窗口最后输出的 Handoff

严格按顺序执行。**不要一次把多个高风险任务塞给 Luna。**

## 建议高级模型只读复核节点

- Task 03 后：父子 pause/resume/cancel/failure 状态机；
- Task 06 后：Schema + Repository owner 双 owner 风险；
- Task 08A/08B 后：Smart Capture Domain API 与 renderer/Codex transport；
- Task 09 后：最关键，Smart Capture Engine/Codex/Prefetch Cutover + ChildAdapter start + 最小 completion/outcome 闭环；
- Task 13/14 后：BossCapture current/history/control 状态隔离与 UI ownership。

详见 `16_Sol_Terra_高风险复核清单_v0.2.2.4.md`。
