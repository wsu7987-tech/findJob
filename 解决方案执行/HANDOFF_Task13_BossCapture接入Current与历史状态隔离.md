# HANDOFF Task13：BossCapture 接入 Current 与历史状态隔离

## 1. 完成结论

Task13 已完成并完成本轮核对。BossCapture 的 current Smart Capture 由服务端 current pointer 决定，再按 `smart_capture_id` 读取详情；Workflow Run 只保留 linked parent 镜像和历史只读查询用途。

本轮复核另外修复了异步旧响应竞态：current Context、Analysis、历史 Context 都按请求代际、业务身份和 Context channel 校验；切换 linked parent 前停止旧父任务 SSE，current 切换期间的旧异步流程不会清空或重建新 current。

第二轮复核修复了三项 owner 混用：创建完成后重新读取 current pointer，不直接用 POST 响应切换页面身份；independent/completed Smart Capture 的 Manual Analysis 使用 Smart Capture Domain API；Smart/custom 公共岗位区按页签选择 owner，custom 启动不再清空仍由服务端保留的 terminal current 派生状态。

## 2. 状态源图

```text
GET /api/fine-job/smart-captures/current（初始化首帧）
              │
              └─ current/events（current 指针 SSE）
                    │ smart_capture_id
                    ▼
GET /api/fine-job/smart-captures/{smart_capture_id}（初始化首帧）
                    │
                    ├─ currentSmartCapture
                    │    ├─ Smart Capture status/progress/capabilities
                    │    ├─ Candidate/JD/jobs/result summary
                    │    └─ Smart Capture Analysis/Codex/Context Domain API
                    │
                    ├─ {smart_capture_id}/events（详情 SSE）
                    │
                    └─ workflow_run_id（仅 linked 时）
                              │
                              ▼
                     GET Workflow Run + linked parent SSE
                              │
                              └─ workflowStore.currentRun
                                   仅作为 linkedParentWorkflowRun 镜像

输入历史 Workflow Run B
              │
              └─ GET /api/fine-job/workflow-runs/{B}
                    │
                    └─ inspectedHistoricalRun
                         └─ inspectedContextSnapshot
                              只读，不写 current、linked parent 或 control source
```

## 3. Current / history store 分离

| 数据 | 当前来源 | 可执行控制 | 是否受历史 B 影响 |
|---|---|---|---|
| current Smart Capture | `/smart-captures/current` 首帧 + `/current/events` 指针流 + `/{id}/events` 详情流 | 由 `currentSmartCapture.smart_capture_id` 调 Smart Capture Domain API | 否 |
| linked parent | `currentSmartCapture.workflow_run_id` → Workflow Run | `workflowStore` 的 parent pause/resume/cancel/advance | 否 |
| inspected history | 手填 Workflow Run ID → `inspectedHistoricalRun` | 只读查看状态和 Context | 不会反向写入前两项 |
| current Analysis/Codex/Context | `smart_capture_id` Domain API | 使用当前 Smart Capture identity | 否 |
| historical Context | `inspectedHistoricalRun.workflow_run_id` | 只读 | 不会覆盖 current Context |

页面不再使用 `restoreLatest(false)` 推断 current，也不使用历史 Run 替换 `workflowStore.currentRun`。

## 4. 验收核对

- current 唯一从 `/api/fine-job/smart-captures/current` 初始化和刷新，再按 current 返回的 ID 读取详情。
- 创建 Smart Capture 的 POST 响应不直接切换页面 current；创建后仍通过 current API 确认身份。
- 同一 Smart Capture 只接受不低于已展示的 `state_version`；旧快照不会覆盖新快照。
- linked parent 只由 current snapshot 的 `workflow_run_id` 建立；切换父任务前关闭旧 SSE。
- independent current 不查询 latest Workflow，不建立父镜像；状态、岗位、Analysis、Codex、Context 仍由 Smart Capture domain identity 驱动。
- 历史 B 查询使用独立 `inspectedHistoricalRun`；清空历史输入不需要恢复 A。
- Context 类型下拉、Workflow Run 区域、Candidate/JD/Planner/Prefetch、Analysis、Codex 和自定义采集能力保留。
- independent completed 可使用 Smart Capture Manual Analysis；Smart 页签不显示 custom 详情/投递动作，custom 页签也不会合并 terminal Smart Capture 岗位。
- 未新增 `revision` 等第二套版本字段。

## 5. 自动测试与启动检查

自动测试：

```text
pnpm --filter fine-job-desktop test:run -- src/renderer/pages/fine-job/BossCaptureCurrent.test.ts
通过：1 个测试文件，6 项测试全部通过。
```

覆盖项包括 current 初始化与详情读取、创建后由 current API 确认身份、linked A / history B 隔离、历史 B 读取后控制与 Codex identity 不切换、independent 不恢复 latest parent、independent completed Manual Analysis owner、Smart/custom 岗位操作隔离、旧 `state_version` 不覆盖新快照，以及详情 SSE 更新 current 且页面不启动定时器。

后端 Smart Capture 回归：

```text
python -m pytest backend/tests/services/test_smart_capture_events.py backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/api/test_fine_job_smart_capture_domain_api.py -q
通过：30 项测试全部通过。
```

事件通道覆盖 current 指针与指定 Smart Capture 详情隔离，以及慢订阅者只接收最新快照。

类型检查：`npx vue-tsc --noEmit` 仍有 7 项 Task12 已记录的 fixture 类型错误；Task13 页面和测试未新增类型错误。

前后端使用独立端口启动检查：

- backend Uvicorn（18000）：health 与 Smart Capture current 均为 HTTP 200。
- fresh review database 的 current 为 `null`；current SSE 为 HTTP 200，并立即返回 `data: null` 首帧。
- desktop Vite（15173）：HTTP 200，包含 `<div id="app"></div>`。
- 检查结束后已正常停止两个独立进程。

## 6. 越界与遗留风险

本轮没有修改旧 `TaskCockpit.vue` 或产品布局，没有删除 Workflow 历史兼容能力。为支持 SSE，后端新增 Smart Capture 事件 broker 和两个只读事件端点，并在现有 Smart Capture 写入提交后发布快照；这属于移除 Task13 新增轮询的必要配套改动。API 类型补正和页面异步身份保护属于任务 13 当前/历史隔离的必要修复。

本轮没有新增页面级 Smart Capture 轮询；页面只保留自定义 BOSS 采集任务已有的定时轮询。Smart Capture SSE 需要在真实 BOSS/Codex 账号下执行 linked A → history B → pause/resume 手测，确认真实执行器事件到达时的状态顺序。

## 7. Task16 节点 D 复核

- Blocking issues：本轮发现的 current POST 身份切换、independent 误显 custom 操作、Manual Analysis 不可用和 custom 启动清空 current 派生视图均已修复；复核后无剩余 blocking issue。
- High-risk non-blocking issues：真实 BOSS/Codex 账号下的 linked A → history B → parent control 与 Codex 运行时顺序尚未手测；当前 SSE broker 为应用进程内事件通道，现有单进程桌面后端部署可用。
- 已确认安全项：current/history/linked parent/Codex identity 分离；independent 不恢复 latest parent；`state_version` 单调保护；Context ID/下拉、Planner、Prefetch、Manual Analysis 与 custom 能力保留；Smart Capture 未新增 `revision`、`cancelled` lifecycle 或 `cancel` capability。
- Task14 边界：Workflow 区域最终归类、父镜像显隐和通用“立即推进”清理仍按任务顺序留给 Task14，本轮没有提前执行。
