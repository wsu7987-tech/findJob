su，以下为 P4 第一轮独立 Reviewer 报告。本轮未修改代码或文档，未执行 native trace，未发送消息、未投递、未 replay。

## Verdict

`BLOCK`

阻断原因集中在 Trace 完整性：发生缓冲溢出或停止阶段漏收后，下游 window、diff、candidate report 仍可能把结果当作完整证据使用。

## Blockers

### B1 — 不完整 Trace 未向下游传播，可能被错误用于 Native Evidence

CDP 4096 与 Trace 20000 的淘汰计数会写入 export diagnostics，但完整性状态到此中断：

- window/range 返回裸 `events` 列表，不携带 diagnostics：[service.py](D:/agent/fine-job/backend/app/services/fine_job/boss_scraper/service.py:121)
- diff 只读取 `events`，完全忽略 diagnostics：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:359)
- candidate report 只接收候选列表：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:473)
- 候选数量为 1 时仍会给出 `HIGH` confidence，即使 Trace 已淘汰记录：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:448)
- 测试只断言计数存在，没有断言 diff/report 拒绝不完整证据：[test_boss_network_trace.py](D:/agent/fine-job/backend/tests/services/test_boss_network_trace.py:272)

因此，一个 `dropped_events > 0`、`cursor_overflow_events > 0` 或 `droppedTraceRecords > 0` 的文件仍可得到 `matches=true` 或 `Confidence: HIGH`。这直接违反“不完整 trace 不应升级 Native Evidence”。

### B2 — stop 阶段存在无 diagnostics 的尾部事件丢失

监听线程停止时只执行一次最终 `_process_buffered_events()`：[boss_network_debug.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_debug.py:171)。

处理 `loadingFinished` 时，`Network.getResponseBody` 的 `send()` 可能继续接收并缓冲新 CDP events：[boss_network_debug.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_debug.py:230)。这些新事件不会再次被消费，随后 `_finish()` 直接 disable、close、export：[boss_network_debug.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_debug.py:258)。

这类丢失不一定触发容量淘汰计数，最终文件可能显示 diagnostics 正常但缺少 remote echo、PUBACK 或 messageSync。

## High Risks

### H1 — normalized diff 会抹掉关联关系

`clientMid`、`serverMid`、`packetId`、`requestId`、`sessionId` 全部统一替换成同一个 `<dynamic>`：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:29)。

这会掩盖：

- PUBLISH packetId 与 PUBACK packetId 不一致；
- sent clientMid 与 messageSync clientMid 不一致；
- messageSync serverMid 与后续 echo 不一致；
- frame 被归到不同 WebSocket request/session。

字段存在、数量和顺序虽保留，协议关联正确性没有保留。

### H2 — observational decoder 仍有 self-validation 与误判空间

优点是 Python decoder 没有复用 TypeScript production parser。

当前缺口：

- MQTT/Techwolf fixture 由测试内的编码 helper 根据同一组字段假设生成：[test_boss_network_trace.py](D:/agent/fine-job/backend/tests/services/test_boss_network_trace.py:52)
- 没有独立来源的固定原始 frame fixture。
- Python MQTT decoder没有严格验证全部 fixed-header flags、零长度 topic、控制包精确长度和零 packetId：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:266)
- 所有 PUBLISH payload 都会尝试 Techwolf decode，没有先限定已验证 topic：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:648)
- `rawEvidence` 只保存方向、opcode 和长度，不保存可供第二实现复核的原始帧：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:659)

因此当前解释器可以证明“本地 parser 能解释本地 fixture”，不足以独立证明 native payload 的协议含义。

### H3 — redaction 存在路径和 topic 旁路

已确认安全的部分包括 query value、body value、header value和聊天正文均未落盘。

剩余旁路：

- URL path 仅专门遮蔽 `/job_detail/{id}`，其他 path 中的身份或安全标识会原样保存：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:64)
- MQTT topic 只遮蔽七位以上纯数字；字母数字混合标识或短 ID 会保留：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:113)
- `businessCode` 接受任意字符串，缺少最终输出层的敏感值校验。

### H4 — 声称有界的 Trace 仍保留无界辅助状态

`records` 有 20000 上限，但 `requests`、`web_sockets` 以及 DebugRun 的 `request_meta` 不会在完成或关闭后清理：[boss_network_trace.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_trace.py:504)。长时间 HTTP Trace 的内存仍可持续增长。

### H5 — LIVE_VALIDATION.md 尚不能由不读源码的人完整执行

文档只给出 start/mark/stop/status API：[LIVE_VALIDATION.md](D:/agent/fine-job/docs/LIVE_VALIDATION.md:34)。以下环节没有可执行入口或完整命令：

- inspect marker window/range；
- candidate report；
- sender dry-run normalized 输出获取；
- native trace 与 dry-run diff；
- overflow 时的自动拒绝规则。

另外 sender dry-run 的 normalized 结构与 Trace `events` 结构不同，当前 diff 无法直接比较：[sender.ts](D:/agent/fine-job/boss-executor-extension/src/platform/boss/chat/sender.ts:107)。

## CDP Reuse Verdict

结论：通过“没有第二套实现”的检查，保留一个并发连接 caveat。

- P3 没有新增 Chrome connector、WebSocket client、Target scanner 或 recorder manager。
- Trace 使用已有 `CDPSession`：[boss_network_debug.py](D:/agent/fine-job/backend/app/services/fine_job/boss_network_debug.py:78)
- 一个 Trace run 使用一条 browser-level CDP WebSocket，并在其上 attach 多个 target session、执行 `Network.enable`。
- 同一连接的两个 `recv()` 入口均由 `_io_lock` 串行保护：[boss_cdp_raw.py](D:/agent/fine-job/backend/app/services/fine_job/boss_scraper/boss_cdp_raw.py:370)
- response 和 event 分别进入 pending table 与 event buffer，未发现 response 被 event reader 吞掉的路径。

系统没有全局连接协调；其他采集任务与 Trace 并行运行时仍可分别创建已有 `CDPSession` 的物理连接。当前能证明的是单个 Trace run 内没有第二条连接。

## Existing Capture Regression

岗位列表、联系人和详情 capture 已改为独立 cursor，读取不会删除其他 consumer 的事件，顺序执行路径成立。

回归仍未完全关闭：

- 4096 缓冲包含所有事件，高频 WS frame 可以淘汰 capture 所需的 request/finished 事件。
- 这些 capture 不检查自己的 cursor overflow，最终表现可能只是超时或返回空结果。
- `events_since()` 没有与 `_append_event()` 共用锁；如果未来真正由多个线程同时消费，遍历期间前端删除/追加可能造成跳过或重复。现有测试只覆盖顺序 consumer。

## Test Quality

独立全量复跑结果：

```text
482 passed, 6 failed, 2 warnings in 506.93s
```

本次 warning 数为 2，P3 记录的第 3 个 warning 未复现。两项 warning 都来自 summary 后台线程在测试 teardown 后访问数据库，调用栈为 `runs.py → summary_nodes.py`。

6 个失败的独立因果结论：

| 失败测试 | 首个业务分歧 | P3 调用链判断 |
|---|---|---|
| `test_cooldown_rules_require_detail_and_evaluation_before_exclusion` | 旧断言期望 `applied`，现实现明确返回 `pending_application`：[job_applications.py](D:/agent/fine-job/backend/app/services/fine_job/job_applications.py:95) | 未进入 CDP/Trace |
| `test_pdf_markdown_and_text_items_complete_stub_delivery_chain` | Markdown `summary_text` 格式断言不同，栈位于 ingest/summary/report | 未进入 CDP/Trace |
| `test_clean_resume_text_removes_common_pdf_artifacts` | 直接调用 `clean_resume_text`，重复行未被删除：[resume_text.py](D:/agent/fine-job/backend/app/services/fine_job/resume_text.py:22) | 未进入 CDP/Trace |
| `test_build_retrieval_context_returns_ranked_hits_and_parent_context` | `ChunkVectorStore.search_related()` 返回零结果：[retrieval.py](D:/agent/fine-job/backend/app/services/retrieval.py:29) | 未进入 CDP/Trace |
| `test_rebuild_chunk_index_version_populates_candidate_collection_without_mutating_chunks` | candidate vector collection 搜索不到重建记录：[retrieval_index_versions.py](D:/agent/fine-job/backend/app/services/retrieval_index_versions.py:246) | 未进入 CDP/Trace |
| `test_commit_web_draft_creates_url_pool_item` | 当前 canonical content 优先保存解析 Markdown，旧断言期望 URL 派生文本：[pool.py](D:/agent/fine-job/backend/app/services/pool.py:240) | 未进入 CDP/Trace |

这些失败路径未导入或调用 `boss_network_trace`、`boss_network_debug`、`boss_cdp_raw`、Trace service 方法。P3 模块导入时只定义类和创建未启动的 manager，没有连接、线程或数据库写入。可以独立判定这 6 项与 P3 CDP/Trace 改动无因果关系，不升级为 HIGH/BLOCKER。

P3 测试本身全部通过，但未覆盖上述 Blocker、真实并发 cursor、停止尾部事件、关联 ID 错配、path/topic 脱敏和不完整证据 fail-closed。

## Self-Validation Risk

`HIGH`

Python MQTT decoder具备实现独立性；fixture 来源、严格验证范围以及缺少可复核 raw frame 使证据独立性不足。当前结果可作为观察线索，不能作为 Native Evidence 升级依据。

## Protocol Evidence Integrity

当前协议文档仍把 WS URL、topic、QoS、retain、DUP、messageSync schema/语义和 endpoint 保持为 `NATIVE_TRACE_REQUIRED` 或 `LIVE_VALIDATION_REQUIRED`，没有发现把 reference repo、mock 或离线 fixture写成 native verified。

P2 的语义边界也保持正确：

- local send 只记录 transport write；
- PUBACK/回调仍为 transport accepted；
- messageSync 只保存关联，不直接提升成功；
- remote echo 才是平台确认候选。

由于 Blocker B1/B2 和 H1，当前 Trace/diff 结果还不能用于改变这些证据等级。

## Side Effect Safety

- marker 只更新本地内存时间与名称，无页面 evaluate、fetch、publish、click 或 replay。
- candidate HTTP report 只读取 normalized events 并渲染文本，没有 replay 能力。
- sender dry-run 在 `connect()`、运行时开关检查和 `publish()` 之前返回，不产生网络发送。
- receive-only existing socket race 在 [LIVE_VALIDATION.md](D:/agent/fine-job/docs/LIVE_VALIDATION.md:134) 中仍明确标记为未解决风险，未被错误宣布关闭。

## Native Trace Still Required

仍需真实页面证据确认：

- WebSocket host/path 与握手；
- MQTT topic、QoS、retain、DUP、packetId/PUBACK；
- Techwolf 字段、类型和值域；
- messageSync clientMid/serverMid 格式、时序和语义；
- remote echo/history 与平台确认关系；
- clientMid 去重范围。

完成 Blocker 修复前不应执行这轮 native trace，否则结果可能无法证明自身完整性。

## Live Validation Still Required

- get/wt 与连接认证组合；
- 联系人加密字段优先级；
- runtime send 开关传播；
- candidate HTTP endpoint 与业务成功语义；
- existing receive-only observer race 的真实影响；
- redaction 输出人工审阅。

## Exact Next Action

最小修复清单：

1. 为 trace/window/range/diff/report统一返回 `evidenceComplete` 和 gap reasons；任一相关 overflow/drop 时，diff 禁止返回 `matches=true`，candidate report 禁止给出 `HIGH`。
2. stop 时循环处理尾部缓冲直到稳定，再 disable/close；最终 diagnostics 在最后一次读取后生成。
3. diff 对动态 ID 使用稳定别名映射，保留相等关系；增加错误 packetId/clientMid/requestId 关联测试。
4. 严格化 observational decoder、限定 Techwolf topic，并加入独立固定 binary fixture。
5. 补齐 path/topic/businessCode 脱敏及辅助状态清理。
6. 为 LIVE_VALIDATION.md 提供实际可执行的 inspect、dry-run、report、diff 流程，并明确 overflow 时拒绝证据升级。