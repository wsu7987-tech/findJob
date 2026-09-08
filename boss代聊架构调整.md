su，复核完成。未修改代码、未运行测试、未访问真实 BOSS，也未重新分析 MQTT/Techwolf 协议。

## CURRENT_SCHEDULER

当前存在两套独立队列，并非统一 Scheduler。

| 通道 | 数据表 | 实际 claim | 排序 | priority | 页面策略 |
|---|---|---|---|---|---|
| greeting | `fj_automation_actions` | 先选任务并打开页面，页面匹配后才锁定 | `created_at ASC, id ASC` | 无 | 每项打开岗位页，成功后默认关闭 |
| chat_message / resume_send | `fj_chat_send_actions` | API 直接更新为 `leased` | `created_at ASC` | 无 | 复用账号的聊天领导标签页，不识别当前 HR 会话 |

### greeting 调用链

1. 用户批准待确认项：

   [routers/fine_job/workflow.py](</D:/agent/fine-job/backend/app/routers/fine_job/workflow.py:67>)  
   `approve_fine_job_review_item()` → `approve_review_item()` → `_enqueue_action()`。

2. Action 写入：

   [workflow.py](</D:/agent/fine-job/backend/app/services/fine_job/workflow.py:622>)  
   `_enqueue_action()` 在 679 行写入 `fj_automation_actions`，状态为 `queued`。

3. 队列推送：

   [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:1146>)  
   `_send_queue()` → WebSocket `task_queue`。

4. 扩展请求打开下一任务页：

   [client.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/client.ts:194>)  
   `handleControlMessage()` 收到 `task_queue` 后调用 `requestTaskPage()`。

5. 后端选择任务：

   [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:564>)  
   `open_task_page()` 使用：

   ```sql
   WHERE status = 'queued'
   ORDER BY created_at ASC, id ASC
   LIMIT 1
   ```

   此时只选中并打开页面，尚未真正 claim。

6. 页面身份读取：

   [client.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/client.ts:620>)  
   `requestCurrentPageProbe()` 选择活动 BOSS 标签页，发送 `BOSS_PAGE_PROBE`。

   [entrypoints/boss/index.ts](</D:/agent/fine-job/boss-executor-extension/src/entrypoints/boss/index.ts:43>)  
   Main World 调用 `readBossPageIdentity()`。

   [read-only-probe.ts](</D:/agent/fine-job/boss-executor-extension/src/platform/boss/read-only-probe.ts:156>)  
   通过 pathname、Vue 岗位列表/详情、详情页 `_jobInfo` 和沟通按钮识别 `encryptJobId`、页面类型、登录与沟通状态。

7. 页面与任务匹配：

   [client.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/client.ts:511>)  
   `reportBossPageIdentity()` 只匹配：

   - `status === queued`
   - `encrypt_job_id` 与页面一致
   - `currentPageTaskId` 为空或等于该任务 ID

8. 真正锁定：

   [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:646>)  
   `match_task()` 使用 `BEGIN IMMEDIATE`，确认不存在其他运行任务，再将目标改为 `running/leased`。

9. dispatch 与完成：

   [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:714>)  
   `mark_task_dispatch_started()`。

   [default-greeting.ts](</D:/agent/fine-job/boss-executor-extension/src/platform/boss/default-greeting.ts:14>)  
   发送前再次检查页面身份、登录状态、岗位 ID、`contacted === false`。

   [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:1228>)  
   完成后默认关闭页面；`close_page_after_completion` 对普通 greeting 默认是 `true`，见 [序列化逻辑](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:1633>)。

10. 下一项：

    [client.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/client.ts:844>)  
    状态同步完成后进入 task cooldown，再次请求后端打开下一任务页。

页面打开失败会把 `created_at` 更新为当前时间，实际移动到队尾；页面匹配失败则保留原 `created_at`。逻辑位于 [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:1312>) 和 [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:1374>)。

### chat_message / resume_send 调用链

1. 回复确认：

   [boss_chat.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_chat.py:2158>)  
   `confirm_reply()` 在确认时检查当前 `based_on_message_id` 和 `session_version`，随后创建 `fj_chat_send_actions`。

2. 简历发送：

   [boss_chat.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_chat.py:2265>)  
   `_create_resume_action()` 检查 session、身份、发送开关，以及最近附件列表中的 ID/filename。

3. 领导标签页：

   [chat-coordinator.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/chat-coordinator.ts:184>)  
   仅从 `/web/geek/chat` 标签页选举；可见标签页优先，其次 `tabId`。

4. claim：

   [chat-coordinator.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/chat-coordinator.ts:309>)  
   `claimAndDispatch()` → `claimChatSendAction()`。

   [boss_chat.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_chat.py:2340>)  
   `claim_send_action()` 的真实 SQL 为：

   ```sql
   WHERE s.account_uid = ?
     AND (a.operation_kind = 'resume_list' OR send_enabled = 1)
     AND (
       a.status = 'queued'
       OR (a.status = 'leased' AND a.lease_expires_at <= now)
     )
   ORDER BY a.created_at ASC
   LIMIT 1
   ```

   没有 `id` tiebreak；相同 `created_at` 时顺序未被 SQL 保证。

5. dispatch：

   [boss_chat.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_chat.py:2405>)  
   `mark_dispatch_started()` 复核 send_enabled、session active、身份字段和领导页租约。

   [sender.ts](</D:/agent/fine-job/boss-executor-extension/src/platform/boss/chat/sender.ts:115>)  
   `send()` 复核账号、目标身份、文本/简历字段，并在发送边界前两次读取 `send_enabled`。

6. 串行领取：

   `activeActions` 以 `account_uid` 为 key，因此同一账号一次只有一个扩展内活动动作。结果回传或下一次 5 秒心跳会继续 claim，同一个聊天领导页可以持续处理不同 HR 的动作。

### 现状结论

- `fj_automation_actions`、`fj_chat_send_actions` 都没有调度 priority 字段。表定义见 [db.py](</D:/agent/fine-job/backend/app/db.py:1203>)、[db.py](</D:/agent/fine-job/backend/app/db.py:1438>)。
- `job_action_center.py` 的 `priority_tier` 和 `fj_chat_attention_states.priority` 属于业务行动展示，不参与 executor claim，见 [job_action_center.py](</D:/agent/fine-job/backend/app/services/fine_job/job_action_center.py:16>)。
- greeting 是 `created_at + id` FIFO；页面打开失败会移到队尾。
- chat/resume 是仅 `created_at` FIFO。
- 后端 executor 状态没有当前页面字段，仅保存连接、队列、风险和冷却状态，见 [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:1664>)。
- greeting 当前页面由扩展临时读取并与指定 `currentPageTaskId` 匹配。
- chat 只判断“这是当前账号的聊天领导页”，不判断该页当前打开哪个 HR。
- greeting 不会持续领取同岗位页面任务。
- greeting 的真实行为是“每项打开页面 → 执行 → 默认关闭 → 冷却 → 再打开下一页”。
- chat 会在同一聊天页持续领取，但属于账号级聊天页复用，并非具体会话 page affinity。
- 同一 session 当前依靠账号级单活动动作和创建时间获得近似顺序；数据库没有 session predecessor 条件，因此尚未形成强顺序保证。
- 实时 inbound text 会取消该 session 尚未 dispatch 的旧动作并把旧草稿标为 stale，见 [boss_chat.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_chat.py:841>)。它将所有新文本视为失效条件，没有执行相关/无关/平台事件分类；历史刷新也没有同等的执行前失效处理。

## MESSAGE_STATE_MODEL

保留 `fj_chat_messages` 作为不可变 raw message 主记录，完整保存平台 ID、direction、原始 type/content、时间和 `raw_meta_json`。现有 `_history_message_content()` 已经会生成“附件状态更新”等摘要，因此迁移后应另外保存真正的 `raw_content/raw_body_json`，见 [boss_chat.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_chat.py:531>)。

新增两个派生层：

### `fj_chat_message_semantics`

- `raw_message_id`，唯一 FK。
- `semantic_type`。
- `display_text`。
- `reply_required`。
- `classifier_version`。
- `semantic_context_json`：匹配的 resume action、filename、规则和方向上下文。
- `derived_at`。

### `fj_chat_message_states`

- `raw_message_id`，唯一 FK。
- `read_state`: `unknown | unread | read | not_applicable`。
- `read_subject`: `candidate | recruiter | not_applicable`。
- `reply_state`: `not_required | unanswered | reply_planned | queued | replied`。
- `read_at/reply_planned_at/replied_at/updated_at`。

API 返回时将 raw、semantic、state 合成为一个 message DTO。

语义规则按“结构字段 → direction → 同 session Action 关联 → 精确内容”执行：

| 条件 | semantic_type | display_text | reply_required |
|---|---|---|---|
| outbound、结构类型为附件状态，且 clientMid/时间窗/session 能关联 `resume_send` | `resume_sent` | 已发送附件简历 | false |
| inbound 文件名与同 session 已发送 resume filename 精确规范化匹配 | `resume_read_receipt` | HR 已查看简历：`filename` | false |
| inbound、结构类型为附件状态，且存在关联 resume action | `resume_viewed` | HR 已查看附件简历 | false |
| 系统消息精确匹配“对方已同意，您的附件简历已发送给对方”，且存在 resume 上下文 | `resume_received_confirmation` | 对方已接收附件简历 | false |
| 结构类型/已知卡片规则匹配“你与该职位竞争者PK情况” | `platform_ad` | 职位竞争情况推广 | false |
| 普通 HR 文本 | `recruiter_text` | 原始文本 | true |
| 无法可靠分类的平台消息 | `platform_event_unknown` | 保留原始展示文本 | false |

缺少 resume action 上下文时，“附件状态更新”和 `xx.pdf` 保持通用平台事件，不推断成简历已读。这样不会产生全局字符串替换。

## ACTION_STATE_MODEL

新增统一权威表 `fj_actions`，旧两张表在迁移期保留来源映射。

核心字段：

- 身份：`id`、`action_type`、`account_uid`、`job_id`、`session_id`。
- 页面：`target_page_kind`、`target_page_key`、`target_context_key`。
- 顺序：`session_sequence`、`revision_no`、`supersedes_action_id`。
- 业务：`priority`、`priority_source`、`authorization_mode`。
- 内容：`text`、`encrypt_resume_id`、`resume_filename`、`payload_json`。
- 聊天基线：`base_raw_message_id`、`base_message_mid`、`base_conversation_revision`、`base_cursor`、`planned_at`。
- 排队：`status`、`available_at`、`waiting_reason_code/detail/since_at`。
- 租约：`lease_owner`、`lease_expires_at`、`execution_epoch`、`attempt_count`。
- preflight：`preflight_state`、`preflight_observed_revision`、`preflight_completed_at`、`preflight_reason_code`。
- dispatch：`dispatch_token`、`dispatch_deadline_at`、`dispatched_at`。
- 结果：`canonical_status`、`outcome`、`status_code`、`completed_at`。
- 审计：`created_at`、`updated_at`、`source_table/source_id`。

状态流：

```text
awaiting_confirmation
  → queued
  → claimed
  → preflighting
  → dispatching
  → accepted
  → succeeded
```

终态/分支：

- `superseded`：已有替代 action。
- `stale`：上下文失效且未产生替代 action。
- `cancelled`
- `failed`
- `blocked`
- `unknown`

`unknown` 必须阻断同 session 后续动作，直到 outbound observation 或人工处理确定结果。

新增 `fj_action_message_links`：

- `action_id`
- `raw_message_id`
- `role`: `trigger | covered | new_context | outbound_result`
- `ordinal`

同一 session 的活跃 action 使用 `(session_id, session_sequence)` 唯一约束。替代 action 继承原 `session_sequence`、增加 `revision_no`；新独立消息 B 使用下一 sequence。

reply 状态变化：

- 新 inbound 且 `reply_required=true`：`unanswered`。
- 草稿/action 已规划并建立 coverage：`reply_planned`。
- 用户确认或预授权 Action 成功入队：`queued`。
- transport accepted、dispatching、unknown：仍未确认 `replied`，保留 `queued`，通过 action detail 显示发送中/待核对。
- 观察到与 action 精确关联的真实 outbound：`replied`。
- action 失败、取消或失效且无 replacement：恢复 `unanswered`。
- semantic reclassification 为无需回复：`not_required`。

## PREFLIGHT_MODEL

统一协议：

```text
claim → preflight snapshot → backend decision → dispatch-started → platform action
```

claim 只取得租约。Executor 获取最新页面/会话快照后调用 preflight API；后端负责持久化 raw message、生成 semantic、比较基线并返回短期 `dispatch_token`。token 到期或 revision 再变化时重新 preflight。

| Action | preflight policy |
|---|---|
| `greeting` | 复核授权、岗位仍有效、没有已成功/结果未知的同岗位 greeting；打开目标岗位页；复核账号、登录、encryptJobId、securityId 和 `contacted=false`。已沟通则 terminal `superseded/already_contacted`。 |
| `chat_message` | 强制抓取该 HR 最新 delta；入库 raw/semantic；比较 base mid/revision/cursor；复核 session active、文本版本与授权。分类不确定时禁止 dispatch。 |
| `resume_send` | 刷新最新聊天 delta；复核 resume 请求仍有效、附件 ID/filename 仍存在、同 session 没有成功或 unknown 的重复发送；相关消息表示撤回/拒绝时 stale，平台确认事件可直接完成或取消重复动作。 |

Chat delta 决策：

1. 无新消息：原 action 通过。
2. 新消息与 A 上下文相关：A → `superseded`；创建 A′，覆盖 A+B 的 inbound 集合，继承 A 的 session sequence。
3. 新消息与 A 无关但需要回复：A 保留；为 B 创建下一 sequence 的 action，A 完成后才能 claim B。
4. 平台事件、广告、无需回复：更新 semantic、read/reply/job activity，A 继续执行。

如果 A 是人工确认内容，A′ 的文本发生变化后回到 `awaiting_confirmation`；原确认不自动授权新文本。

运行条件与 freshness 分离：

- `send_enabled=false`、executor 离线、队列暂停：Action 保留 `queued`，设置 operational waiting reason。
- conversation changed、resume context changed：记录 preflight decision。
- dispatch API 和 Main World 在真正外部请求前继续复核 runtime 开关。
- runtime 恢复不会跳过 freshness preflight。

## PRIORITY_AND_PAGE_AFFINITY

默认基准：

| Action 来源 | priority |
|---|---:|
| HR 新回复产生的 chat action | 300 |
| 用户主动确认的 chat message | 300 |
| resume_send | 200 |
| greeting | 100 |

计算：

```text
effective_score =
  priority
  + page_affinity_bonus
  + waiting_age_bonus
```

其中：

- `page_affinity_bonus`：
  - exact `target_page_key`：20
  - 同 page kind/account：8
  - 其他：0
- `waiting_age_bonus = min(79, queued_minutes)`。
- 业务档位间隔为 100，而所有 bonus 合计最多 99，因此较低业务档位不能仅凭页面亲和或等待时间越过更高档位。
- 最终排序：`effective_score DESC, queued_at ASC, id ASC`。

页面 key：

- chat/resume：`boss:{account_uid}:chat`
- greeting：`boss:{account_uid}:job:{encrypt_job_id}`

需要 `max_same_page_batch`，建议默认 8。原因是同档位持续进入当前页面时，单靠有上限的 age bonus 仍可能造成其他页面长期等待。

规则：

- 连续 8 个同 page key 后，在同一最高业务档位存在其他页面任务时，下一次必须从其他页面选择。
- 不允许该规则让 greeting 越过 chat_message，或让 greeting 越过 resume_send。
- 页面切换、队列空、较高档位抢占后重置 batch count。

需要抢占：

- 新任务 `priority` 高于当前 action 时，允许抢占 `claimed/preflighting` 阶段。
- 已进入 `dispatching` 后禁止抢占。
- 同档位只在下一次 claim 重排，避免频繁抖动。
- 同 session 后继 action 不能抢占其 predecessor。
- 被抢占 action 回到 queued，保留原 `queued_at`，不损失等待年龄。

claim SQL 必须在一个写事务中完成，并带条件更新，避免当前 chat claim 的选择和 UPDATE 之间出现并发窗口。

## UI_MODEL

### 自动代聊

- 会话列表：
  - unread 数量。
  - `待回复/已规划/排队中/已回复/无需回复`。
  - semantic latest event。
  - HR、公司、岗位、等待时长。
- 消息时间线：
  - `display_text` 为主。
  - platform event 使用独立事件卡片。
  - 可展开查看 raw type/content。
  - 显示 read subject/read state、reply state、覆盖该消息的 action。
- 草稿区：
  - base message、planned_at、conversation revision。
  - preflight/replan 状态。
  - stale/superseded replacement 链。
  - priority 与排队原因。

### 待确认

以统一 `fj_actions.status=awaiting_confirmation` 为数据源：

- Tabs：全部、聊天回复、发送简历、打招呼。
- 展示触发消息、semantic type、基线版本、priority、等待时间。
- 相关消息到达后显示“需重新生成”，不允许继续确认旧文本。
- BossChat 仍作为会话详情和编辑入口。

### 执行队列 / 运行状态

现有 [DeliveryRunStatus.vue](</D:/agent/fine-job/apps/desktop/src/renderer/pages/fine-job/DeliveryRunStatus.vue:287>) 当前只展示 greeting 队列，改为统一队列：

- executor 当前 `page_kind/page_key/context`。
- 当前 action、preflight 阶段、连续同页批次数。
- 按 action type、page kind、status 的数量。
- 每项显示：
  - business priority。
  - 三个 score 分量和 effective score。
  - waiting reason。
  - session sequence。
  - preflight/replan 状态。
  - lease/dispatch 状态。
- unknown session blocker 单独告警。

### 动作日志

现有日志查询只关联 `fj_automation_actions`，见 [delivery_runs.py](</D:/agent/fine-job/backend/app/services/fine_job/delivery_runs.py:331>)。统一后每次状态变化写结构化事件：

- `action_created/confirmed/claimed`
- `preflight_started/passed/replan_required`
- `action_superseded/replacement_created`
- `dispatch_started/accepted`
- `outbound_observed/succeeded`
- `failed/unknown/cancelled`
- `page_switched/priority_preempted`

日志字段包含 action/session/job、旧新状态、priority 分量、page key、waiting/preflight reason、replacement ID。

## FILE_CHANGE_MAP

后端：

- [db.py](</D:/agent/fine-job/backend/app/db.py:1203>)：统一 Action、semantic/state、coverage、preflight schema 和索引。
- [boss_executor.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_executor.py:497>)：替换 greeting 专用 queue selection，保存当前页面与 batch 状态。
- [boss_chat.py](</D:/agent/fine-job/backend/app/services/fine_job/boss_chat.py:974>)：raw ingest、semantic 派生、chat preflight、reply coverage；移除独立 claim 权威性。
- [workflow.py](</D:/agent/fine-job/backend/app/services/fine_job/workflow.py:622>)：greeting 创建统一 Action。
- [job_action_center.py](</D:/agent/fine-job/backend/app/services/fine_job/job_action_center.py:349>)：将现有业务优先级映射到统一 Action priority。
- [execution_reconciliation.py](</D:/agent/fine-job/backend/app/services/fine_job/execution_reconciliation.py:191>)：outbound observation 同时更新 Action 与 covered inbound reply_state。
- `services/fine_job/action_scheduler.py`：统一 eligibility、score、claim、session ordering。
- `services/fine_job/action_preflight.py`：三类 preflight policy。
- `services/fine_job/message_semantics.py`：版本化上下文语义规则。
- [routers/fine_job/boss_executor.py](</D:/agent/fine-job/backend/app/routers/fine_job/boss_executor.py:96>)、[boss_chat router](</D:/agent/fine-job/backend/app/routers/fine_job/boss_chat.py:144>)：迁移为统一 executor actions API。
- [schemas/fine_job/boss_executor.py](</D:/agent/fine-job/backend/app/schemas/fine_job/boss_executor.py:27>)、[boss_chat.py](</D:/agent/fine-job/backend/app/schemas/fine_job/boss_chat.py:139>)：新增页面、基线、preflight 和统一 Action DTO。

扩展：

- [types.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/types.ts:3>)：统一 Action/claim/preflight/page DTO。
- [client.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/client.ts:470>)：合并两套 claim 流程。
- [chat-coordinator.ts](</D:/agent/fine-job/boss-executor-extension/src/finejob/chat-coordinator.ts:309>)：由统一 scheduler 驱动，保留账号领导页能力。
- [entrypoints/boss/index.ts](</D:/agent/fine-job/boss-executor-extension/src/entrypoints/boss/index.ts:43>)：执行 preflight command 和统一 dispatch command。
- [message/content.ts](</D:/agent/fine-job/boss-executor-extension/src/message/content.ts:58>)：报告 current page/context 与 preflight snapshot。
- [read-only-probe.ts](</D:/agent/fine-job/boss-executor-extension/src/platform/boss/read-only-probe.ts:156>)：继续提供 greeting 页面快照。
- [sender.ts](</D:/agent/fine-job/boss-executor-extension/src/platform/boss/chat/sender.ts:115>)、[default-greeting.ts](</D:/agent/fine-job/boss-executor-extension/src/platform/boss/default-greeting.ts:14>)：只接受已通过且 token 有效的统一 Action。

桌面端：

- [types.ts](</D:/agent/fine-job/apps/desktop/src/renderer/types.ts:1397>)、`services/api.ts`：统一 Action、semantic、reply/read 状态。
- [fineJobBossChat.ts](</D:/agent/fine-job/apps/desktop/src/renderer/stores/fineJobBossChat.ts:15>)：semantic inbox 与 action coverage。
- [fineJobBossExecutor.ts](</D:/agent/fine-job/apps/desktop/src/renderer/stores/fineJobBossExecutor.ts:11>)：统一 queue summary/current page。
- [BossChat.vue](</D:/agent/fine-job/apps/desktop/src/renderer/pages/fine-job/BossChat.vue:696>)：消息状态和语义事件。
- [ReviewQueue.vue](</D:/agent/fine-job/apps/desktop/src/renderer/pages/fine-job/ReviewQueue.vue:254>)：统一待确认。
- [DeliveryRunStatus.vue](</D:/agent/fine-job/apps/desktop/src/renderer/pages/fine-job/DeliveryRunStatus.vue:402>)：统一执行队列。
- [AutomationLogs.vue](</D:/agent/fine-job/apps/desktop/src/renderer/pages/fine-job/AutomationLogs.vue:106>)：完整 Action lifecycle。

## MIGRATION_PLAN

1. 加法迁移：新增统一表、字段和索引，旧表继续工作。
2. Raw/semantic 回填：保留全部旧消息；根据 raw_meta、direction 和已有 resume action 生成第一版 semantics。无法可靠判断的记录标记 `platform_event_unknown`。
3. Action 回填：
   - `BOSS_DEFAULT_GREETING` → `greeting`
   - `operation_kind=text` → `chat_message`
   - `operation_kind=resume` → `resume_send`
   - `resume_list` 迁为只读 executor utility operation，不作为业务发送 Action
4. 为旧 chat action 从 reply task 回填 base message、session version、planned_at；缺少可靠基线的 queued action 标记 `preflight_required`。
5. 为同 session 旧动作按 `created_at, id` 分配 `session_sequence`；unknown 动作设为 blocker。
6. 双写阶段：旧创建入口同时写统一 Action，executor 仍读取旧队列。
7. Shadow 排序阶段：只计算统一 scheduler 结果和 score，不执行；比对来源数量、顺序和等待原因。
8. 扩展协议切换：统一 `claim → preflight → dispatch`；旧 claim API停止领取新动作。
9. UI 切换到统一 actions/session semantic API。
10. 停止旧表写入；旧表作为历史来源保留，不删除原始消息或执行记录。
11. 最终离线验证 session 顺序、score 边界、replacement、unknown blocker 和 semantic fixture；真实 BOSS 验证另行取得明确授权。

## STAGE_1_IMPLEMENTATION

- 已新增 `fj_actions`、`fj_action_message_links`、`fj_chat_message_semantics`、`fj_chat_message_states`，并为 raw message 增加独立原始内容列。
- 初始化会幂等回填 `fj_automation_actions`、`fj_chat_send_actions` 的 greeting、chat_message、resume_send，并保留来源、创建时间、状态、租约、结果和 reply task 的真实 `based_on_session_version`。
- greeting、chat_message、resume_send 在创建旧队列记录的同一事务内双写统一 Action；旧 executor 的 claim、dispatch、结果、超时、取消、授权变更和重试同步更新 shadow Action。已观察 outbound 或直接 `conversation_created` 证据确认的成功为最终权威，后到旧表终态不会倒退；无直接成功证据的 canonical unknown 继续保守保留。
- greeting 使用已固化的 `fj_greeting_account_bindings` 生成 job page key；账号暂不可可靠确定时统一 Action 记录 `account_identity_required`、`greeting_account_binding:{job_id}` 并保持 blocked，绑定完成后恢复旧队列对应状态。
- 平台消息按结构、方向、已观察到真实 outbound 的同会话简历 Action 和精确文本派生 semantic/state；实时与历史 raw 内容、raw body 和未知字段独立保留。
- reply planning 由 `semantic.reply_required` 驱动；未知附件确认/文件名文本保持 `platform_event_unknown`。一条 chat action 覆盖 reply task 的完整消息集合，failed/cancelled/stale 回退 `unanswered`，确认替换动作会建立 `supersedes_action_id`、转移完整 coverage，并将继承消息推进为 `queued`。
- `human_takeover` 仅停止自动生成控制，不再阻断用户手工确认 chat_message 或手工创建 resume_send；`paused` 继续取消旧队列动作。
- 旧 greeting/chat executor queue 继续作为实际执行来源；未接入 unified scheduler，未调整 BossChat、ReviewQueue、DeliveryRunStatus UI。
- 定向验证覆盖历史回填幂等与基线、授权与生命周期单调性、直接 greeting 成功证据、greeting 身份绑定、实时 raw 保存、平台事件回复拦截、简历弱关联降级、coverage 回退和真实 realtime replacement 链。

READY_FOR_SCHEDULER_CUTOVER
