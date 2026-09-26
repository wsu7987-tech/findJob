# 04 / 后续任务：Context 纯读与控制幂等

完整协议迁移列为后续独立任务。本次保留现有接口及父子控制语义，处理用户可见的 loading、错误和超时后状态同步。

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
- 写请求超时后先查询权威状态，展示实际结果或待确认提示。
- 同一次提交期间按钮保持 loading，避免常规重复点击。
- 现有 transition_id 与服务端控制实现继续保留。

上述处理进入 01、05 和本次验收矩阵，不等于已完成完整幂等迁移。

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

该项获批后再确定数据结构和迁移需求，不在本轮添加数据库结构或事件协议。
