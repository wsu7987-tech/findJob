# HANDOFF_Task08B：Codex Renderer Transport / Workspace / Skill

## A. 完成结论

08B 已完成 Smart Capture 的 Renderer、Electron transport、Workspace 和 Skill 接线；本窗口仍处于 Task09 Cutover 前，未切换生产 Engine authority。

- 新 Smart Capture handoff 以 `smart_capture_id + analysis_batch_id + handoff_attempt_id` 为业务 identity；`workflow_run_id` 为可选 linked 父级导航和历史关联。
- independent Smart Capture 可 attach Codex session、claim handoff、写入 Prompt、ACK 后通过 Smart Capture MCP 保存结果，全程不要求 Workflow Run。
- linked Smart Capture 与 independent 共用相同的 Smart Capture transport；历史 Workflow handoff 继续由 legacy transport 支持。
- App 启动 Smart Capture controller，只查询 current Smart Capture 和 Analysis snapshot；不调用 `getLatestFineJobWorkflowRun()` 或 `advanceFineJobWorkflowRun()` 推进 Smart Capture。

## B. Renderer / Transport 接线

- `api.ts`：提供 Smart Capture detail、context、analysis item、session、claim、prompt-written、ACK、release/retry 和 save API。
- `workflowCodexHandoff.ts`：新增 Smart Capture handoff 与重试/再次提交；并发键使用 `smart_capture_id`。
- `fineJobWorkflowCodexController.ts`：保留 legacy controller，新增仅以 current Smart Capture 驱动的 controller。
- `codex-session.ts`、`codex-ipc.ts`、`preload.ts`：新增 Smart Capture 专属 IPC/PTY composer 通道；返回 `smartCaptureSessionMode`，不再以 Workflow session mode 表示 Smart Capture 会话。
- `App.vue`：只启动 Smart Capture auto controller。

## C. Workspace / Terminal / Skill

- `CodexWorkspace.vue` 的 Smart Capture 入口读取 `smart_capture_id`；`parent_workflow_run_id` 仅展示导航信息。
- `CodexTerminalPanel.vue` 的 Smart Capture 返回入口固定到岗位采集页；历史 Workflow 仍返回对应驾驶舱。
- BossCapture 的手工 batch、交接、再提交、重试优先使用 Smart Capture API；旧 Workflow 路径保留给 legacy/history。
- `.agents/skills/finejob/SKILL.md` 与运行时资源副本均加入 Smart Capture Analysis 流程：ACK → context/items → item context → Smart Capture save；independent 不要求 Workflow Run。
- CodexWorkspace 以路由任务类型区分 Smart Capture 和 inspected Workflow；Smart Capture route 不会进入 Workflow handoff 分支。

## D. 验证结果

- `npm run test:run -- electron/codex-session.test.ts electron/codex-ipc.test.ts src/renderer/services/workflowCodexHandoff.test.ts src/renderer/services/fineJobSmartCaptureCodexController.test.ts`：24 passed。
- `python -m pytest backend/tests/api/test_fine_job_smart_capture_domain_api.py`：3 passed。
- `npx vite build`：通过；仅有既有 chunk 大小警告。
- `vue-tsc --noEmit`：未通过，剩余错误位于既有 BossCapture 空值、TaskCockpit/fineJobChatPolicy 测试夹具和 legacy controller fixture；08B Smart Capture transport 的类型错误已清除。
- `CodexWorkspace.test.ts` 当前为 4 passed、7 failed；失败来自旧 Workflow 测试 mock 缺少 `startPolling` 和终端挂载夹具，不属于 Smart Capture handoff 逻辑。
- 组合后端 API 回归在当前命令窗口于 30 秒输出截断，未将其计为通过。

## E. Authority / Owner 状态

- current Smart Capture：后端 `/fine-job/smart-captures/current`。
- Codex/Analysis transport identity：Smart Capture Domain；linked 仅携带 parent/history link。
- production execution authority：仍为 Task09 前既有 Workflow Engine，Smart Capture transport 没有启动新的 Engine。
- 历史兼容：Workflow API、legacy controller、Workflow Workspace 入口与 Workflow MCP tools 均保留。

## F. 下一窗口风险

1. Task09 切换前必须先完成 linked child outcome 最小闭环，再将生产执行权交给 Smart Capture Engine。
2. Task09 后不得由 legacy Workflow controller、`advance` 或找不到 Smart Capture 的回退路径启动 live Smart Capture 单元。
3. 需要清理全量 Vue 测试夹具和类型错误，之后再将全量桌面测试作为可靠 gate。
4. 真实桌面端 Codex 交互尚未作为本窗口验收项执行；已覆盖 IPC/PTY 专项行为。
