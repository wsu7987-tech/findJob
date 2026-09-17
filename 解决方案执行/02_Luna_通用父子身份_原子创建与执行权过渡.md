# 任务 02：通用父子身份 / SQLite 原子创建 / Child 契约 / 迁移期执行权过渡

> v0.2.2.4 封版：本任务必须同时阅读 `E_状态_子任务_事件与实时传输契约.md`。Child relation 不得只存在于 payload、内存 Store 或前端 timeline。

## 已确认决策 / 本任务主要完成事项

- Workflow Run 是父编排；Smart Capture 是当前首个 child implementation。
- 驾驶舱勾选岗位采集后，一次业务创建必须得到一致的 parent + linked Smart Capture + child ref + current slot。
- independent Smart Capture 只创建 Smart Capture，不创建隐藏 Workflow Run，也不创建伪 `fj_workflow_children` relation；因此它不发送 parent orchestration outcome event。
- 父层以后会编排更多 child，所以 child 契约必须通用。
- **本任务只建立 identity/contract，不启动第二套 Pipeline Engine。**

## 目标父子模型

父层至少能持久化/返回：

```text
child_type
child_ref / smart_capture_id
status
started_at
completed_at
short_summary / result_summary
capabilities or waiting_reason
```

父层不读取 Prefetch/Handoff/BOSS batch 私有字段决定 child 生命周期。

## 通用 child relation（强制）

默认新增或重建一张通用 child relation 表（建议名：`fj_workflow_children`），不得把当前首个 Smart Capture 的关系继续塞进 Smart Capture 私有字段或 Workflow JSON。

至少保存：

```text
id / workflow_run_id / child_type / child_ref / sequence
status / control_state / waiting_reason / control_cause
capabilities_json / result_summary_json
started_at / completed_at / created_at / updated_at
state_version / child_state_version
```

必须有：

- `(workflow_run_id, child_type, child_ref)` 唯一约束；
- child 顺序字段；
- parent 读取 child outcome 的稳定 service/API；
- parent 与 child 双向关系可由数据库查询校验；
- relation `state_version` 与 `child_state_version` 分离：前者是 parent projection 版本，后者是最后接受的 child lifecycle version；
- 后续增加第二、第三种 child 时不需要改写 Smart Capture 专用字段。

如果复用现有 `fj_workflow_tasks`，必须证明不会把 Smart Capture 内部 JD/Analysis task 与 parent child relation 混为一类，并提供同等字段、FK、唯一约束和历史兼容。

## SQLite 原子创建要求（强制）

驾驶舱开始任务时：

```text
validate child config
→ BEGIN IMMEDIATE（或项目 DB wrapper 的等价 SQLite write lock/transaction）
→ 检查 Smart Capture current slot
→ 检查 collection capacity / custom conflict
→ INSERT parent
→ INSERT linked Smart Capture(pending, execution_config_json=完整 SmartCaptureExecutionConfig)
→ INSERT child relation（含 sequence/config reference）
→ SET current smart_capture
→ COMMIT
→ 事务成功后才允许启动任何外部 BOSS/Codex side effect
```

要求：

- 任一步 DB 失败整体 rollback；
- 不把“先 parent commit、失败后再补偿 child”作为正常最终方案；
- 网络/BOSS/Codex/browser 调用不放在数据库写事务内；
- parent 与 child 双向关系一致性可校验。
- child relation、**完整 SmartCaptureExecutionConfig**、current pointer 必须与 parent 在同一事务中落库；允许使用 `execution_config_json` 作为本阶段的原子配置载体，Task 05 不得再把 Workflow contract 当隐藏 config owner；
- 创建请求需要可重试，但重试不能产生第二组 parent/child；
- commit 成功而外部启动失败时，保留 `pending`/`interrupted` 快照并提供明确 `retry/stop`，不得靠最近更新时间猜 current；parent `cancel` 如需终止 child，映射到 child `stop`。

## 父子 Authority（强制）

- `fj_smart_captures`：child lifecycle/config/execution 权威；
- `fj_workflow_children`：parent→child canonical relation + parent orchestration projection 权威；
- parent 不能通过更新 `fj_workflow_children.status` 直接“写回” Smart Capture lifecycle；
- `fj_smart_captures.workflow_run_id` 仅保留 linked 导航/历史 link，并与 relation 在本任务同一事务中保持一致。

## 迁移期间唯一执行 authority

Task 02 完成后：

- linked Smart Capture 已是正式 child identity/current owner；
- 但 Pipeline 生产执行 authority 仍保持**一套**，直到 Task 09 Cutover；
- 不允许 parent 旧 Engine 和 SmartCapture 新 Engine 同时启动同一 child 的 BOSS/JD/Analysis/Prefetch；
- 若为过渡需要 bridge，bridge 必须显式委托唯一旧执行路径，并在 Handoff 标记；不能双启动。

## Cutover 后的 Child Start 契约（本任务先定义，不提前切换）

Task 09 后父编排只能通过通用 child adapter 启动当前 child：

```text
Parent Orchestrator
→ ChildAdapter.start(child_type, child_ref)
→ SmartCaptureService.start(smart_capture_id)
→ pending → running
```

父层不得直接调用 BOSS/JD/Analysis/Prefetch；Task 02 只建立接口/契约，不在 Cutover 前启用第二套 production Engine。

## 双向互斥测试

- linked pending 已创建 → independent create 409；
- independent nonterminal 已存在 → cockpit create（包含 Smart Capture child）整体失败，不留下孤儿 parent；
- custom 正在采集 → cockpit create 不留下 parent/child 半成品；
- 并发两个 cockpit create → 只有一个成功。
- 创建响应丢失后重新进入页面 → 能看到同一条 pending current，不重复创建；
- pending 显式 `stop` 后才释放 current/capacity，不能由后台按时间猜测释放。

## 禁止事项

- 不创建隐藏 Workflow Run 给 independent；
- 不在 parent 写 Smart Capture 私有 Pipeline 状态；
- 不在本任务迁 Pipeline tables；
- 不在本任务切换 Engine authority。

## 用户手测

尝试快速连续点击两次“开始父任务”；数据库最终只能有一组一致 parent+linked child，第二次明确失败且无孤儿记录。

## 高级模型复核

建议在 Task 03 一起复核事务边界与状态机。
