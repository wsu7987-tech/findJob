# 任务 13：BossCapture 接入真正 Current Smart Capture + linked/history 状态隔离

> v0.2.2.4 封版：current/详情刷新必须遵守 `E_状态_子任务_事件与实时传输契约.md` 的 Smart Capture snapshot/polling contract，不得以 Workflow SSE 或 latest Workflow Run 代替。

## 前置条件

Smart Capture Engine 已在 Task 09 Cutover；不要让 BossCapture 再恢复旧 Workflow Engine 成为业务 owner。

## 已确认决策 / 本任务主要完成事项

- 页面 current 权威来源为 `GET /api/fine-job/smart-captures/current`；
- `currentSmartCapture` 不再从 Workflow Run 拼装；
- 侧边栏/驾驶舱进入同一路由、同 current；
- linked parent 只由 `currentSmartCapture.workflow_run_id` 获取；
- 历史 Run ID 查询用独立只读 `inspectedHistoricalRun`；
- 本任务**暂时不删除/隐藏 Workflow Run 大区域，不做最终布局清理**。

## 目标状态源

```text
currentSmartCapture          # 服务端 current，业务权威
linkedParentWorkflowRun      # currentSmartCapture.workflow_run_id，可控制
inspectedHistoricalRun       # 手填 Workflow Run ID，只读
inspectedContextSnapshot     # 跟随 inspected run/channel，只读
```

同时 Smart Capture Analysis/Codex 自身 snapshot 使用 Smart Capture Domain API，不借 inspected/linked Workflow Store 反推。

## Snapshot / polling 验收

- 初始化先读取 `GET /api/fine-job/smart-captures/current`；
- 详情和进度读取 `GET /api/fine-job/smart-captures/{id}`，返回 `state_version`；
- polling 或 scoped SSE 始终绑定 `currentSmartCapture.id`；
- current 变化只由服务端 current pointer 变化驱动，不由页面更新时间猜测；
- independent 没有 Workflow ID 时仍能持续看到 progress、capabilities、waiting reason 和 terminal result；
- history Run B 的读取永远不能替换 Smart Capture polling、control、Codex task 或 parent mirror source。

## 历史 ID 强制验收

当前 linked parent=A：

1. 输入历史 Run B；
2. 能读 B 历史 Context/状态；
3. current Smart Capture 不变；
4. linked parent 仍 A；
5. pause/resume/stop 仍作用 A/当前 child；
6. 不切控制 SSE/polling 到 B；
7. Codex current task 不切到 B；
8. 清空 B 后无需“恢复 A”，因为 A 从未被替换。

## independent current

- 不调用 latest Workflow Run 当 parent；
- linked parent=null；
- Smart Capture Candidate/JD/Analysis/Codex/Prefetch 全部可用；
- 历史 Workflow Run ID/Context 工具仍可查看旧 Run。

## 禁止事项

- 不删 Context 下拉；
- 不删 Codex/Planner/Prefetch；
- 不做 `v-if=workflow_run_id` 把整个旧 Workflow 大块直接隐藏；
- 不带 route ID 选择 current；
- 不让 inspected run 替换当前控制对象；
- 不恢复 `restoreLatest(false)` 作为 current identity fallback。
- 不使用 Workflow SSE 作为 independent Smart Capture 的唯一实时来源；
- 不因为 current snapshot 暂时为空就自动恢复 latest Workflow 或启动旧 Engine。

## 自动测试

current API 初始化；linked A/history B 隔离；independent 不恢复 latest parent；side nav/cockpit 同 current；控制/Codex polling 不被 history 污染；断线后按 `smart_capture_id + state_version` 恢复，且 API 不出现第二套 `revision` 字段。

## 用户手测（强制）

linked A → 输入旧 B 看 Context → pause/resume 仍作用 A；independent current 时不会自动镜像任何旧 parent，Codex 功能仍可用。

## 高级模型复核

**建议。** 只审状态源污染/current identity。
