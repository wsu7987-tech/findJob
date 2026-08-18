# FineJob

FineJob 是一个本地化的 AI 求职驾驶舱，目标是帮助用户管理简历、求职目标、岗位筛选、HR 沟通和待处理事项，并在用户授权下执行可控的浏览器自动化。

当前项目基于原 KnowledgeCurator 技术底座改造，保留可复用的 OCR、PDF 解析、Playwright、FastAPI、LangGraph、SQLite、LLM Provider 等能力；前端产品层会重做为求职场景。

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

然后在桌面端“FineJob 配置 → 智能执行器”中选择“本机 Codex CLI”，可按需填写模型和推理强度；留空时跟随 Codex 默认值。保存后点击“检测 Codex CLI”。

Codex 生成任务使用临时目录、只读沙箱、JSONL 事件和 JSON Schema 结果约束。FineJob 不读取 Codex 登录凭据，最终业务结果及运行元数据仍保存在 FineJob 中。

## 内置 BOSS CDP 采集模块

FineJob 在 `backend/app/services/fine_job/boss_scraper/` 内置 BOSS CDP 采集能力。核心实现基于
`eatmoreduck/boss-zhipin-scraper` 2.2.0（基线提交
`2bc40f56a3ca3249ce3b98cdda0187e0bd612aa5`，MIT），保留核心脚本结构，并通过
`service.py` 向 FineJob 其他模块提供稳定调用入口。

FineJob 已将平台登录、岗位采集页和投递准备中的真实采集统一切换到该 CDP 服务。桌面端
“岗位采集”支持打开专用 Chrome、定位搜索页，以及“当前页有效时采集当前页、否则自动导航”
两种操作路径。城市选择来自内置采集器的完整码表；长时间采集通过后台任务显示列表页、岗位详情、
当前岗位和预计剩余时间。详情既可在列表后自动采集全部，也可由用户手工选择、按投递策略或 AI
建议后再采集，并在右侧抽屉查看完整 JD、标签、推荐理由和来源信息。
采集结果会写入 SQLite：同一岗位按 BOSS 岗位标识去重，重复采集时更新最后采集时间和采集次数。
桌面端“历史采集”作为与“岗位采集”同级的独立页面，支持关键词、城市、公司规模、详情状态、
重复状态和日期范围筛选，并支持白名单字段排序和分页。当前批次列表会标记新岗位/历史岗位，
展示公司规模并使用固定高度滚动表格；成功完成后进度卡自动收起，失败状态继续保留。
历史页通过可排序列标题切换服务端排序；历史页和当前岗位采集列表都支持单岗“采集详情/重新采集详情”。
历史岗位单独刷新详情不会增加岗位采集次数。
旧 Puppeteer 登录和采集文件仍保留用于迁移期对照，但正式业务入口不再调用；
当前仍不提供自动打招呼、真实投递或“最新发布”专用逻辑。
调试 CLI 可直接从 FineJob 环境运行：

```powershell
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --check
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --setup-chrome
uv run python -m backend.app.services.fine_job.boss_scraper.boss_cdp_raw --keyword "AI Agent" --city 上海 --pages 1
```

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
