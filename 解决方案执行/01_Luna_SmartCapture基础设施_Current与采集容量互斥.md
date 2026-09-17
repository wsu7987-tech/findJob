# 任务 01：Smart Capture 基础设施 / Current slot / Smart-Custom 采集容量互斥 / 重启恢复

> v0.2.2.4 封版：状态合法值、current pointer、pending recovery 和 capacity 规则必须与 `E_状态_子任务_事件与实时传输契约.md` 一致，不能只修 service 常量而遗漏 DB CHECK/schema type。

## 已确认决策 / 本任务主要完成事项

1. 接通 Smart Capture router 与 startup recovery。
2. 任意 Smart Capture 创建成功即占 current slot；`pending/paused/waiting_for_user/interrupted` 等非终态都占槽。
3. Current Smart Capture slot 与 BOSS **collection execution capacity** 分开建模。
4. Smart Capture 与“自定义采集”在所有入口严格互斥。
5. restart 后不显示幽灵 running，不用“最近更新时间”替代持久化 current identity。
6. 本任务不迁 Pipeline owner。

## 当前已核对问题

- `backend/app/routers/fine_job/smart_captures.py` 未完整注册；
- startup 未调用 `recover_interrupted_smart_captures()`；
- `ACTIVE_STATUSES` 缺 `pending/waiting_for_user`；
- linked create current 接管存在延迟；
- recovery 会按最近 active/updated_at 重新猜 current；
- Smart Capture router 直接看进程内 custom task，旧 Workflow `assert_collection_start_allowed()` 又维护另一套判断，互斥规则分散；
- 自定义采集至少从 BossCapture API、Codex tool 等多个入口启动。

## 两个互斥概念

### A. Current Smart Capture slot

只描述 Smart Capture identity：同一时刻最多一个非终态 Smart Capture。

### B. Collection execution capacity

描述 BOSS 采集执行能力：Smart Capture 和 custom capture 不允许并行占用。

要求建立统一后端 service/helper，使以下入口都调用同一规则，而不是各自猜：

- independent Smart Capture create/start；
- cockpit linked Smart Capture create/start；
- BossCapture custom capture start；
- Codex tool 触发 custom capture；
- 其他源码搜索到的采集启动入口。

## 目标行为

1. Smart Capture create 前在 DB 写事务中检查非终态 slot；存在则 409 `COLLECTION_TASK_ACTIVE` 或稳定同义错误。
2. `pending` 一创建即占 current。
3. paused/interrupted 不释放 Smart Capture slot。
4. completed/stopped/failed 不阻止下一条 create；current 可以保留最后终态快照，直到新 create 替换。`failed` 是 hard terminal 且同一 capture 不 retry；任何可恢复错误必须保持 `interrupted`/其他非终态，从而继续占槽。
5. 非终态 Smart Capture 存在时，custom start 必须被拒绝。
6. custom capture 正在实际占用采集执行器时，Smart Capture create/start 必须被拒绝。
7. restart：丢失进程执行器的 running/pausing 等收敛到 interrupted/安全等待；current ID 不漂移到“最近更新”的别的任务。
8. pending 外部启动失败后保留可恢复快照；只有明确 `start/stop/retry` 才改变其占槽语义。parent `cancel` 不是 Smart Capture capability，而是映射为 child `stop`。
9. custom capacity 的占用/释放由统一后端规则决定，重启和多入口情况下不能只依赖某个前端或单一进程内 manager。

## 允许修改

- Smart Capture router/service/schema/test；
- `main.py`；
- 统一 collection capacity service/helper；
- Boss/custom start path 与 Codex custom start path 的互斥接线；
- 必要 DB helper/index，但不迁 Pipeline 表 owner。

## 禁止事项

- 不迁 Candidate/JD/Analysis/Codex/Prefetch；
- 不重做 BossCapture UI；
- 不把 custom capture 强行改造成 Smart Capture；
- 不用“最近更新时间”作为 current identity fallback 来掩盖不一致。

## 自动测试（强制）

至少：

- router 非 404；
- create→current；
- pending/paused/waiting_for_user/interrupted 阻止第二 Smart Capture；
- terminal 后可 create；
- Smart Capture 非终态时 custom start 被拒；
- custom running 时 Smart Capture create/start 被拒；
- custom 互斥覆盖 BossCapture API 与 Codex tool 入口；
- restart 不幽灵 running；
- current 不因另一历史任务 updated_at 变化而漂移。
- Smart Capture `waiting_for_user` 作为 canonical lifecycle status 在 DB CHECK、schema、service/API 中一致；legacy `waiting_next_batch + waiting_reason` 只允许 migration/read mapping，不允许新写入；
- pending `start/stop/retry` 与 custom capacity 的并发竞态有测试；failed 不可 retry、interrupted retry 后仍保持同一 current identity；snapshot 可观察变化会单调递增 `state_version`。

## 用户手测

1. 建一个 paused Smart Capture，再尝试“自定义采集”，应明确拒绝；
2. 运行自定义采集，再尝试 independent Smart Capture，应明确拒绝；
3. 重启后端，原 running Smart Capture 不得仍显示后台运行。

## 高级模型复核

一般不需要。
