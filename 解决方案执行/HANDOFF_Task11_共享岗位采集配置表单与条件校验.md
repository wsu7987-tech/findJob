# Task 11：共享岗位采集配置表单与条件校验交接

## 1. 核对结论

- BossCapture 与 TaskCockpitNew 共用 `SmartCaptureConfigForm`、`SmartCaptureExecutionConfig` 和同一套前端校验。
- `delivery_target_enabled=false` 时，Codex、建议投递策略、Recommend 目标不参与必填校验；Cockpit 提交也不会再额外要求建议投递策略。
- `delivery_target_enabled=true` 时，前端与后端共同校验建议投递策略、Codex 模型、推理强度、Recommend 目标及候选目标数关系。
- independent 与 linked 创建都把完整执行配置写入 `fj_smart_captures.execution_config_json`，snapshot 从 Smart Capture owner 回读。
- Cockpit 请求 schema 保留 `filters/pages/include_details/prefer_current_page`，linked snapshot 不丢采集参数。
- BossCapture 使用 independent Smart Capture 创建；Cockpit 保持 parent orchestration + linked child 创建。

## 2. 本轮修复

1. 修复 TaskCockpitNew OFF 模式提交时无条件要求建议投递策略的问题。
2. 修复 `DeepJobSearchConfig` 未声明采集参数导致 linked 配置被 Pydantic 丢弃的问题。
3. 增加 OFF 请求不发送 Codex/推荐字段的前端断言。
4. 增加 linked Smart Capture snapshot 对 filters、pages、include_details、prefer_current_page 的回归断言。

## 3. 配置 snapshot 示例

```json
{
  "smart_capture_id": "sc-001",
  "workflow_run_id": "run-001",
  "execution_config": {
    "search": {
      "filter_strategy_id": "filter-001",
      "keywords": ["Python"],
      "cities": ["上海"],
      "filters": {"experience": "104"},
      "pages": 2,
      "include_details": true,
      "prefer_current_page": false
    },
    "candidate_target_count": 12,
    "delivery_target": {
      "enabled": true,
      "recommendation_strategy_id": "recommend-001",
      "recommend_target": 4,
      "review_target": 2,
      "target_mode": "all"
    },
    "jd_detail_policy": {"include_details": true, "analyze_all_candidates": true, "batch_size": 3},
    "analysis": {
      "codex_model": "gpt-5.6-luna",
      "codex_reasoning_effort": "high",
      "guidance": "关注平台工程和自动化经验",
      "handoff": "manual",
      "after_analysis_batch": "wait_for_user",
      "batch_size": 3
    },
    "context_budget": 16000,
    "stop_policy": {"min_depth": 2, "scroll_batch_size": 4, "max_depth": 18, "low_yield_streak_limit": 5}
  }
}
```

OFF 模式只将 `delivery_target.enabled` 设为 `false`，其余配置结构保持一致，Codex 与推荐字段为空不会阻止岗位采集。

## 4. 测试与启动检查

- 后端配置 + Workflow API 回归：82 passed。
- 后端修复定向回归：5 passed。
- 前端共享配置测试：3 passed。
- 前端相关回归：9 passed；`fineJobWorkflow.test.ts` 3 个既有测试因测试环境缺少 `window` 失败。
- 前端类型检查：仅既有测试 fixture 类型错误，新增源码无类型错误。
- 后端 `/api/health`：HTTP 200。
- 前端 `/`：HTTP 200。
- Smart Capture `/api/fine-job/smart-captures/current`：HTTP 200。

## 5. 越界与轮询

- 未修改旧 `TaskCockpit.vue`、后续任务文档或 Smart Capture completion/event 语义。
- 没有发现 Task 11 新增定时轮询。Workflow 状态继续使用既有 SSE；`startPolling` 仅是兼容方法名，实际建立 `EventSource`。
- `任务01_轮询风险核对.md` 无需更新。

## 6. 遗留风险

- BossCapture independent 任务刷新后的 current 接管仍属于 Task 13。
- 尚未进行真实 BOSS/Codex 环境手测。
- 前端既有 Workflow fixture 的 `window` 测试环境问题仍待单独处理。
