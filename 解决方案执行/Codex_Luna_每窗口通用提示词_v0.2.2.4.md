# Codex Luna 每窗口通用提示词 v0.2.2.4

请只执行当前提供的一个编号任务，不顺手完成后续任务。

## 必须先读

1. `00_总览_已确认决策与执行顺序_v0.2.2.4.md`
2. `00_版本变更与复核结论_v0.2.2.4.md`
3. `E_状态_子任务_事件与实时传输契约.md`
4. 当前编号任务文件
5. 与任务相关时阅读 `A_现有功能迁移台账.md`、`B_父子任务控制状态矩阵.md`、`C_Pipeline_Owner与历史兼容矩阵.md`、`D_执行权切换与API调用链矩阵.md`
6. 上一个窗口 Handoff

## 开工前

- `git status` / `git diff`，不要覆盖前序任务。
- 用代码搜索验证任务书指出的现状；若前序任务已改变源码，以产品约束 + Handoff 为准。
- 先找到现有测试，再修改代码。
- 明确本窗口是否处于 **Cutover 前**；Task 09 之前不得让旧/新 Engine 对同一 live child 双跑。

## 优先级

1. 总览已确认产品决策；
2. 当前任务；
3. E 状态/事件/实时传输契约与 A/B/C/D 矩阵；
4. 前序 Handoff 与已通过测试；
5. 当前代码现状。

历史坏代码不因“少改”而自动成为目标设计。

## 修改纪律

- 不大范围格式化/重命名无关文件。
- 旧 `TaskCockpit.vue` 不修改、不复用。
- BossCapture 默认迁移/接线/归类，不是删掉重写。
- 不删除 Planner、Candidate、JD、Analysis、Codex、Prefetch、Context、历史 Run ID、Context 下拉、Manual Analysis 等能力降低难度。
- 不给 independent Smart Capture 新建隐藏 Workflow Run。
- 不新增第二套业务权威状态源。`fj_smart_captures` 是 child lifecycle/config/execution 权威；`fj_workflow_children` 是 parent relation/projection 权威。
- 前端不得连续调多个业务 API 拼父子一致性。
- 不用通用 `advance` 绕过具体 waiting/blocked reason。
- 数据库 migration 必须支持已有数据库原地升级。
- 不得把“或等价状态”当作不定义字段/映射的理由；状态、waiting reason、control cause、state_version 必须有唯一解释。`waiting_for_user` 是人工阻塞的 canonical Smart Capture lifecycle status；新写入不得改用 `waiting_next_batch + reason`。Smart Capture 不新增 `cancelled` lifecycle；Smart Capture command/capability 使用 `stop`，parent `cancel` 映射为 child `stop`，最终 lifecycle 为 `stopped`。
- Pipeline owner migration 必须逐表验证 FK、复合主键、唯一索引和 independent 无 Workflow ID 的 insert/read。
- parent child relation、child event 幂等、Smart Capture snapshot/polling 必须有后端持久化/API 测试。linked Child outcome event 必须带 relation identity，并以 `(child_relation_id,event_id)` 幂等；relation 还必须保存 `child_state_version`，只接受更高 event `state_version`。independent 不创建 parent relation/outcome event。Smart Capture snapshot 只使用整数 `state_version`，任何可观察字段变更都使其单调递增，不得新增 `revision`；capabilities 固定为 start/pause/resume/retry/stop booleans，且 `failed` terminal 必须 `retry=false`。
- 任何外部 BOSS/Codex side effect 不应发生在 parent/child identity 创建写事务内。
- Task 09 之前禁止正式 production Engine 双 authority；Task 09 Cutover 前还必须验证最小 completion/outcome 闭环；Task 09 之后禁止 live fallback 回旧 Workflow Engine。

## 遇到不确定语义

不要擅自扩展产品模型。优先保留已有能力和数据；在最后“偏差/风险”中报告。**（v0.2.2.4 已确认，不再是待定项）** hard failure 与用户主动 stop 共享同一父层决策集合：跳过该 child 继续后续编排 / 结束父任务；hard `failed` 本身仍明确不可对同一 child retry，skip 不改变这一点。除此之外若再遇到任务书未覆盖的新语义，不要自行发明。

## 完成输出格式

### A. 已完成
逐条对应任务书。

### B. 改动文件
路径 + 一句话。

### C. 测试
实际命令、通过/失败、未跑原因。

### D. 保护性回归
迁移台账中哪些能力已确认未丢。

### E. Authority / Owner 状态
本窗口结束后：current owner、Pipeline data owner、production execution authority、历史兼容路径分别是什么。

### F. 与任务书的偏差 / 未决风险
无则写“无”。

### G. 下一窗口 Handoff
10～30 行：schema/API 变化、migration、owner/authority、仍保留的 legacy adapter、下一任务最危险点。
