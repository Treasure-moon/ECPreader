"""FastAPI 应用：仅监听 127.0.0.1，带会话令牌与安全响应头。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, security
from .api import router

TOKEN = security.session_token()
_VERSION = config.APP_VERSION


def create_app() -> FastAPI:
    config.ensure_dirs()
    app = FastAPI(
        title=config.APP_NAME,
        version=_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(router)

    assets = Path(config.WEB_DIR) / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if path.startswith("/api/"):
            token = request.headers.get("x-ecp-token") or request.query_params.get("token", "")
            if not token or token != TOKEN:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = (
            "private, max-age=86400" if "/pages/" in path and response.status_code == 200 else "no-store"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self' data:"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = Path(config.WEB_DIR) / "index.html"
        html = html_path.read_text(encoding="utf-8")
        html = html.replace("__ECP_TOKEN__", TOKEN).replace("__ECP_VERSION__", _VERSION)
        return HTMLResponse(html)

    @app.get("/favicon.ico")
    async def favicon() -> JSONResponse:  # 浏览器请求时避免 404 噪音
        return JSONResponse({"ok": True})

    return app


app = create_app()