# 任务 06：Pipeline Repository / Query Owner 切换 + 历史兼容

> v0.2.2.4 封版：必须按 `E_状态_子任务_事件与实时传输契约.md` 和 Task 05 的逐表 owner 矩阵实现。repository 不得通过“没有 Workflow ID 就回退旧函数”掩盖 schema 缺口。

## 已确认决策 / 本任务主要完成事项

在 Task 05 schema 已存在的前提下，把**新业务写入/查询**切到 `smart_capture_id`，同时保留旧 Workflow Run 历史查看兼容。

本任务仍不做 production Engine Cutover。

## 实施要求

1. 新 repository/service 函数优先接收 `smart_capture_id` / execution owner context。
2. 新数据写入必须写 `smart_capture_id`；independent 不需要伪造 workflow id。
3. linked 的 `workflow_run_id` 仅作为 parent/history link，不得决定 current/completion/active。
4. 历史 Workflow Run B 的 Context/状态仍能通过 legacy read adapter 查询。
5. 对无法回填的 legacy data 使用显式 compatibility read path，不让新运行链反向依赖 legacy owner。
6. Repository 层不得出现“如果没有 workflow_run_id 就不能 Analysis/Context/Handoff”的假设。
7. 双 owner 过渡期间定义单一权威：**新业务 Smart Capture owner；旧 Workflow id 只读历史/关联。**
8. 保留现有 Workflow API tests 的历史读取语义；新增 Smart Capture owner repository tests。
9. `workflow_task_id`、`workflow_run_id` 等 legacy identity 不能作为 independent 的隐式前置条件；需要 domain task/analysis identity 时必须显式建立。
10. 新写入、completion、active/current、control capability 均以 Smart Capture owner 为准；legacy adapter 只能读取或转换历史，不得反向成为 new owner。

## 重点检查

- Search/Discovery；
- Analysis items/batches；
- Context；
- Handoff；
- Prefetch；
- Reservation；
- completion progress；
- Manual Analysis。

## 自动测试

- linked 新写入按 Smart Capture 查询完整；
- independent `workflow_run_id=null` 的全部 repository CRUD；
- historical Run Context 可读；
- historical Run 查询不会成为 new owner；
- independent discovery/JD/Analysis/feedback/Codex session 不通过 Workflow-only FK；
- reservation/prefetch/handoff 旧测试不降断言。

## Cutover Guard

Task 06 后 production live Engine 仍不能双跑。只允许 repository 已支持新 owner，新 Engine 正式启用等 Task 09。

## 高级模型复核

**强烈建议。** 重点审双 owner、fallback 是否会偷偷把 Workflow 重新变成业务权威。
