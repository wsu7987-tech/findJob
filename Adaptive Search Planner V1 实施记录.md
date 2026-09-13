# Adaptive Search Planner V1 实施记录

## Search Combination schema

新增 `fj_workflow_search_combinations`，按 Run 持久化动态搜索组合。核心字段包括：

- 身份：`id`、`workflow_run_id`、`keyword`、`city`、`platform_filters_json`、`identity_json`
- 状态：`status`、`sequence`、`parent_combination_id`、`transition_action`、`transition_reason`、`selected_axis`、`evidence_json`
- 累计指标：`batch_count`、`pages_seen`、`jobs_seen`、`run_fresh_jobs`、`historical_duplicates`、`cooldown_excluded`、`strategy_pass`、`strategy_review`、`strategy_reject`、`qualified_fresh_jobs`、`candidate_jobs`、`novelty_yield`、`qualified_novelty_yield`、`duplicate_rate`、`low_novelty_streak`、`low_qualified_yield_streak`
- 生命周期：`started_at`、`completed_at`、`stop_reason`

组合 identity 由 `keyword + city + canonicalized platform_filters` 组成，同一 Run 内唯一。初始组合始终是 baseline `{}`，Planner 每次只按实际结果创建一个下一组合。

## 实际支持的平台 filter

Planner 直接复用 `boss_cdp_raw.py` 中的现有映射：

| 业务维度 | BOSS filter key | FineJob 策略字段 |
| --- | --- | --- |
| 公司规模 | `scale` | `company_scales` |
| 融资阶段 | `stage` | `company_stages` |
| 薪资 | `salary` | `monthly_salary_min`、`monthly_salary_max_at_least` |
| 经验 | `experience` | `experiences` |
| 学历 | `degree` | `degrees` |
| 公司行业 | `industry` | `company_industries` |

`job_types` 未映射为平台筛选，仍由 FineJob 本地策略判断。`daily_salary_min` 不能安全映射到月薪 bucket 时不生成 salary 平台筛选。月薪条件通过 salary bucket helper 转换为可能相交的粗粒度平台范围，最终资格仍由 FineJob Filter Strategy 判断。

## failure code schema

Filter Evaluation 在原有中文 `reasons` 之外，直接在规则判断位置写入 `failure_codes`：

`salary`、`experience`、`degree`、`company_scale`、`company_stage`、`company_industry`、`city`、`job_type`、`title`、`company`、`skill`、`boss_active_status`。

Planner 仅使用前六个可映射维度生成 BOSS filter；其余 code 继续用于统计和展示。历史重复、cooldown 与策略 reject 使用独立字段和计数，不互相写入 failure codes。

## Planner 决策规则

1. 当前组合持续产生 `qualified_fresh_jobs` 时继续滚动当前组合。
2. Fresh 很少或低 novelty streak 达到阈值时，统计当前 Scope 历史重复岗位的可映射字段分布，优先选择低覆盖或尚未探索的允许值，记录 `duplicate_pool_skew`、维度和 evidence。
3. Fresh 存在但 Qualified Fresh 为零，或 Qualified Fresh 产出低于阈值时，按 `failure_codes` 计数选择最高频且可映射的失败维度，生成 `ADD_FILTER` 或 `REPLACE_FILTER`。
4. 合格 Fresh 产出低于阈值时，进入低合格产出分支；组合过窄且无结果时允许 `REMOVE_FILTER`。
5. 每次组合操作保留 `ADD_FILTER`、`REMOVE_FILTER`、`REPLACE_FILTER`、`SWITCH_COMBINATION`，并通过 canonical identity 防止重复执行。
6. 达到组合安全上限或当前 Scope 没有新的合理组合时，才切换 Scope。

## 城市 / 关键词切换顺序

按 Run 创建时批准的列表遍历：

```text
keyword[0] + city[0]
keyword[0] + city[1]
...
keyword[1] + city[0]
...
```

当前 `keyword + city` 的平台组合耗尽后进入下一个批准城市；当前 keyword 的批准城市全部耗尽后进入下一个批准 keyword。新 Scope 从 baseline `{}` 开始，独立计算 depth、streak 和组合指标；全局 Candidate Pool、JD Prefetch、Analysis Batch 与 Codex handoff 保持原有边界。

## TaskCockpit 展示

仅增加“搜索策略状态”最小区域，展示当前 Scope、当前组合、Fresh、历史重复、Filter Reject、Qualified Fresh、切换原因和下一动作。

## targeted tests

- 后端完整 targeted 首轮：`69 passed`
- 规则修正后 Planner + Workflow API 复测：`44 passed`
- salary helper 清理后 Planner 复测：`9 passed`
- 前端 `TaskCockpit.test.ts`：`13 passed`
- 未运行全量测试。

## Git 状态

当前项目规则要求未经用户运行不得执行 git 操作，因此本轮未执行 `git` 命令，没有 commit hash。提交前检查结果为上述 targeted tests 全部通过，现有 `test.md` 未修改。

可由你在确认工作区内容后直接执行：

```powershell
git add backend/app/db.py backend/app/schemas/fine_job/workflow_runs.py backend/app/services/fine_job/adaptive_search_planner.py backend/app/services/fine_job/job_evaluation.py backend/app/services/fine_job/workflow_runs.py backend/app/services/fine_job/boss_capture_history.py backend/app/services/fine_job/boss_capture_tasks.py backend/app/services/fine_job/boss_scraper/service.py backend/tests/api/test_fine_job_workflow_runs_api.py backend/tests/services/test_adaptive_search_planner.py backend/tests/services/test_job_evaluation.py apps/desktop/src/renderer/pages/fine-job/TaskCockpit.vue apps/desktop/src/renderer/pages/fine-job/TaskCockpit.test.ts apps/desktop/src/renderer/types.ts apps/desktop/src/renderer/services/api.ts "Adaptive Search Planner V1 实施记录.md"
git commit -m "实现 Adaptive Search Planner V1"
git push
```
