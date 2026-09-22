# Task 12 新驾驶舱编排 UI 与快递式时间线核对交接

## 1. 核对结论

Task 12 验收通过。当前实现覆盖 idle、parent terminal、running、暂停、child waiting、failed waiting decision 和 terminal result 保留状态；新驾驶舱没有读取 Smart Capture 的 Prefetch、Handoff 或 Analysis 私有字段推导父任务状态。

本轮复核发现并修复两点：

1. child 恢复/重试成功后，Store 现在回读 Workflow Run 父快照，驾驶舱父子投影会同步刷新。
2. child 恢复/重试按钮现在同时检查对应 child projection 的 `control_state` 与后端 `capabilities`，避免把操作展示给其他 child。
3. 勾选 child 后自动打开对应配置 Drawer，并保留独立“配置”入口。

## 2. 验收项核对

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| idle 显示编排区 | 通过 | `TaskCockpitNew.test.ts`：idle 场景存在 `orchestration-panel` |
| 勾选 child → Drawer | 通过 | CheckboxGroup `change` 事件打开 Drawer；组件测试已覆盖 |
| 配置完整度 | 通过 | 复用 Task 11 的 `SmartCaptureConfigForm`、`childConfigs`、`childValidation` 和共享校验 |
| running 后隐藏编排区 | 通过 | `canEditOrchestration` 仅允许未创建或 parent terminal 场景显示 |
| 快递时间线 | 通过 | 只读取 `children[]` projection 的 child 状态、等待原因、能力、结果摘要和时间字段 |
| 岗位采集跳转无 ID | 通过 | 组件调用 `router.push({ name: "fine-job-capture" })`；测试断言无 params/query |
| pause/resume | 通过 | 页面按钮按父 `control_state` 显示；Store 父恢复只调用 resume endpoint，不追加旧 advance |
| stop/failed waiting decision | 通过 | failed child 不显示重试；显示“跳过该子任务继续”“结束父任务”，测试已点击验证 skip 调用 |
| terminal 后配置区重新出现且旧结果保留 | 通过 | terminal 场景重新显示编排区，child timeline 仍显示 `result_summary.short_summary` |
| 旧 SSE 快照保护 | 通过 | 更低父 `state_version` 不覆盖当前父子投影 |

## 3. 路由与页面状态文字证明

- 驾驶舱新页面路由：`/fine-job/cockpit`，名称 `fine-job-cockpit`。
- 岗位采集页面路由：`/fine-job/capture`，名称 `fine-job-capture`。
- 时间线“查看岗位采集”只按路由名导航，不携带 Smart Capture ID、Workflow Run ID 或 query。
- idle/terminal 页面文字：`新任务编排`、`岗位采集`、`配置完整`/`待补充配置`、`建立并自动推进`。
- running 页面文字：`父任务状态：active`、`暂停`、`停止任务`，无 `orchestration-panel`。
- failed waiting 页面文字：`父任务状态：child_failed_waiting_decision`、`跳过该子任务继续`、`结束父任务`，无 `重试子任务`。
- timeline 结果文字示例：`结果摘要：已采集 8 个候选`。

## 4. 测试与启动检查

### 组件/Store 定向测试

执行：

```text
pnpm --filter fine-job-desktop test:run -- src/renderer/pages/fine-job/TaskCockpitNew.test.ts src/renderer/stores/fineJobWorkflowRun.test.ts src/renderer/services/smartCaptureExecutionConfig.test.ts
```

结果：3 个文件、10 项测试全部通过。

### 前端类型检查

执行 `npx vue-tsc --noEmit`。新增 Task 12 源码没有新增类型错误；仍有 7 项既有测试 fixture 类型错误，位置在旧 `TaskCockpit`、聊天策略和 Workflow Codex 测试。

### 前后端启动检查

复核本地服务：

- 后端 `http://127.0.0.1:8000/api/health`：HTTP 200。
- 前端 `http://127.0.0.1:5173/`：HTTP 200。
- `GET /api/fine-job/smart-captures/current`：HTTP 200。
- `GET /api/fine-job/workflow-runs/latest?include_completed=true&created_from=task_cockpit`：HTTP 200。

## 5. 越界与必要性

- 改动范围保持在新驾驶舱页面、Workflow Run Store 及对应测试；没有修改旧 `TaskCockpit.vue`、BossCapture 页面、后端执行链或 Pipeline owner。
- Workflow Run Store 的 child 恢复/重试回读属于父子投影同步所需的配套修复，和本任务 UI 操作范围直接相关，保留合理。
- 共享配置表单和校验继续复用 Task 11 结果，没有复制第二套岗位采集配置逻辑。
- 没有新增产品功能、后端控制入口或 Workflow `advance` 入口。

## 6. 轮询风险

本轮没有新增定时轮询设计，也没有修改 `任务01_轮询风险核对.md`。Workflow 状态仍通过已有 EventSource SSE；child 恢复/重试后的父快照回读只由用户操作触发一次，不属于定时轮询。

## 7. 遗留风险

- 尚未进行真实 BOSS/Codex 账号下的采集手测，因此真实浏览器、采集器和 Codex 运行时行为仍需后续窗口验证。
- `vue-tsc` 的 7 项既有 fixture 类型错误仍待独立清理。
