# 任务 04：Pipeline Owner Seam / Characterization Tests / Cutover Guard

> v0.2.2.4 封版：Cutover Guard 还必须覆盖 `E_状态_子任务_事件与实时传输契约.md` 中列出的旧 capture-finished callback、resume+advance、App-level Workflow controller 和 history fallback。

## 目标

在不改变生产执行 authority 的前提下，把巨大 `workflow_runs.py` 里的岗位采集域能力识别成可被 Smart Capture owner 调用的 service seam，并用 characterization tests 固定现有正确行为。

## 已确认归属

Smart Capture Domain：Search、Candidate、JD、Analysis、Codex handoff、Prefetch、Reservation、Context、Recommend/Review progress、Manual Analysis。

Parent Workflow：child orchestration、child status/result summary、父级 pause/cancel/等待决策。

## 实施要求

1. 建立最小 owner/execution context，例如：

```text
smart_capture_id
optional workflow_run_id       # parent/history only
config/contract
source
```

2. 抽取/包装 repository/service seam，让后续函数不必把 `workflow_run_id` 当唯一业务 identity。
3. 为以下行为建立 characterization tests：
   - Candidate/搜索组合推进；
   - JD batch；
   - Analysis item/batch；
   - Handoff claim/ACK/release/retry；
   - Analysis N + Prefetch N+1；
   - Reservation 唯一性；
   - Context snapshot/budget；
   - Manual Analysis。
4. 形成现有 API/前端/MCP 调用链清单，与 `D_执行权切换与API调用链矩阵.md` 对齐。
5. 增加 Cutover Guard：在 Task 09 之前，测试/配置保证 production path 只有旧唯一 authority；新 service seam 可单测/集成测试，但不能对同一 live child 双启动。
6. Guard 必须能侦测旧 Workflow live callback 被调用；在 Cutover 后该调用必须失败、委托 Smart Capture 或被明确标记为 history-only，不能静默启动旧 Engine。
7. 本任务只固定最终 Smart Capture snapshot contract 与“不允许 Workflow-only identity”的 guard/characterization；**不得在 Task 04 提前实现 Task 08A 的 snapshot/polling Domain API**。child event idempotency 仍需 characterization。

## 禁止事项

- 不改 DB owner/schema；
- 不改变 OFF/ON completion；
- 不整体复制 `workflow_runs.py` 到 `smart_captures.py`；
- 不正式切 production Engine authority；
- 不删旧 Workflow API tests。

## 测试

现有 workflow API tests 继续通过；新增 owner resolver/context、旧 callback guard、“不会双启动”、**snapshot contract/identity guard（不是完整 polling 实现）** 和重复 child event 不二次推进的 characterization test。

## 高级模型复核

一般不需要；若 Luna 提议一次性复制/重写大文件，立即停止并改为小 seam。
