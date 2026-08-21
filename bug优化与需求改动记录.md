# 岗位采集与投递建议改动同步

## 当前代码状态

- 已推送到 `origin/main`。
- 本轮相关提交：
  - `aef0e92 fix:岗位采集`
  - `af5dfb0 fix:历史采集`
- 当前工作区在推送后保持干净。

## 本轮修复的问题

### 1. 投递建议错误评估了不该处理的岗位

岗位采集页现在只评估：

- 用户在列表中勾选的岗位；
- 已经完成岗位详情采集的岗位；
- 尚未获取过投递建议的岗位。

未完成详情的岗位保持原状态，不会被投递建议接口处理；已经获取过建议的岗位不会再次出现在列表上方的“获取未评估岗位投递建议”范围内。

列表上方按钮改为和采集详情一致的勾选后执行模式，并显示待处理数量。没有选择策略、没有可评估岗位、任务执行中时，按钮会 disabled。

### 2. 投递建议结果没有写入详情

投递评估结果现在会同时写入：

- 当前采集任务内存中的岗位对象；
- 当前任务详情抽屉显示的数据；
- 历史采集数据库中的岗位记录；
- `payload_json` 兼容字段。

详情抽屉会显示评估结论、置信度、理由、风险和缺失信息。

### 3. 列表和详情页按钮状态不正确

当前岗位采集页：

- 已完成详情但没有投递建议：显示“获取投递详情”；
- 已完成详情且已有投递建议：列表只显示“查看详情”，避免从列表重复获取；
- 未完成详情：显示采集详情相关按钮；
- 详情页对已完成详情显示“获取投递建议”或“重新获取投递建议”。

历史采集页：

- 已完成详情且没有投递建议：列表显示“获取投递详情”；
- 已有投递建议：列表不显示重复获取按钮，但详情抽屉支持“重新获取投递建议”；
- 未完成详情：显示采集详情或重新采集详情；
- 详情采集和投递建议获取互相 disabled，避免并发覆盖。

### 4. 点击采集时全局错误提示误导

前端错误映射此前遇到 404/501 会直接显示：

> 当前后端暂未提供这个接口，请确认后端版本是否已更新。

现在如果后端返回了具体的 `error_message`，优先展示后端业务错误；只有没有具体错误信息时，才显示通用的接口不可用提示。这样可以区分“接口不存在”和“接口存在但任务/岗位状态不允许操作”。

## 历史采集同步内容

### 数据库

`fj_boss_jobs` 新增：

```text
delivery_evaluation_json TEXT
```

数据库初始化会自动添加缺失字段，并从旧记录的 `payload_json.delivery_evaluation` 回填，避免用户为了查看历史建议重新采集。

### 后端

新增历史岗位投递建议接口：

```text
POST /api/fine-job/boss-capture/history/{history_job_id}/delivery-evaluations
```

接口要求历史岗位详情已完成，使用选定的岗位建议投递策略执行评估，并返回更新后的历史岗位。

当前任务投递评估接口仍为：

```text
POST /api/fine-job/boss-capture/tasks/{task_id}/delivery-evaluations
```

请求体新增可选字段：

```json
{
  "recommendation_strategy_id": "策略 ID",
  "job_ids": ["只评估的岗位 ID"]
}
```

### 前端

历史采集页新增：

- 建议投递策略选择框；
- 列表获取投递建议按钮；
- 详情抽屉投递建议展示；
- 获取/重新获取投递建议按钮；
- 投递建议获取中的 loading 和 disabled 状态。

## “获取未评估岗位投递建议”和“AI 评估”的区别

“获取未评估岗位投递建议”描述的是执行范围：从当前已勾选岗位中筛出已完成详情且尚未评估的岗位，然后执行投递评估。

它不是独立的评估模型。实际使用规则由选中的“岗位建议投递策略”的 `evaluation_method` 决定：

- `rules`：只使用本地规则，不消耗 LLM Token；
- `llm`：使用 LLM 评估；
- `hybrid`：先执行规则，再对符合条件的岗位使用 LLM。

新建岗位建议投递策略默认是 `hybrid`，但如果用户选择的是“仅规则”策略，获取投递建议就不会调用 AI。

## 主要改动文件

### 桌面端

- `apps/desktop/src/renderer/pages/fine-job/BossCapture.vue`
- `apps/desktop/src/renderer/pages/fine-job/BossCaptureHistory.vue`
- `apps/desktop/src/renderer/services/api.ts`
- `apps/desktop/src/renderer/services/contract.ts`
- `apps/desktop/src/renderer/stores/fineJobBossCapture.ts`
- `apps/desktop/src/renderer/stores/fineJobBossHistory.ts`
- `apps/desktop/src/renderer/types.ts`

### 后端

- `backend/app/db.py`
- `backend/app/routers/fine_job/boss_capture.py`
- `backend/app/schemas/fine_job/boss_capture.py`
- `backend/app/services/fine_job/boss_capture_history.py`
- `backend/app/services/fine_job/boss_capture_tasks.py`

### 测试

- `backend/tests/api/test_fine_job_boss_capture_api.py`
- `backend/tests/services/test_boss_capture_tasks.py`
- `apps/desktop/src/renderer/services/contract.test.ts`

## 验证结果

- 后端 BOSS 采集相关测试：17 项通过。
- 前端 Vitest：27 个测试文件、87 个用例通过。
- 桌面端生产构建：通过。
- 构建仅有既存的 JavaScript chunk 体积提示，不影响构建结果。

## 后续联调重点

1. 在桌面端重新启动后端和 Electron，确认数据库迁移正常执行。
2. 采集一个岗位并完成详情采集。
3. 在列表勾选岗位，点击“获取未评估岗位投递建议”。
4. 确认当前任务详情抽屉和历史采集页都能看到同一份投递建议。
5. 刷新应用后确认历史建议仍然存在。
6. 使用规则策略和 hybrid 策略各验证一次，确认实际评估方式符合策略配置。

## 本次对话补充：策略管理与岗位列表排序

### 1. 策略管理标签输入修复

`StrategyManagement.vue` 原先使用了项目组件库不存在的 `tag-input`，且内联组件依赖运行时模板编译，导致页面无法正常显示。

已统一替换为 Element Plus 原生组件：

```text
el-select multiple filterable allow-create default-first-option clearable
```

现在支持输入内容后回车生成标签，并保留多选、删除和清空能力。

### 2. 策略管理布局与默认选中

岗位筛选策略和岗位建议投递策略已同步完成以下调整：

- 保存按钮移动到左侧策略列表区域；
- 左侧策略列表和右侧编辑表单分别滚动；
- 滚动区域最大高度为 `1000px`；
- 滚动区域设置明确的视口可用高度，避免继续由页面外层统一滚动；
- 滚动条宽度压缩为 `4px`；
- 页面加载后自动选中第一条已有策略；
- 没有策略时保留空白新建表单状态。

### 3. 岗位列表排序

`BossCapture.vue` 岗位列表新增表头排序：

- 采集；
- 岗位标题；
- 公司规模；
- 薪资；
- 经验；
- 招聘者活跃；
- 筛选结果。

筛选结果默认按以下顺序排列：

```text
通过 → 待确认 → 不通过
```

排序采用多级排序规则：最后点击的列为主排序，之前点击的列依次作为次级排序。例如先点击“采集”，再点击“筛选”，结果为“筛选”优先，相同筛选结果内再按“采集”排序。应用筛选策略或 AI 初筛后会恢复默认的筛选结果顺序。

公司规模、薪资、经验和招聘者活跃字段使用了专用排序规则，避免直接按文本排序造成明显的顺序错误。

### 4. 本次验证与推送

- `pnpm exec vue-tsc --noEmit`：通过；
- 本次改动已包含在提交 `8c614d7 feat:重构投递策略` 中；
- 已推送到 GitHub `origin/main`；
- 当前本地 `main` 与 `origin/main` 已同步，工作区干净。

### 5. 主要改动文件

- `apps/desktop/src/renderer/pages/fine-job/StrategyManagement.vue`
- `apps/desktop/src/renderer/pages/fine-job/BossCapture.vue`
