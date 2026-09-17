# 任务 07：Smart Capture Engine —— Search / Candidate / BOSS 执行域

## 已确认决策 / 本任务主要完成事项

把 Smart Capture Pipeline 的前半段执行能力迁到 owner-neutral/Smart Capture service：Search Combination → BOSS Capture Batch → Candidate Pool。

本任务不迁 Codex transport，不做最终 Engine Cutover。

## 必须保护的现有能力

- 搜索组合生成与顺序；
- BOSS browser not running 卡点；
- capture batch 持久化与进度；
- candidate discovery / 去重；
- 已有筛选/策略行为；
- stop policy / stop_after_current_batch 等配置；
- app restart 后丢失进程的安全 interrupted；
- custom collection capacity 互斥。

## 实施要求

1. 执行入口以 `smart_capture_id` / owner context 为核心，不要求 `workflow_run_id`。
2. BOSS batch 必须持久化 `smart_capture_id`。
3. linked/independent 调用同一 Search/Candidate/BOSS service。
4. parent 不直接创建 BOSS batch；但 production authority 正式切换仍等 Task 09。
5. 对旧 Workflow Engine 中的 search/capture 逻辑优先抽取共享 service/adapter，不复制两套规则。
6. pause/interrupted capability 与 Task 03 状态机一致。

## 自动测试

- owner-neutral Search/Candidate/BOSS service；
- linked/independent 输入产生同类 batch/candidate 结果；
- independent 不需 Workflow Run；
- browser interruption；
- pause/resume；
- custom capacity conflict；
- 不产生双 batch。

## 用户手测

此任务可不做完整产品手测；只做低页数独立执行的开发态验证。正式 live 路径验收在 Task 09 后。

## 高级模型复核

一般不需要。
