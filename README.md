# FineJob

FineJob 是一个本地化的 AI 求职驾驶舱，目标是帮助用户管理简历、求职目标、岗位筛选、HR 沟通和待处理事项，并在用户授权下执行可控的浏览器自动化。

当前项目提供可复用的 OCR、PDF 解析、Playwright、FastAPI、LangGraph、SQLite、LLM Provider 等能力，前端产品层聚焦求职场景。

## Codex 工作台

桌面端“Codex 工作台”嵌入本机已登录的 Codex TUI。Electron 自动启动或复用 FineJob 后端，并管理本实例的单一 Codex PTY 会话，支持新建、恢复最近会话、中断和结束；Electron 退出后本地后端可由下次启动继续复用。FineJob Skill 与 FineJob Profile Skill 的正式资源保存在 `apps/desktop/resources/codex/skills/`，启动时同步到专用工作区并加载 MCP 配置。Codex 可使用 37 个 `finejob.*` 工具读取正式策略和统一岗位评估上下文、驱动现有岗位采集与筛选、在原搜索页动态继续下滑或停止当前采集、获取当前 JD、分析求职资料、保存岗位评估、创建打招呼预览、生成代聊草稿并请求受控发送。

敏感操作在工作台中配置总开关和分项预授权。未获预授权的请求显示在待确认卡片中；真实动作仍由 FineJob 的业务版本、登录态、执行器和任务状态共同校验。

## 产品方向

- 求职资料中心：管理 PDF 简历、Markdown、手动文本和项目资料，通过 OCR/识别与 AI 生成可确认的事实、QA、策略和上下文。
- 求职目标：维护多个岗位方向、城市、薪资、关键词和排除词。
- 岗位池：采集岗位、去重、评分、解释推荐或跳过原因。
- 待处理：集中处理投递、打招呼、HR 回复、面试时间等需要确认的事项。
- 对话辅助：基于已确认且允许对外使用的候选人上下文和岗位信息生成回复草稿。
- 受控自动化：通过可见浏览器完成用户确认后的页面操作。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 桌面端 | Electron, Vue 3, Pinia, Element Plus, Vite, node-pty, xterm |
| 后端 | FastAPI, Pydantic, LangGraph, Python MCP |
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
  resources/codex/skills/   FineJob Codex Skill 正式资源
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

然后在桌面端“FineJob 配置 → 智能执行器”中选择“本机 Codex CLI”。模型支持下拉选择，也可以直接输入自定义模型 ID；点击“刷新模型列表”会调用 `codex debug models` 获取当前 Codex 模型目录，不需要额外填写 API Key。推理强度支持默认、Minimal、Low、Medium、High 和 XHigh。模型或推理强度留空时跟随 Codex 默认值。保存后点击“检测 Codex CLI”。该选择同时用于求职进展分析和自动代聊消息草稿生成；选择 Codex CLI 后，生成消息不再要求配置 LLM API Key。

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
岗位采集的“更多筛选条件”支持全职/兼职及其附属薪资、结算方式、兼职时间，和工作经验、学历、
公司规模、融资阶段；多选值会按 BOSS 搜索页参数以英文逗号拼接，切换求职类型时会清空对应附属条件。
展开区还可选择已保存的岗位筛选策略，通过“智能选择”将其中可映射的工作类型、薪资、经验、学历、
公司规模和融资阶段填入 BOSS 搜索条件。
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
`fj_automation_actions` 持久化执行任务。推荐但未授权自动打招呼、以及 AI 待确认岗位进入待确认池；
不建议岗位保留原结论并允许用户明确覆盖。桌面端“待确认”页面支持编辑招呼语、批准、拒绝和查看
已批准项。执行任务已连接独立 Chrome 执行器，可在用户授权后执行 BOSS 默认招呼。
旧 Puppeteer 登录和采集文件仍保留用于迁移期对照，但正式业务入口不再调用；
当前不提供真实投递或“最新发布”专用逻辑。
调试 CLI 可直接从 FineJob 环境运行：

```powershell
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --check
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --setup-chrome
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --keyword "AI Agent" --city 上海 --pages 1
```

## BOSS 自动代聊

桌面端新增“自动代聊”工作台，扩展在用户开启监听后观察 BOSS `chat` WebSocket 的新消息，后端按
实时、定时或手动三种触发方式生成回复草稿。草稿可以重新生成和人工编辑；只有用户点击“确认发送”
且草稿依据的最后入站消息与会话版本仍一致时，才会创建一次发送动作。

工作台可按需同步 BOSS 历史聊天，并在会话顶部展示当前阶段、等待对象、等待时长、沟通来源、跟进建议与拒绝原因。
“分析进展”只处理当前会话，并按状态提供“生成回复”“生成跟进”或“询问拒绝原因”一个主要动作。
回复、跟进和询问原因都先生成草稿，由用户查看或编辑后确认发送；不会自动发送简历或联系方式，也不会
自动确认 AI 草稿。发送使用 BOSS 的 `chat` MQTT/Protobuf 通道；`publish` 未抛错只展示为“已提交发送”，
不表示平台已经送达。多个 BOSS 沟通页通过账号级租约选出一个领导标签页，领导失效后递增 epoch 切换，
未知发送结果不会自动重试。

实现和限制见《自动代聊功能具体执行方案.md》，扩展安装与验证见
《boss-executor-extension/README.md》。

## 求职数据更新

桌面端“求职数据更新”按用户选择时间执行 Refresh Scope Discovery。列表来源支持智能选择、仅使用
本地列表和明确刷新 BOSS；智能模式在最近 `platform_synced_at` 不超过 30 分钟时复用本地数据，
否则复用 `capture_chat_friend_list → sync_friend_list`。Scope 将时间范围内完整会话
`sessions_in_scope` 与需要 history API 的 `sessions_to_sync` 分开保存，关联岗位、缺失 JD 和缺失评估
均从完整会话集合计算。页面显示实际来源、列表同步时间和 Scope 生成时间。

用户确认后，页面只在已有 Codex 会话处于 running 且可提交 Prompt 时以 `scope_id` 创建持久化
Refresh Run。Run 初始步骤为 `waiting_codex`；Prompt 成功写入当前 Codex 会话后进入聊天历史同步。
提交失败时可按原 `run_id` 重新提交或取消。Run Item 只从 Scope 复制，并继续复用
`sync_history_messages`、`prepare_chat_job` 和现有岗位详情采集流程。
Run 与 Item 状态可在页面关闭、Codex 中断或应用重启后恢复；恢复时只返回未完成和可重试 Item。
页面在 Run 运行期间每 2 秒读取持久化进度，进入终态或离开页面后停止。

数据补充完成后，若用户勾选沟通分析、缺失投递建议、回复草稿或跟进建议，Refresh Run 进入统一
Codex 分析链路。页面只提交一次任务 Prompt；Codex 在当前 CLI 会话中先调用一次
`finejob.prepare_job_hunt_refresh_analysis(run_id)`，由服务端完成确定性事实锚定和旧任务状态同步，
再按 `context_arguments` 调用 `finejob.get_job_hunt_refresh_analysis_item_context` 逐项读取聊天、
岗位、JD 和候选人上下文。所有已勾选 AI 结果在同一个 Codex CLI 会话内生成，并通过
`finejob.save_job_hunt_refresh_analysis` 保存；结果体积较大时允许分批保存，但不重新 prepare，
不把投递建议、回复草稿和跟进建议拆成多次独立 AI 分析。若 manifest 或单个 item 上下文过大，
服务返回明确 blocker，流程停止等待人工处理。

服务端以 Activity 作为求职事实流，并投影为正式 Pipeline。Pipeline 覆盖简历已查看、用人部门评估、
面试时间沟通、面试、Offer、拒绝和岗位关闭，同时保存 `waiting_on`、`contact_origin` 与结构化拒绝原因。
数据库升级会逐项检查正式 Activity 事件集合，把旧投递表中的 `offer`、`rejected`、`closed` 幂等回填为
Activity，并重建 Pipeline，保留历史事件和终态。

沟通来源优先使用 FineJob 自动打招呼或人工确认发送的动作证据；完整历史首条真实消息由招聘方发送时记为
招聘方主动，由候选人发送且缺少 FineJob 动作证据时记为外部主动，历史不完整时保持未知。系统附件消息可锚定
简历提交、接收和查看，招聘方明确表示“已招到人”记录为候选人被拒绝且原因为岗位已招满；只有岗位取消、
HC 关闭或停止招聘才关闭岗位。模糊表达会保留当前状态等待更多证据。

批量和单会话分析共用 Activity → Pipeline 闭环。单会话 Insight 以会话与分析版本覆盖最新结果，重复分析复用
同一 Insight，并按消息证据去重 Activity；单会话进展分析通过严格 JSON Schema 约束 Codex 输出。
需要回复、到期跟进或补充拒绝原因时，单会话分析会在同一流程中生成待确认草稿。消息草稿结合岗位评估、
已确认候选人资料与等待天数；页面同时保留常驻“生成消息”入口，允许用户在建议时间前主动生成。
自动识别拒绝后隐藏人工拒绝标记，原因仍不明确时可在聊天页或历史岗位页继续分析并生成原因询问。
跟进策略集中根据阶段与等待时长判断，结果保存在
`fj_chat_attention_states`。分析生成的草稿可进入待确认回复任务，保存分析本身不会创建发送动作。

岗位详情通过统一 Job Progress 视图展示阶段、等待对象、来源、最近进展、行动建议和求职结果，并可直接进入对应聊天。
求职数据更新完成后汇总等待招聘方、等待候选人、建议跟进、简历已查看、用人部门评估、拒绝和岗位关闭数量。

缺失投递建议写回复用正式 `fj_job_evaluations` 能力，并保留 JD 版本、候选人上下文修订和输出校验。
Refresh 的 evaluation 写回用于补资料，不触发投递、打招呼或发送动作，也不受策略冷却排除拦截。
Run 提供 `finejob.list_job_hunt_refresh_analysis_items` 查询分析 item 明细，可定位每个聊天或岗位的
保存、跳过、当前 evaluation 和原因。

## 当前状态

当前已完成求职资料 V2 主链路：

- 简历作为资料源之一，PDF、Markdown、手动文本和项目资料进入统一资料中心；DOCX 延后接入。
- 资料解析强制经过 OCR/文本识别与 AI 结构化输出，AI 草稿逐项确认后进入正式档案。
- 原子事实、证据、动态 QA、回答版本、简历版本、求职活动与搜索词均支持动态维护。
- `ProfileContextService` 为搜索、岗位评估和 HR 代聊统一生成经过确认与披露过滤的 Markdown 上下文。
- 旧简历接口保留迁移期兼容，本地旧数据通过预览和用户明确确认后迁移。

## 文档

- `docs/产品计划.md`
- `docs/架构设计.md`
- `docs/安全策略.md`
- `docs/Codex执行器适配计划.md`
- `docs/遗留代码参考.md`
- `求职资料功能重构方案.md`
- `Codex与FineJob业务能力集成执行方案.md`
- `自动代聊功能具体执行方案.md`
- `阶段二-a.md`
