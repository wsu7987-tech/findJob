# HANDOFF Task01：Smart Capture 基础设施与容量互斥

执行日期：2026-09-17；启动修复补充：2026-09-18

## 1. 任务边界

本任务只处理 Smart Capture 基础设施、current slot、Smart/custom collection capacity 和 restart recovery。

已读取并遵循：Task00 handoff、A/B/C/D/E 契约、Task01 任务说明及窗口通用提示词。没有迁移 Pipeline owner，没有创建通用 parent-child relation/event，没有切换 production execution authority，没有重构 BossCapture UI，也没有执行 Task02 及后续任务。

## 2. 验收结论

- Task01 专项验收条目已通过；项目全量测试仍受既有基线问题和当前环境阻塞，详见第 6、7 节。
- Smart Capture router 已注册，`/api/fine-job/smart-captures/current`、`/{capture_id}`、`/{capture_id}/start`、`pause`、`resume`、`retry`、`stop` 可用。
- Smart Capture create 在同一数据库写事务中占用 current；`pending`、`paused`、`waiting_for_user`、`interrupted` 等非终态持续占槽。
- `completed`、`stopped`、`failed` 为终态，终态后允许创建下一条 Smart Capture；`failed` 不允许 retry。
- Smart 与 custom collection 通过统一 capacity helper 双向互斥，覆盖 BossCapture API、Codex tool、续采、选中详情和历史详情入口；Smart/Workflow 采集保持 `capture_source='smart'`。
- 应用启动时执行 recovery；丢失执行器的 `running`/`pausing` 收敛为 `interrupted`，current identity 不按更新时间漂移，不产生幽灵 running。
- Smart snapshot 输出 `waiting_reason`、`control_cause`、整数 `state_version`、capabilities、progress、result_summary；可观察内容未变化时不增加 `state_version`。
- 修复中断迁移遗留 `fj_smart_captures_legacy_status*` 时的可重复启动：合并历史记录、保留 current 指针、恢复批次外键后清理临时表。
- 修复 Electron 35.7.5 不支持 `electron/main` 与 `electron/renderer` 子路径导致的桌面启动失败。
- 修复孤立 `pending` Smart Capture 阻塞所有采集入口：活动任务查询现在只读取 `fj_smart_capture_current` 指向的任务。
- 修复岗位采集页停止 Smart Workflow 后的前端状态循环：终态 `cancelled` 可重新开始，停止按钮正确消失，并允许切换到自定义采集页。

## 3. Capacity helper 与入口接线

统一实现位于 `backend/app/services/fine_job/collection_capacity.py`：

- `assert_collection_start_allowed()`：数据库写事务内检查 Smart/custom 互斥。
- `start_custom_collection()`：custom 启动前原子预占，启动失败或终态任务释放。
- `start_custom_collection_phase()`：已有 custom 任务的续采/详情阶段复用同一容量；Smart 任务保持 Smart 路径。
- `recover_collection_capacity()`：应用重启时收敛遗留 custom 执行占用。

已接线入口：

1. `POST /api/fine-job/boss-capture/capture`
2. `finejob.start_job_capture`
3. `POST /api/fine-job/boss-capture/tasks/{task_id}/continue`
4. `finejob.continue_job_capture`
5. `POST /api/fine-job/boss-capture/tasks/{task_id}/details`
6. `finejob.collect_job_details`
7. `POST /api/fine-job/boss-capture/history/{history_job_id}/details`
8. `finejob.collect_job_detail`

## 4. DB/schema 状态变化

- `fj_boss_capture_batches` 增加 `capture_source`，取值为 `smart` 或 `custom`。
- `fj_smart_captures` 增加 `waiting_reason`、`control_cause`、`state_version`、`progress_json`、`result_summary_json`，并将 `waiting_for_user` 纳入状态约束。
- legacy `waiting_next_batch` 搭配人工等待原因只在迁移/读取时映射为 `waiting_for_user`，新写入使用 canonical status。
- 新增单槽表 `fj_collection_execution_capacity`，持久化 custom 执行容量预占。
- 启动时执行兼容迁移、Smart 表约束修复、batch 外键修复及索引创建。

## 5. 修改文件

- `backend/app/services/fine_job/collection_capacity.py`
- `backend/app/db.py`
- `backend/app/services/fine_job/boss_capture_history.py`
- `backend/app/services/fine_job/boss_capture_tasks.py`
- `backend/app/services/fine_job/smart_captures.py`
- `backend/app/routers/fine_job/smart_captures.py`
- `backend/app/routers/fine_job/boss_capture.py`
- `backend/app/services/fine_job/workflow_runs.py`
- `backend/app/services/fine_job/codex_tools.py`
- `backend/app/main.py`
- `apps/desktop/src/renderer/types.ts`
- `apps/desktop/src/renderer/services/api.ts`
- `backend/tests/api/test_fine_job_smart_captures_api.py`
- `backend/tests/test_db.py`
- `backend/tests/api/test_fine_job_smart_captures_api.py`
- `backend/app/services/fine_job/collection_capacity.py`
- `backend/app/services/fine_job/smart_captures.py`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/windows.ts`
- `apps/desktop/electron/tray.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/scripts/build-electron.mjs`
- `apps/desktop/scripts/dev.mjs`
- `apps/desktop/src/renderer/pages/fine-job/BossCapture.vue`

## 6. 自动测试

Task01 专项与相关回归：

```text
pytest -q backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/test_db.py backend/tests/api/test_fine_job_boss_capture_api.py
35 passed in 42.06s

pytest -q backend/tests/api/test_fine_job_codex_api.py
8 passed, 1 failed in 13.05s
```

后端全量：

```text
pytest -q
530 passed, 21 failed, 24 errors in 517.92s
```

桌面端与 BOSS 扩展全量：

```text
pnpm --filter fine-job-desktop test:run
Vitest 配置加载阶段失败：Error: spawn EPERM

pnpm --dir boss-executor-extension test
Vitest 配置加载阶段失败：Error: spawn EPERM
```

专项通过项覆盖 router 非 404、create/current、非终态占槽、终态放行、Smart/custom 双向互斥、restart recovery、failed 不可 retry、current identity 不漂移及 `state_version` 无变化不递增。

启动与迁移回归：

```text
pytest -q backend/tests/test_db.py backend/tests/api/test_fine_job_smart_captures_api.py
22 passed in 27.38s

scripts/start-backend.ps1 -NoReload -Port 8010
启动完成；GET http://127.0.0.1:8010/api/health 返回 200。

pnpm --filter fine-job-desktop dev
Vite 启动成功，Electron 入口不再出现 electron/main 模块错误。

pnpm --filter fine-job-desktop exec vite -- --host 127.0.0.1 --port 5173
Vite ready；GET http://127.0.0.1:5173/ 返回 200 text/html。

pnpm exec node scripts/build-electron.mjs（工作目录 apps/desktop）
通过。

孤立 pending 回归：

```text
pytest -q backend/tests/api/test_fine_job_smart_captures_api.py
14 passed in 18.61s

当前本地数据库 GET /api/fine-job/workflow-runs/collection-active
返回 {"active_task": null}；current Smart Capture 为 completed。
```

前端停止状态回归：

```text
pnpm exec vite build（工作目录 apps/desktop）
通过；2270 modules transformed，exit 0。
```
```

## 7. 遗留问题与边界

- 当前环境缺少 `mcp` Python 模块，MCP server 注册测试无法导入。
- Task00 已知 Workflow contract/resume 基线失败仍存在，本任务未修改 Workflow owner 或相关产品行为。
- `test_boss_capture_tasks.py` 中两个详情测试替身未接受现有 `should_stop` 参数，仍表现为既有 fixture compatibility failure；全量运行时另有多项 `tmp_path`/临时目录权限错误。
- 前端桌面端与 BOSS 扩展 Vitest 均因 `spawn EPERM` 未能进入收集阶段。
- 前端生产构建仍被既有 TypeScript 错误阻断，涉及 `BossCapture.vue`、TaskCockpit/聊天策略/Workflow 类型测试与 `workflowCodexHandoff.ts`；本次启动修复未触碰这些业务类型。
- 8000 端口复测期间曾出现 `WinError 10048` 端口占用，改用 8010 完成启动与健康检查；需在本机正常运行时确认 8000 端口无其他服务占用。
- 历史孤立 `pending` 记录保留在 Smart Capture 历史中，不再参与活动槽位判断；后续可在历史记录清理需求中单独处理。
- 全量后端另有既有 PDF 清洗、Web Draft、聊天及 Codex 执行测试失败，未触碰其产品逻辑。
- 旧 batch 缺失 `capture_source` 时采用 custom 兼容推断；遗留 queued/running 记录可能保守占用 custom capacity，需后续数据清理窗口确认。

## 8. 下一窗口接续

下一窗口先复核本 handoff 的测试结果和上述遗留项，不改变 Task01 已确定的 owner 与 capacity 语义。后续任务按总览顺序执行；Task02 之前不引入通用 parent-child relation/event，不切换 Pipeline owner 或 production execution authority。
