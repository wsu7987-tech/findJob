# 任务 14：BossCapture Workflow Run 区域最终归类 / 父任务镜像 / 显隐

> v0.2.2.4 封版：历史检查、Smart Capture detail、linked parent mirror 的状态源必须分别遵守 `E_状态_子任务_事件与实时传输契约.md`，不能因为复用组件而共用 control/polling state。

## 前置条件

Task 13 的 current / linked parent / inspected history / Smart Capture Domain 状态已通过测试，否则不要做本任务。

## 已确认决策 / 本任务主要完成事项

把当前“大杂烩”区域按 ownership 迁移成三类，**保留既有能力，不通过删除完成整理**。

### A. Smart Capture 执行详情

Planner、Candidate/JD、Analysis、Prefetch、Codex、Recommend/Review、当前 Smart Capture Context。

### B. 历史 / Context 检查工具

保留：Workflow Run ID 输入、Context 类型下拉、查看历史 Run/Context/状态。只读；independent Smart Capture 时也可存在。

### C. linked parent 镜像

只有 `currentSmartCapture.workflow_run_id != null` 时显示。

只包括：父 ID/状态/当前编排步骤/开始时间、pause/resume/cancel、返回驾驶舱。UI 若显示“停止父任务”，其业务动作仍是 parent `cancel`。

父镜像里的 pause/resume/cancel 调父业务接口，并由后端按状态机同步 child；其中 parent `cancel` 由后端映射为 Smart Capture child `stop`，不在前端直接调 child stop，也不拼两次调用。

## “立即推进”

删除通用人工 `立即推进` UI。

合法卡点必须仍有具体 action：resume/retry/返回驾驶舱/跳过取消 child（驾驶舱）等。

## Codex

从“父 Workflow 操作”语义中迁到 Smart Capture Analysis/Codex 区。independent 必须仍有全部 Codex 能力。

## 历史 ID/Context

- 不删除；
- 不隐藏在 linked parent `v-if` 内；
- 不允许它改变 current/parent/Codex execution identity。

## 页面保护

迁移时尽量移动/抽 component，不大段重写 BossCapture。未在迁移台账标记可删除的能力不得消失。

## 自动测试

- linked：父镜像显示且控制正确；
- independent：只隐藏父镜像；
- historical tools 两种来源都可用；
- 通用立即推进不存在；
- Codex/Prefetch/Planner 仍存在；
- history B 不污染 linked A。

## 用户手测（强制）

linked A → 输入历史 B 看 Context → 父控制仍 A；independent → 父镜像不显示，但历史工具和 Smart Capture/Codex 完整。

## 高级模型复核

建议与 Task 13 一起复核。

## Authority 保护（v0.2.2.4）

父镜像展示的数据来自 parent snapshot / `fj_workflow_children` projection；不能通过写镜像 Store 直接改变 Smart Capture lifecycle。历史 inspected Run 仍只读。
