from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import load_config
from backend.app.db import Database
from backend.app.errors import register_error_handlers
from backend.app.routers.config import router as config_router
from backend.app.routers.fine_job.boss_capture import router as fine_job_boss_capture_router
from backend.app.routers.fine_job.codex import router as fine_job_codex_router
from backend.app.routers.fine_job.companies import router as fine_job_companies_router
from backend.app.routers.fine_job.boss_chat import router as fine_job_boss_chat_router
from backend.app.routers.fine_job.boss_executor import router as fine_job_boss_executor_router
from backend.app.routers.fine_job.boss_network_debug import router as fine_job_boss_network_debug_router
from backend.app.routers.fine_job.delivery_runs import router as fine_job_delivery_runs_router
from backend.app.routers.fine_job.delivery_strategies import router as fine_job_delivery_strategies_router
from backend.app.routers.fine_job.job_intents import router as fine_job_intents_router
from backend.app.routers.fine_job.job_journey import router as fine_job_job_journey_router
from backend.app.routers.fine_job.job_actions import router as fine_job_job_actions_router
from backend.app.routers.fine_job.job_hunt_analytics import router as fine_job_job_hunt_analytics_router
from backend.app.routers.fine_job.job_hunt_refresh import router as fine_job_job_hunt_refresh_router
from backend.app.routers.fine_job.platform_sessions import router as fine_job_platform_sessions_router
from backend.app.routers.fine_job.profiles import router as fine_job_profiles_router
from backend.app.routers.fine_job.profiles_v3 import router as fine_job_profiles_v3_router
from backend.app.routers.fine_job.resumes import router as fine_job_resumes_router
from backend.app.routers.fine_job.strategies import router as fine_job_strategies_router
from backend.app.routers.fine_job.workflow import router as fine_job_workflow_router
from backend.app.routers.health import router as health_router
from backend.app.routers.internal_codex import router as internal_codex_router
from backend.app.routers.parse_results import router as parse_results_router
from backend.app.routers.pdf_drafts import router as pdf_drafts_router
from backend.app.routers.pdf_parse import router as pdf_parse_router
from backend.app.routers.quick_capture import router as quick_capture_router
from backend.app.routers.qa import router as qa_router
from backend.app.routers.web_drafts import router as web_drafts_router
from backend.app.routers.web_session_profiles import router as web_session_profiles_router
from backend.app.routers.pool import router as pool_router
from backend.app.routers.retrieval import router as retrieval_router
from backend.app.routers.report import report_router, reports_router
from backend.app.routers.results import router as results_router
from backend.app.routers.runs import router as runs_router
from backend.app.routers.summary import router as summary_router
from backend.app.services.pdf_draft_store import PdfDraftStore
from backend.app.services.pdf_reparse_job_store import PdfReparseJobStore
from backend.app.services.web_draft_store import WebDraftStore
from backend.app.services.web_reparse_job_store import WebReparseJobStore
from backend.app.services.web_session_profiles import WebSessionProfileStore
from backend.app.services.fine_job import boss_executor
from backend.app.services.fine_job.codex_runtime import CodexRuntimeRegistry
from backend.app.services.fine_job.profile_store import ensure_default_profile


def create_app() -> FastAPI:
    config = load_config()
    config.app_data_dir.mkdir(parents=True, exist_ok=True)
    config.output_root.mkdir(parents=True, exist_ok=True)
    config.summary_output_dir.mkdir(parents=True, exist_ok=True)
    config.report_output_dir.mkdir(parents=True, exist_ok=True)
    config.qdrant_path.mkdir(parents=True, exist_ok=True)

    db = Database(config.sqlite_path)
    db.initialize()
    # 后端启动后等待插件主动完成一次心跳测试，再恢复执行器连接状态。
    boss_executor.reset_executor_connections(db)
    ensure_default_profile(db)

    app = FastAPI(title="Knowledge Curator Backend")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["null"],
        allow_origin_regex=r"^(https?://(127\.0\.0\.1|localhost)(:\d+)?|chrome-extension://[a-z0-9-]+)$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.config = config
    app.state.db = db
    app.state.pdf_draft_store = PdfDraftStore()
    app.state.pdf_reparse_job_store = PdfReparseJobStore()
    app.state.web_draft_store = WebDraftStore()
    app.state.web_reparse_job_store = WebReparseJobStore()
    app.state.web_session_profile_store = WebSessionProfileStore(
        config.app_data_dir / "web-session-profiles.json"
    )
    app.state.codex_runtime_registry = CodexRuntimeRegistry()

    register_error_handlers(app)

    app.include_router(health_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(fine_job_boss_capture_router, prefix="/api")
    app.include_router(fine_job_codex_router, prefix="/api")
    app.include_router(fine_job_companies_router, prefix="/api")
    app.include_router(fine_job_boss_chat_router, prefix="/api")
    app.include_router(fine_job_boss_executor_router, prefix="/api")
    app.include_router(fine_job_boss_network_debug_router, prefix="/api")
    app.include_router(fine_job_delivery_runs_router, prefix="/api")
    app.include_router(fine_job_delivery_strategies_router, prefix="/api")
    app.include_router(fine_job_intents_router, prefix="/api")
    app.include_router(fine_job_job_journey_router, prefix="/api")
    app.include_router(fine_job_job_actions_router, prefix="/api")
    app.include_router(fine_job_job_hunt_analytics_router, prefix="/api")
    app.include_router(fine_job_job_hunt_refresh_router, prefix="/api")
    app.include_router(fine_job_platform_sessions_router, prefix="/api")
    app.include_router(fine_job_profiles_router, prefix="/api")
    app.include_router(fine_job_profiles_v3_router, prefix="/api")
    app.include_router(fine_job_resumes_router, prefix="/api")
    app.include_router(fine_job_strategies_router, prefix="/api")
    app.include_router(fine_job_workflow_router, prefix="/api")
    app.include_router(parse_results_router, prefix="/api")
    app.include_router(pdf_drafts_router, prefix="/api")
    app.include_router(web_drafts_router, prefix="/api")
    app.include_router(web_session_profiles_router, prefix="/api")
    app.include_router(pdf_parse_router, prefix="/api")
    app.include_router(quick_capture_router, prefix="/api")
    app.include_router(qa_router, prefix="/api")
    app.include_router(pool_router, prefix="/api")
    app.include_router(retrieval_router, prefix="/api")
    app.include_router(summary_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(report_router, prefix="/api")
    app.include_router(reports_router, prefix="/api")
    app.include_router(results_router, prefix="/api")
    app.include_router(internal_codex_router, prefix="/api")

    return app
