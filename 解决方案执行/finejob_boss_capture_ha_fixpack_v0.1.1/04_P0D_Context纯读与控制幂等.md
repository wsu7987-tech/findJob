# 04 / 后续任务：Context 纯读与控制幂等

文档修订：2026-09-26-r1；基于 `task-cockpit-phase1@b5c4df74f170661d98a1cd236cf693296e85b41d` 静态核对。新增接口/字段均为待实施规划，代码与业务测试尚未执行。

Context 纯读和完整控制幂等迁移仍为后续。补齐版已将 14 的启动意图回执、有限去重及轻量回执表迁移纳入本次；不能把它们再次延后。本次保留父子控制语义。

## 1. 已核实事实

- smart_capture_domain.py 的 get_context_snapshot 在缺少快照时调用 _create_context_snapshot。
- 手动分析批次与分析 Item Context 的服务调用也使用 get_context_snapshot。
- pause、resume、stop 路由已有 transition_id 入参。
- 前端对应 API 方法目前未发送 transition_id。

这些事实说明纯读和幂等改造有价值；完整迁移需要同时覆盖服务调用和 UI。

## 2. 本次约束

- Context 请求有独立 loading、明确错误和请求身份校验。
- Context GET 排除公共自动重试。
- 保留必要的已有 Context 创建触发链，不仅因区域折叠就删除调用。
- create、start、pause、resume、stop、retry 均不自动重放。
- 启动类写请求超时后按 14 查询 operation 回执；其他控制查询目标任务。current/active 为空不构成失败证明。网络 loading 结束不解除未决启动锁。
- 同一次提交期间按钮保持 loading；请求结束而结果未确认时改为待确认提示并保留提交锁，支持手动查询。
- 现有 transition_id 与服务端控制实现继续保留。

上述处理进入 01、05、14 和本次验收矩阵。启动意图去重是本次有限实现，不等于完整控制幂等迁移。

## 3. 后续 Context 迁移

- GET 仅返回已有资源，明确 missing 语义。
- 创建、刷新改为显式 mutation 或 engine 行为。
- 逐一迁移手动分析、Item Context、自动分析与采集流程的依赖。
- 确保关闭 UI 后运行所需 Context 仍能生成。
- 定义生成失败、过期和重新生成时的业务处理。

## 4. 后续控制幂等

- 每次用户意图使用稳定 transition_id。
- 同一意图的重发复用 ID，新意图使用新 ID。
- 服务端同一次操作只执行一次副作用。
- 响应丢失后先读取确认，必要时使用原 ID 重发。
- 覆盖 linked 父控制、independent、恢复、停止及终态行为。

完整控制幂等另立项确定其额外数据结构；本轮仅添加 14 所需启动回执结构，不增加增量事件协议。
