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
