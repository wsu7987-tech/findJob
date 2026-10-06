from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mcp", action="store_true")
    args, _ = parser.parse_known_args()

    if args.mcp:
        # MCP 模式使用标准输入输出承载 Codex 协议。
        from backend.app.mcp.fine_job_server import main as run_mcp

        run_mcp()
        return

    # 服务模式直接启动 FastAPI 工厂，避免便携包依赖外部 Python 命令。
    from uvicorn import run

    from backend.app.main import create_app

    run(
        create_app,
        factory=True,
        host="127.0.0.1",
        port=int(os.environ.get("KNOWLEDGE_CURATOR_BACKEND_PORT", "8000")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
