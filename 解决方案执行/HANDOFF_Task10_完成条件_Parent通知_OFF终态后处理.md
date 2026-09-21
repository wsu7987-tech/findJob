# Task 10：完成条件、Parent 通知与 OFF 终态后处理交接

## 1. 验收结论

- OFF：`candidate_count >= candidate_target_count` 时 Smart Capture 完成并写入稳定 `result_summary`。
- linked OFF：child completion event 仅消费一次，父任务保留 child 摘要并完成；independent 不创建 relation/event。
- ON：候选达标后继续 JD/Codex/Prefetch，Recommend/Review 结果目标满足后才完成。
- completed 后 Manual Analysis 使用 `smart_capture_id`，不回滚 child/parent，不重复发送完成事件，不启动 Prefetch。
- 终态后的迟到 BOSS 回调在 listener 入口结束，不再进入自动 Engine。
- OFF 独立采集在搜索耗尽但候选未达标时保持 `waiting_next_batch`；批次快照同步不提前判定完成。

## 2. 代码与测试证据

- `smart_capture_engine.py` 收紧 OFF 完成条件并保持 ON 结果完成条件。
- `smart_capture_domain.py` 保持 terminal handoff ACK 为后处理，不启动 Prefetch。
- `smart_captures.py` 拦截终态迟到回调，避免写入新的候选或搜索任务。
- `smart_captures.py` 的批次快照同步只记录系统等待，完成条件由 Smart Capture Engine 统一判定。
- API 回归覆盖 OFF independent/linked、ON result、Manual Analysis、result summary。
- completion event 覆盖重复 event、旧 version、新 event_id 迟到事件及 parent 稳定性。
- 回归命令：4 个 API/service 测试文件，`99 passed`。
- 启动检查：后端 `/api/health` HTTP 200；前端 `/` HTTP 200；临时进程已停止。

## 3. Authority / Owner

- current owner：Smart Capture。
- Pipeline data owner：`smart_capture_id`。
- production execution authority：Smart Capture Engine。
- parent relation/outcome authority：`fj_workflow_children` 与持久化 child event；历史 Workflow 查询和 guard 保护的兼容入口保留。

## 4. 轮询与风险

- Task 10 未新增轮询设计，`任务01_轮询风险核对.md` 无需更新。
- 仍需真实 BOSS/Codex 环境手测 OFF linked 父推进及 ON 结果目标完成链路。
- 下一任务重点：在不改变 Smart Capture completion/event 语义的前提下继续共享配置表单与条件校验。
