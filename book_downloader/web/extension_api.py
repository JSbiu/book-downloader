"""Pair a locally installed extension without exposing the rest of the API."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field


class BrowserReply(BaseModel):
    id: str = Field(max_length=64)
    client: str = Field(max_length=80)
    result: dict | None = None
    error: str | None = Field(default=None, max_length=3000)


def install_browser_routes(app, manager):
    bridge = manager.browser_bridge

    def require_extension(request: Request):
        if not bridge.authorized(request.headers.get("authorization")):
            raise HTTPException(401, "浏览器连接已失效，请刷新本地书页重新连接扩展。")

    router = APIRouter(prefix="/api/browser/extension", dependencies=[Depends(require_extension)])

    @router.get("/work")
    def work(client: str, ready: bool = True, permissions: bool | None = None, version: str = ""):
        if not client or len(client) > 80:
            raise HTTPException(400, "无效浏览器连接")
        try:
            return bridge.poll(client, ready, permissions, version[:32])
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/reply")
    def reply(value: BrowserReply):
        if not bridge.complete(value.id, value.client, value.result, value.error):
            raise HTTPException(410, "该浏览器请求已停止或超时")
        return {"ok": True}

    app.include_router(router)

    @app.get("/api/browser/pairing")
    def pairing(request: Request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "请从本地书页连接扩展")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "请从本地书页连接扩展")
        return JSONResponse({"token": bridge.token}, headers={"Cache-Control": "no-store"})

    @app.get("/api/browser/status")
    def browser_status():
        return {**bridge.status(), "mode": manager.settings.browser_mode}

    @app.get("/api/browser/download")
    def extension_download():
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            for path in sorted((Path(__file__).parent / "extension").iterdir()):
                if path.is_file():
                    archive.write(path, path.name)
        return Response(buffer.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="book-downloader-browser.zip"'})
