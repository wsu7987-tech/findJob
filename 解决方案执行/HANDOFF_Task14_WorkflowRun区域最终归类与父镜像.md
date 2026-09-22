# HANDOFF Task14：Workflow Run 区域最终归类与父镜像

## 1. 核对结论

Task14 通过。BossCapture 的三类 ownership 已分开：

- Smart Capture 执行详情：当前 Smart Capture 状态、进度、Planner、岗位/JD、Analysis、Prefetch、Codex、Recommend/Review、当前 Context。
- 历史 / Context 检查工具：Workflow Run ID、Context 类型下拉、历史 Run 状态和历史 Context，只读且独立显示。
- linked parent 镜像：仅当 current Smart Capture 的 `workflow_run_id` 有值时显示，展示父 ID、父状态、编排步骤、开始时间以及父 pause/resume/cancel 和返回驾驶舱。

历史 B 查询不会写入 `workflowStore.currentRun`，不会改变 current Smart Capture、linked parent A 或 Codex identity。independent Smart Capture 只隐藏父镜像，历史工具、Smart Capture、Analysis/Codex 能力仍保留。

## 2. 迁移前后功能清单

| 迁移前混合区域 | Task14 后归属 | 结果 |
|---|---|---|
| Workflow 状态、搜索进度、完成计数、Prefetch | Smart Capture 执行详情 | 保留并继续展示 |
| Planner / Combination | Smart Capture 执行详情 | 保留 |
| Candidate/JD、岗位列表、Recommend/Review | Smart Capture / 岗位区域 | 保留 |
| Analysis Queue / Result / Manual Analysis | Smart Capture Analysis | 保留 |
| Codex 查看、提交、重提、重交接 | Smart Capture Analysis/Codex | 已从父操作区域迁入，independent 仍可用 |
| 当前 Smart Capture Context | Smart Capture Context | 保留，按 `smart_capture_id` 读取 |
| Workflow Run ID 与 Context 类型下拉 | 历史 / Context 工具 | 保留，历史读取只读 |
| 历史 Run 状态与历史 Context | 历史 / Context 工具 | 保留，支持切换 Context 类型刷新 |
| 父 ID、状态、步骤、开始时间 | linked parent 镜像 | 仅 linked 显示 |
| 父 pause/resume/cancel、返回驾驶舱 | linked parent 镜像 | 保留，按 parent API 调用 |
| 通用“立即推进” | 无 | 按任务明确允许项删除 |

## 3. 明确删除项

仅删除通用人工“立即推进”按钮及其页面 handler `advanceSmartWorkflowRun`。Workflow advance API、store 能力和其他页面的合法卡点动作未删除。

## 4. 本轮复核修复

- 父镜像 pause/resume/cancel 显隐改为依据 parent `control_state/control_cause`，避免 `waiting_child_interrupted` 等子任务卡点误显示不可用的父 resume。
- 历史工具和当前 Context 的类型下拉统一刷新可见的 current/history 快照；历史 B 仍不会改变 current 或父镜像。
- 第二轮复核将 Planner、Prefetch、执行进度和完成进度改为读取 Smart Capture snapshot，independent 不再因缺少父 Workflow 而丢失这些展示。
- 第二轮复核补齐 independent Smart Capture `start` 与通用 `retry` capability 对应的“启动当前采集 / 重试采集”入口；linked pending 仍只由父编排启动，暂停、继续、停止按 snapshot capability 显示。

## 5. Authority 与 parent cancel

- current 来源：Smart Capture current pointer，再按 `smart_capture_id` 读取详情。
- linked parent 来源：仅由 current Smart Capture 的 `workflow_run_id` 读取并建立父镜像。
- 历史来源：`inspectedHistoricalRun` 与 `inspectedContextSnapshot`，不写入控制 store。
- 父镜像停止按钮调用 parent `cancel`；后端 `parent_cancel_child_in_connection` 在同一事务将 parent 置为 cancelled，并将 linked Smart Capture child 置为 stopped。前端没有拼接 child stop 调用。

## 6. 测试与启动验证

- UI：`pnpm --filter fine-job-desktop test:run -- src/renderer/pages/fine-job/BossCaptureCurrent.test.ts`，11/11 通过。
- 驾驶舱卡点回归：`TaskCockpitNew.test.ts`，3/3 通过。
- 后端 parent cancel/child stop 回归：5 通过，45 项未选中。
- `npx vue-tsc --noEmit`：保留 7 项既有 fixture 测试类型错误，未新增 Task14 页面或测试类型错误。
- 启动验证：后端 `/api/health`、`/api/fine-job/smart-captures/current`、current SSE 均正常；前端 `http://127.0.0.1:15173` 返回 200，应用根节点存在。临时服务已停止，端口已释放。

## 7. 轮询核对

本任务没有新增定时轮询设计，不需要更新 `任务01_轮询风险核对.md`。当前 Smart Capture 使用 current/详情 SSE，linked parent 使用既有 Workflow SSE；store 中的 `startPolling` 只是历史方法名，实际建立 EventSource。自定义 BOSS 采集任务的既有定时轮询保持原样。

## 8. 遗留风险

真实 BOSS/Codex 登录环境下的 linked A → 历史 B → 父控制顺序仍需人工手测；自动回归已覆盖 current/history/parent identity 隔离、父镜像显隐、子任务中断时的控制按钮、pending/interrupted 语义化动作、independent Planner/Prefetch 和 Context 类型切换。
