# 任务 11：共享岗位采集配置表单 + 条件必填校验

> v0.2.2.4 封版：表单输出必须落为 Smart Capture 的完整 `SmartCaptureExecutionConfig`，字段 owner 以 `E_状态_子任务_事件与实时传输契约.md` 为准。

## 已确认决策 / 本任务主要完成事项

BossCapture 与驾驶舱 Drawer 共用配置组件/数据模型/校验，不共用 submit action。

## 字段保护

以 BossCapture 当前真实字段为基线，至少核对：搜索/筛选、关键词、城市、候选目标、采集参数、投递目标、Recommend/Review、target mode、Analysis batch、Codex model/reasoning/handoff、Context budget、analysis guidance、stop policy 等。

抽组件不等于删字段，也不得静默修改既有默认值/回显。

## 条件校验

- 始终必填：真正采集所需字段；
- `delivery_target_enabled=false`：不能因为 Codex/recommendation 字段为空判“不完整”；
- `true`：才要求 Recommendation/Codex/Recommend target/Analysis 等真正必需字段；
- 数值关系与后端 validator 一致；
- 组件输出 `isValid/errors/missingFields`，供驾驶舱显示完整度。

## 提交边界

- BossCapture submit → independent Smart Capture；
- Cockpit submit → parent orchestration create + linked child；
- 共享组件内部不得自行 create 任务。

## 配置持久化 owner（强制）

共享表单提交的完整配置必须在 Smart Capture 创建事务中保存，至少包括：

```text
search/filter/keyword/city
candidate_target_count
delivery_target_enabled
JD/detail policy
analysis batch / Codex model / reasoning / handoff
recommend/review targets
context budget / analysis guidance
stop policy
```

- BossCapture independent 与 Cockpit linked 使用同一配置 schema 和后端 validator；
- parent 只能保存 child config reference 或必要校验快照，不能成为 independent ON 运行依赖；
- 后端返回的 Smart Capture snapshot 必须能回显同一份配置；
- OFF 模式缺少 Codex 字段时仍能创建并完成候选采集；ON 模式所需字段必须在服务端再次校验。

## 自动测试

OFF/ON 条件校验；BossCapture/Cockpit 同配置同结果；保存/回显字段不丢。
- independent/linked 的完整 config owner 一致；没有隐藏 Workflow config 依赖；
- 创建后从 Smart Capture snapshot 读取配置，字段完整且默认值不漂移。

## 用户手测

关闭投递目标不填 Codex 仍完整；开启后清空 Codex/策略即时不完整。

## 高级模型复核

不需要。
