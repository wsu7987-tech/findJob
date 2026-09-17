# 任务 05：Pipeline Schema / Migration —— Smart Capture Owner 字段与索引

> v0.2.2.4 封版：本任务必须阅读 `E_状态_子任务_事件与实时传输契约.md`。本任务不是“给旧表加一列”任务，而是确保 independent Smart Capture 在没有 Workflow Run 的情况下可持久化完整 Pipeline。

## 已确认决策 / 本任务主要完成事项

本任务**只解决 schema/migration**，不切 repository 权威，不切 production Engine。

以 `C_Pipeline_Owner与历史兼容矩阵.md` 为准：新业务 owner 最终为 `smart_capture_id`；linked 可保留 `workflow_run_id` 作为父关联/历史键；independent 在 `workflow_run_id=null` 下也必须能持久化完整 Pipeline。

## 必查结构

至少：

- Search Combination / discovery / candidate/evaluation 相关表；
- Workflow task 中实际承担 JD/Analysis item 的记录；
- `fj_workflow_context_snapshots`；
- `fj_workflow_analysis_handoffs`；
- `fj_workflow_prefetch_batches/items`；
- `fj_workflow_candidate_reservations`；
- Codex session/handoff 关联字段；
- 与上述实体关联的 unique/index/FK。

## 逐表 owner 迁移清单（强制）

至少输出并测试以下映射：

| 当前表/能力 | 新业务 owner | linked parent/history link | independent 要求 |
|---|---|---|---|
| Search Combination | `smart_capture_id` | `workflow_run_id` 可保留为 parent/history link | `workflow_run_id=null` 可写读 |
| Job Discovery / Candidate | `smart_capture_id` | parent link 可选 | 不依赖 `workflow_task_id` 指向 Workflow |
| JD / Analysis task/item | Smart Capture domain task identity | parent link 可选 | 无隐藏 Workflow task 也可写读 |
| Context Snapshot | `smart_capture_id + channel` | 可通过 parent 查历史 | `smart_capture_id + channel` 唯一 |
| Analysis Handoff | `smart_capture_id + analysis_batch_id` | parent 仅导航/历史 | 主键/唯一键不能以 nullable Workflow ID 为唯一 owner |
| Prefetch Batch/Item | `smart_capture_id` | parent link 可选 | 完整 CRUD 不要求 Workflow ID |
| Candidate Reservation | `smart_capture_id` / owner context | parent link 可选 | active unique 语义不退化 |
| Evaluation Feedback | Smart Capture analysis/task identity | parent link 可选 | 不强制 FK 到 Workflow-only task |
| Codex session/handoff | Smart Capture analysis/handoff | parent 可选 | independent 可以 attach/claim/release |

如果现有 `fj_workflow_tasks` 同时承担 parent child、JD task 和 Analysis item 三种语义，必须拆分或增加明确的 domain task owner；不能仅仅把 `workflow_run_id` 改成 nullable。

不要机械改表名。可以保留历史表名，只要 owner 语义正确。

## Migration 要求

1. 原地升级，不要求清库。
2. migration 可重复/有版本保护。
3. 给每个新业务 owner 增加 `smart_capture_id`，新运行记录不得只写 Workflow ID。
4. 对仍有 `workflow_run_id NOT NULL`、Workflow-only FK、复合 PK/UNIQUE 的 SQLite 表执行安全 table rebuild 或等价迁移；只 `ALTER TABLE ADD COLUMN` 不算完成。
5. linked 历史数据能可靠回填则回填；不能可靠回填时保留 legacy row，不伪造错误 Smart Capture。
6. independent 记录允许 `workflow_run_id=null`，且不因间接 FK（例如 `workflow_task_id`）重新要求 Workflow。
7. unique/index 改为支持 Smart Capture owner，尤其 handoff/context/prefetch/reservation；不能让 nullable `workflow_run_id` 破坏唯一约束。
8. active reservation 唯一语义不退化；本轮**保持现有 active reservation 对 `job_id` 全局唯一**，不要自行新增跨 Smart Capture 重复 reservation 产品语义。completed 后处理与 current 任务冲突时应明确 conflict/复用 artifact，且不得回滚 completed lifecycle/parent。
9. 新建 Smart Capture 的完整 Execution Config 与状态约束同步迁移，不能只有搜索字段。
10. migration 不改变当前 production query authority；Task 06 才切 repository。

### SQLite 结构验收（强制）

Luna 必须用 `PRAGMA table_info`、`foreign_key_list`、`index_list`/`index_info` 和实际 insert/update 测试证明：

- 新表约束允许 independent `workflow_run_id=null`；
- owner、parent link、legacy row 三者含义没有混淆；
- context/handoff/prefetch/reservation 的唯一键在 owner 变化后仍正确；
- 旧数据库升级可重复执行；
- 外键开启时 migration 不丢数据、不产生 orphan；
- `fj_workflow_tasks` 或其替代 domain task 结构能支撑 independent discovery/analysis/feedback。

## 自动测试

- 空库新建；
- v0.2/旧 schema upgrade；
- migration 重跑；
- linked 可回填；
- 无法回填 legacy 安全保留；
- independent 可插入 context/prefetch/handoff/reservation 且 `workflow_run_id=null`；
- independent 可插入 discovery、JD/Analysis task/item、feedback、Codex session/handoff，且不创建隐藏 Workflow；
- unique/index 保持预期。

## 用户手测

无需 UI。要求 Luna 输出 migration SQL/测试证明以及“哪些 legacy row 无法回填”的处理策略。

## 高级模型复核

**强烈建议在 Task 06 后一起复核。**

## 与 ExecutionConfig 的边界

Task 02 已要求创建 Smart Capture 时完整 `SmartCaptureExecutionConfig` 归 Smart Capture owner（允许 `execution_config_json`）。本任务迁的是 Pipeline 业务表 owner/FK/index，不得把 ExecutionConfig 的 ownership 延迟到本任务才建立，也不得继续依赖 Workflow contract 作为 ON 模式隐藏配置源。
