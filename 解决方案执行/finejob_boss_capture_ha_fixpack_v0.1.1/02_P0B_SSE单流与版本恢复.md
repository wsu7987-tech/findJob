# 02 / P0B：共享实时订阅与状态恢复

本次执行任务。保留原文件名方便引用，当前方案采用应用级共享 current + detail 完整快照订阅。单流及增量协议移至后续范围。

## 1. 当前调用链

- BossCapture.vue 创建页面级 current 和 detail EventSource。
- App.vue 启动 services/fineJobWorkflowCodexController.ts 中的 Smart Capture 控制器，控制器也创建 current 和 detail。
- 活动 Smart Capture 下可能合计四条相同业务的 SSE，另有独立职责的父 Workflow 流。
- publish_smart_capture_snapshot 总是发布对应任务快照，仅在 current_pointer_changed 时发布 current 快照。
- 页面已在终态关闭 detail；全局控制器也已有部分终态清理。
- 当前 broker 只保留最新完整快照，可以用最新快照恢复状态。

上述事实决定本次应保留 current 的身份通知和 detail 的进度通知职责。

## 2. 目标连接模型

| 场景 | current | Smart Capture detail | 父 Workflow |
| --- | --- | --- | --- |
| 无 current | 应用级一条 | 无 | 按既有页面业务需要 |
| current 为活动或可继续任务 | 应用级一条 | 当前任务最多一条 | linked 且需要父镜像时保留 |
| current 进入终态 | 应用级一条 | 关闭 | 根据父任务状态独立决定 |
| 离开采集页面 | 保持 | 按 current 状态保持 | 释放页面持有的父镜像订阅 |
| 应用关闭 | 关闭 | 关闭 | 按应用生命周期释放 |

本次连接验收为整个 renderer 共用一套 current + detail：活动任务至多两条 Smart Capture SSE，终态最多一条。父 Workflow 单独统计。

## 3. 所有权

优先新增一个专用 Pinia Store，建议路径 stores/fineJobSmartCapture.ts，集中负责：

- 当前任务身份与完整快照；
- 一套 current 和 detail 连接；
- 初次加载、同步错误、连接恢复状态；
- refreshCurrent、startRealtime、stopRealtime 等必要动作。

实施前确认没有等价模块；已有等价实现时直接复用。

App.vue 持有应用级启停。页面读取 Store 状态并注册自己的展示监听。全局控制器消费同一份快照，继续负责自动分析交接；移除其自行创建的重复 EventSource。

页面卸载只释放页面监听和区域请求。应用级连接继续服务自动分析；重新进入页面复用快照，需要时刷新。不改 Codex handoff 的业务判断、批次去重和控制权。

## 4. 身份、版本和竞态

- current API 或 current 流决定当前任务身份；detail 快照只能更新当前身份。
- detail 回调检查连接代次、任务 ID 及 state_version。
- 同一任务旧版本被忽略；不同任务的版本号不做大小比较。
- current 读取开始后若已收到新的身份事件，旧 GET 不覆盖新身份。
- 同一身份下 GET 与 SSE 并发时应用较新版本。
- current 返回 null 时关闭 detail，清理当前任务展示和相关区域。
- 切换 current 时立即使旧 detail 和相关区域请求失效。
- 终态立即关闭 detail；保留 current 等待下一任务。
- 相同快照重复到达不会重复创建连接或重复触发自动分析。

## 5. 连接恢复

用户只需看到简明的“连接中 / 正在重新同步 / 同步失败”提示，正常连接不增加页面噪声。

- 断线保留最后成功快照，提示同步可能延迟。
- 使用 EventSource 已有自动重连，避免同时再创建第二套重连循环。
- 重连成功后读取一次 current 权威状态；按当前身份恢复 detail。
- 同步请求合并，失败以有界请求和退避恢复。
- 初次请求失败有错误与重试入口，不能直接显示无任务。
- 无 EventSource 的实际支持环境如需回退，使用同一 Store 的有界读取恢复，不引入第二个状态来源。
- 格式错误事件结束本次解析并触发必要同步，页面保持可操作。

本次继续传输完整快照，不新增 event_id、delta、事件缓冲表或事件缺口算法。

## 6. 页面区域刷新

概览与列表消费同一权威快照。Context 和 Analysis 沿用现有语义阶段刷新规则，补齐请求状态。

应用级快照应用不等待页面的 Context 或 Analysis 请求完成，避免附属区域拖住主进度。切换模式隐藏区域时，可结束页面订阅或延后非必要读取；自动分析控制器仍正常运行。

## 7. 授权后的验收

对应 08 的 RT、RC 项：

- 页面与全局控制器共用连接，进出页面不增加重复连接。
- detail 持续展示列表和详情采集进度。
- 断线恢复后同步最新状态；旧任务事件无法污染新任务。
- null、终态、卸载、应用关闭的生命周期正确。
- 离开采集页后自动分析仍执行，重复快照不重复交接。
- linked 父镜像与历史查询保持现有身份边界。

后续如切换为单流增量协议，需另行定义缺口恢复与数据资源拆分；该事项不影响本次验收。
