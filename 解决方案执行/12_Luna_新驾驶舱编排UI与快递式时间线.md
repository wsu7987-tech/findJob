# 任务 12：新驾驶舱编排 UI + 快递式时间线

> v0.2.2.4 封版：读取父/child 状态时必须遵守 `E_状态_子任务_事件与实时传输契约.md`，使用后端 `control_state/waiting_reason/capabilities/state_version`，不能根据 Smart Capture 私有字段猜测父步骤。

## 已确认决策 / 本任务主要完成事项

- 只修改**新驾驶舱相关实现范围**；旧 `TaskCockpit.vue` 不动、不复用。
- 允许修改 `TaskCockpitNew.vue` 以及为其服务的 component/store/type/service/API。
- idle/parent terminal 显示编排区；
- 勾选 child → 完整度 → 右侧 Drawer；
- 父非终态运行/等待/暂停时隐藏整个可编辑编排区；
- 驾驶舱只显示父状态、快递式 child 时间线、合法操作、结果摘要；
- 岗位采集内部详情留 `/fine-job/capture`；导航无 ID。

## 可扩展 child 配置模型（强制）

Phase 1 虽只有 Smart Capture，也不要写死成一个永久 `enableSmartCapture` boolean。

建议数据形态类似：

```text
selectedChildren[]
childConfigs[childType]
childValidation[childType]
```

具体 Vue 实现可调整，但必须能自然增加第二种/第三种 child。

## 非终态规则

以下不能重新显示可编辑编排区：

- running；
- paused；
- waiting child；
- `waiting_child_paused`；
- `child_cancelled_waiting_decision`；
- `child_failed_waiting_decision`；
- interrupted/其他可恢复 waiting。

只有 parent 真 terminal（completed/cancelled/failed 等）才重新显示。

## 时间线数据

父快照提供通用 child step summary：key/label/status/control_state/started_at/completed_at/short_summary/child_type/ref/waiting_reason/control_cause/capabilities/state_version。

前端不要读取 Smart Capture Prefetch/Handoff 私有字段推导父步骤事实，也不要用旧 `status`/`paused` 布尔值替代 `control_state`。

## 按钮

- running：pause/cancel；
- parent paused：resume/cancel；
- child paused waiting：显示等待与进入 child/适当 resume；
- `child_cancelled_waiting_decision`：skip child / 结束父任务；
- child interrupted：仅在后端 `retry/resume` capability 为 true 时显示对应恢复动作；
- child failed：视为 terminal hard failure，不显示同一 child retry；**（v0.2.2.4 已确认）** 显示与 `child_cancelled_waiting_decision` 相同的两个按钮：跳过该 child 继续 / 结束父任务，不提供万能 advance。

## 结果保留

父终态后编排区重现，但上一轮 result/timeline 不自动清空；真正创建下一 parent 时再切新显示上下文。

## 自动测试

idle/running/waiting/terminal 可见性；child collection 配置模型；完整度；路由无 ID；取消/失败 waiting；terminal 结果保留。

## 用户手测（强制）

Drawer 配置→开始→编排区消失→查看岗位采集 URL 无 ID→pause/resume→child stop waiting→skip→完成后编排区重现且旧结果仍在。

## 高级模型复核

一般不需要。
