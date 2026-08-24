# FineJob

FineJob 是一个本地化的 AI 求职驾驶舱，目标是帮助用户管理简历、求职目标、岗位筛选、HR 沟通和待处理事项，并在用户授权下执行可控的浏览器自动化。

当前项目提供可复用的 OCR、PDF 解析、Playwright、FastAPI、LangGraph、SQLite、LLM Provider 等能力，前端产品层聚焦求职场景。

## 产品方向

- 简历中心：上传简历、解析内容、形成结构化事实库。
- 求职目标：维护多个岗位方向、城市、薪资、关键词和排除词。
- 岗位池：采集岗位、去重、评分、解释推荐或跳过原因。
- 待处理：集中处理投递、打招呼、HR 回复、面试时间等需要确认的事项。
- 对话辅助：基于简历事实和岗位信息生成回复草稿。
- 受控自动化：通过可见浏览器完成用户确认后的页面操作。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 桌面端 | Electron, Vue 3, Pinia, Element Plus, Vite |
| 后端 | FastAPI, Pydantic, LangGraph |
| 存储 | SQLite, SQLite FTS5 |
| AI | OpenAI-compatible LLM / Codex CLI / Embedding provider / stub provider |
| 文档解析 | PyMuPDF, pymupdf4llm, RapidOCR |
| 网页自动化 | Playwright |
| BOSS 岗位采集 | Chrome CDP + 页面原生 API 响应旁听 |
| BOSS 执行器 | WXT + Chrome MV3 + MQTT + Protobuf |
| 测试 | pytest, Vitest, Vue Test Utils |

## 项目结构

```text
apps/desktop/
  electron/                 Electron main/preload/tray/shortcut modules
  src/renderer/             Vue pages, stores, components
  scripts/                  desktop dev/build helpers

backend/
  app/
    graphs/                 LangGraph workflows
    routers/                FastAPI routers
    schemas/                API contracts
    services/               OCR, PDF parse, web capture, LLM, retrieval services
      fine_job/boss_scraper/ FineJob 内置 BOSS CDP 采集模块
  tests/                    pytest suites

docs/                       产品和架构文档
scripts/                    backend start and helper scripts
boss-executor-extension/    BOSS 默认招呼与自动代聊 Chrome 执行器
```

## 本地启动

安装依赖：

```powershell
pnpm install --config.offline=false
uv sync --group test
```

如果 Electron 二进制缺失，执行：

```powershell
$env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
pnpm rebuild electron esbuild --config.offline=false
```

启动桌面端：

```powershell
pnpm --filter desktop dev
```

启动后端：

```powershell
./scripts/start-backend.ps1
```

## 使用本机 Codex CLI

FineJob 可以继续使用原有 LLM，也可以复用本机已经登录的 Codex CLI。先确认：

```powershell
codex --version
codex login status
```

然后在桌面端“FineJob 配置 → 智能执行器”中选择“本机 Codex CLI”。模型支持下拉选择，也可以直接输入自定义模型 ID；点击“刷新模型列表”会调用 `codex debug models` 获取当前 Codex 模型目录，不需要额外填写 API Key。推理强度支持默认、Minimal、Low、Medium、High 和 XHigh。模型或推理强度留空时跟随 Codex 默认值。保存后点击“检测 Codex CLI”。

Codex 生成任务使用临时目录、只读沙箱、JSONL 事件和 JSON Schema 结果约束。FineJob 不读取 Codex 登录凭据，最终业务结果及运行元数据仍保存在 FineJob 中。

## 内置 BOSS CDP 采集模块

FineJob 在 `backend/app/services/fine_job/boss_scraper/` 内置 BOSS CDP 采集能力，并通过
`service.py` 向 FineJob 其他模块提供稳定调用入口。

FineJob 已将平台登录、岗位采集页和投递准备中的真实采集统一切换到该 CDP 服务。桌面端
“岗位采集”支持打开专用 Chrome、定位搜索页，以及“当前页有效时采集当前页、否则自动导航”
两种操作路径。城市选择来自内置采集器的完整码表；长时间采集通过后台任务显示列表页、岗位详情、
当前岗位和预计剩余时间。详情既可在列表后自动采集全部，也可由用户手工选择、按多场景岗位筛选
策略或 AI 初筛后再采集；详情完成后可统一生成规则或 LLM 投递建议。评估 V2 对每个岗位单独调用
AI，输出硬性条件证据、分维度匹配、优势、差距、风险、缺失信息、简历优化建议和安全招呼语草稿，
不生成向招聘者追问的问题。筛选结果使用通过、排除、
待判断三态，招聘者活跃状态在详情采集后回填，避免把列表阶段的缺失值误判为不活跃。
采集结果会写入 SQLite：同一岗位按 BOSS 岗位标识去重，重复采集时更新最后采集时间和采集次数。
桌面端“历史采集”作为与“岗位采集”同级的独立页面，支持关键词、城市、公司规模、公司行业、
融资阶段、详情状态、
重复状态和日期范围筛选，并支持白名单字段排序和分页。当前批次列表会标记新岗位/历史岗位，
展示公司规模并使用固定高度滚动表格；成功完成后进度卡自动收起，失败状态继续保留。
历史页通过可排序列标题切换服务端排序；历史页和当前岗位采集列表都支持单岗“采集详情/重新采集详情”。
历史岗位单独刷新详情不会增加岗位采集次数。
桌面端“策略管理”统一管理多条岗位筛选策略、岗位建议投递策略和全局投递执行策略。旧单例
求职意向首次读取时会迁成默认筛选策略；公司行业、融资阶段和福利已提升为岗位主记录正式字段，
旧采集记录会从保留的原始 payload 自动回填。
每次 V2 评估还会写入不可变的 `fj_job_evaluations` 记录，并按投递执行策略路由到待确认池或
`fj_automation_actions` 持久化队列。推荐但未授权自动打招呼、以及 AI 待确认岗位进入待确认池；
不建议岗位保留原结论并允许用户明确覆盖。桌面端“待确认”页面支持编辑招呼语、批准、拒绝和查看
已批准项。动作队列已连接独立 Chrome 执行器，可在用户授权后串行执行 BOSS 默认招呼。
旧 Puppeteer 登录和采集文件仍保留用于迁移期对照，但正式业务入口不再调用；
当前不提供真实投递或“最新发布”专用逻辑。
调试 CLI 可直接从 FineJob 环境运行：

```powershell
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --check
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --setup-chrome
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --keyword "AI Agent" --city 上海 --pages 1
```

## BOSS 自动代聊（首版）

桌面端新增“自动代聊”工作台，扩展在用户开启监听后观察 BOSS `chat` WebSocket 的新消息，后端按
实时、定时或手动三种触发方式生成回复草稿。草稿可以重新生成和人工编辑；只有用户点击“确认发送”
且草稿依据的最后入站消息与会话版本仍一致时，才会创建一次发送动作。

首版明确不读取 BOSS 历史聊天、不提供回复置信率、不使用 QA 集、不自动发送简历或联系方式，也不会
自动确认 AI 草稿。发送使用 BOSS 的 `chat` MQTT/Protobuf 通道；`publish` 未抛错只展示为“已提交发送”，
不表示平台已经送达。多个 BOSS 沟通页通过账号级租约选出一个领导标签页，领导失效后递增 epoch 切换，
未知发送结果不会自动重试。

实现和限制见《自动代聊功能具体执行方案.md》，扩展安装与验证见
《boss-executor-extension/README.md》。

## 当前状态

当前阶段是项目框架清理和 FineJob 产品骨架搭建：

- 前端旧知识库页面会被移除。
- 可复用底层能力会保留。
- 后端旧业务 API 暂时保留，后续按模块确认后再删。
- FineJob 的数据库表、API、Agent 和自动化执行器会逐步新增。

## 文档

- `docs/产品计划.md`
- `docs/架构设计.md`
- `docs/安全策略.md`
- `docs/Codex执行器适配计划.md`
- `docs/遗留代码参考.md`
- `自动代聊功能具体执行方案.md`
