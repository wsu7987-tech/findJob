# FineJob BOSS 执行器

这是 FineJob 的独立 BOSS 浏览器执行器子项目。当前 `0.1.0` 已完成阶段 1 的只读岗位身份识别，不连接 FineJob 后端，也不会点击页面、调用 BOSS 变更接口或发送任何消息。

## 当前能力

- WXT + Manifest V3 + Vue 3 + TypeScript 独立工程。
- 适配 `boss-helper` 的 `comctx` 代理结构，完成 MAIN → Content → Background 三层健康检查。
- BOSS 页面右下角 Shadow DOM 状态面板。
- 只读识别搜索页、推荐页和独立岗位详情页的当前岗位身份；列表与详情或详情 URL 与页面数据不匹配时失败关闭。
- 列表页读取 Vue `jobList`/`jobDetail`；独立详情页读取页面自带的 `_jobInfo`、唯一 HR 姓名和沟通按钮状态。
- 详情页要求沟通按钮文本、`data-isfriend` 以及按钮 `data-url` 中的岗位身份一致；“继续沟通”映射为已沟通，“立即沟通”映射为未沟通。
- 显示岗位名称、身份来源、HR/脱敏 HR 标识、脱敏岗位 ID、登录状态和是否已沟通，不显示 `securityId` 等内部校验字段。
- 真实动作硬禁用。
- 独立类型检查、单元测试、Chrome 构建和 ZIP 打包。

## 安装依赖

```powershell
pnpm install --config.offline=false
```

## 验证和打包

```powershell
pnpm run typecheck
pnpm run test
pnpm run build
pnpm run zip
```

构建产物：

```text
.output/chrome-mv3/
.output/fine-job-boss-executor-0.1.0-chrome.zip
```

实际 ZIP 文件名以 WXT 输出为准。

## 在 Chrome 中加载

1. 打开 `chrome://extensions`。
2. 开启右上角“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择本项目的 `.output/chrome-mv3`。
5. 打开或刷新 `https://www.zhipin.com/` 页面。

框架可用时，页面右下角会出现“FineJob BOSS 执行器”状态面板，并显示：

```text
框架已加载
Background：正常
Content：正常
Main World：正常
模式：只读框架
FineJob：尚未连接
真实动作：已禁用
岗位只读识别：正常 / 等待 / 错配 / 不可执行
页面类型：搜索 / 推荐 / 详情
岗位、身份来源、HR、脱敏岗位 ID、已沟通状态
```

三层均为“正常”只证明扩展入口和内部通信可用。“岗位只读识别：正常”还需要人工核对面板岗位与页面当前岗位一致；这些结果都不代表自动打招呼已经实现。

当前自动化验证结果：类型检查通过，3 个测试文件共 19 项测试通过，Chrome MV3 生产构建和 ZIP 打包通过。用户已确认 Background、Content、Main World 均正常；阶段 1 仍需在搜索页、推荐页和详情页人工核对至少 20 个岗位，要求零错配。详情页 HR 姓名和沟通状态可以只读显示，但 `_jobInfo.user_id` 目前仍只是“待验证 HR 标识”，不得进入后续真实动作。

## 安全边界

- 当前没有 localhost/FineJob API 权限。
- MAIN World 只读 BOSS 页面 Vue 或独立详情页 `_jobInfo` 中岗位身份所需的字段，并把经校验的最小快照交给隔离世界状态面板。
- 登录状态只输出布尔值；不输出 `_PAGE.encryptUserId` 原值或 `_userInfo` 内容。
- 当前不读取 Cookie、BOSS token、简历或聊天内容。
- 当前不包含动作领取、页面点击、私有接口请求或消息发送代码。
- 当前没有 `friend/add`、聊天 WebSocket/MQTT、页面按钮点击或消息发送实现。

## 参考来源

工程入口、代理通信以及列表页岗位字段接入方式适配自本地固定快照 `D:\agent\参考项目\boss-helper`，提交 `09df246399bd4edd4a1e35793bfe028e23578330`。独立详情页入口依据用户保存的真实 HTML 中 `_jobInfo` 结构自主实现；`boss-crawler-skill` 仅用于交叉确认其也从 HTML 定位 `job_id`、`securityId`，未复制代码。轮询快照、失败闭锁、载荷校验、面板和测试为自主实现。详情见《第三方代码与许可证说明.md》。
