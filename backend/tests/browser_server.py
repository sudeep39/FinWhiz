"""Explicit test-only server; never loaded by production startup."""

import socket
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from finwhiz.api import create_app
from finwhiz.config import Settings

if __name__ == "__main__":
    settings = Settings(
        _env_file=None,
        openai_api_key="offline",
        nebius_api_key="offline",
        pinecone_api_key="offline",
        app_password="",
        node_env="",
        replit_deployment="",
        ai_provider="openai",
        finwhiz_data_dir=Path(sys.argv[1]),
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    print(f"READY http://127.0.0.1:{sock.getsockname()[1]}", flush=True)
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings, disable_workers=True), log_level="warning")
    )
    server.run(sockets=[sock])
