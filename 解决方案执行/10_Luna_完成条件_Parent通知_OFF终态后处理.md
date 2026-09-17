# 任务 10：Smart Capture 完成条件 / Parent Completion Event / OFF completed 终态后处理

> v0.2.2.4 封版：completion event、状态版本和后处理边界以 `E_状态_子任务_事件与实时传输契约.md` 为准。

## 已确认决策 / 本任务主要完成事项

### delivery target OFF

```text
candidate_count >= candidate_target_count
→ Smart Capture completed
→ 写 result_summary
→ linked parent 只消费一次 child completed event 并进入下一 child/完成
```

不再进入 `candidate_target_reached_waiting_analysis` 作为自动生命周期卡点。

### delivery target ON

Candidate 达标只是供给阶段；继续 JD/Codex/Prefetch，Recommend/Review 合约满足后才 completed。

## OFF completed 终态不可回滚（v0.2.2 强化）

现有人工选择岗位 / Manual Analysis 能力必须保留，但：

- completed Smart Capture lifecycle **保持 completed**；
- 后续人工分析不得把 capture 改回 running/waiting；
- 不重新创建“父必须等待 Codex”的关系；
- parent 已推进后不回滚、不二次阻塞；
- 不重复发 child-completed 事件；
- 可以在 completed capture 上产生后处理 Analysis artifact/result；必须优先使用以 `smart_capture_id` 为 identity 的 Smart Capture 后处理 API；
- 如果暂时保留 legacy compatibility，必须是显式 adapter，并有测试证明不会写 Workflow lifecycle、不会清除 parent control state、不会重新占用 current、不会发 child-completed；不能直接复用会把 Run 改回 `waiting_codex` 的旧函数。

## Parent completion 契约

父只消费 child outcome/result summary，例如：

```text
child_status=completed
result_summary={candidate_count, recommend_count?, review_count?, ...}
```

父不读取 Prefetch/Handoff/Analysis item 细节判断 child 是否完成。

仅 **linked** Smart Capture 进入 Parent completion event 路径。Parent 只消费带 `event_id/transition_id/child_relation_id/child_type/child_ref/state_version` 的 child outcome；以 `(child_relation_id,event_id)` 幂等，并只接受 `event.state_version > relation.child_state_version`。接受后原子写入 `child_state_version`，parent relation 自己的 `state_version` 再独立递增。independent Smart Capture completed 只持久化自身 `result_summary`/terminal snapshot，不创建伪 relation 或 parent outcome event。

## 自动测试

1. OFF linked：candidate target → child completed → parent 继续；
2. OFF independent：completed；
3. OFF completed 后 Manual Analysis 仍可用，但 child 仍 completed、parent 不回滚；
4. ON candidate 达标不 completed；
5. ON 结果目标达标才 completed；
6. linked completion event 幂等 + `child_state_version` 乱序 guard；
7. completed 后自动 Engine 不再启动新 capture/prefetch；
8. result summary 稳定可历史读取。
9. 重复、迟到、重启后重新投递的 completion event（包括新 event_id + 旧 state_version）不会二次推进 parent；
10. completed OFF Manual Analysis 使用 Smart Capture identity，且不会调用旧 Workflow lifecycle mutation。

## 用户手测（强制）

- OFF linked candidate target 2～3：父自动推进；随后人工选历史候选送 Codex，确认父状态不回退；
- ON：candidate 达标后继续分析，结果目标满足才完成。

## 高级模型复核

建议与 Task 09 一起复核完成边界。Task 09 已负责 Cutover 前最小 completion/outcome 接线，本任务负责完整 OFF/ON completion policy、OFF completed 后处理和终态回归。
