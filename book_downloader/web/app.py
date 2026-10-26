"""Same-origin HTTP interface; all long-running work belongs to TaskManager."""

from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from threading import Thread
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .tasks import BookBusyError, Settings, TaskManager
from .extension_api import install_browser_routes


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["search", "download", "update", "fill_gaps", "merge"]
    url: str = ""
    query: str = ""
    force_browser: bool = False


class RetryRequest(BaseModel):
    force_browser: bool = False
    use_current_settings: bool = False


class ShutdownRequest(BaseModel):
    confirm_stop: bool = False


class RemoveBookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    delete_files: bool = False


def create_app(root=None, *, manager=None, on_shutdown=None):
    manager = manager or TaskManager(Path(root or Path.cwd()))

    @asynccontextmanager
    async def lifespan(app):
        manager.start()
        yield
        manager.stop()
        # HTTP and navigation requests are bounded by their configured timeouts.
        manager.thread.join()

    app = FastAPI(title="书页 · 本地下载", lifespan=lifespan)
    app.state.manager = manager
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            extension_request = (request.url.path.startswith("/api/browser/extension/")
                                 and origin and origin.startswith("chrome-extension://")
                                 and manager.browser_bridge.authorized(request.headers.get("authorization")))
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/") and not extension_request:
                return JSONResponse({"detail": "请从本地页面操作"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "操作请求需要 JSON 格式"}, status_code=415)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": "找不到对应的书目或任务"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(BookBusyError)
    async def book_busy(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(OSError)
    async def storage_error(request, error):
        logging.getLogger(__name__).exception("Web state storage failed")
        return JSONResponse({"detail": "本地记录或文件无法读写，请检查目录权限及后台日志。"}, status_code=500)

    @app.get("/api/health")
    def health():
        return {"app": "book-downloader-web", "version": 1, "root": str(manager.root), "stopping": manager.shutdown_event.is_set()}

    @app.get("/api/settings")
    def settings():
        with manager.lock:
            return manager.settings.model_dump()

    @app.put("/api/settings")
    def save_settings(settings: Settings):
        return manager.update_settings(settings.model_dump())

    @app.get("/api/books")
    def books():
        rows = manager.books()
        return JSONResponse(rows, headers={"X-Book-Downloader-Warnings": json.dumps(manager.notices[:5], ensure_ascii=True)})

    @app.get("/api/tasks")
    def tasks():
        return manager.list_tasks()

    @app.get("/api/books/{identifier}/removal")
    def removal_preview(identifier: str):
        return manager.removal_preview(identifier)

    @app.post("/api/books/{identifier}/remove")
    def remove_book(identifier: str, options: RemoveBookRequest):
        return manager.remove_book(identifier, **options.model_dump())

    @app.post("/api/tasks", status_code=202)
    def submit(task: TaskRequest):
        return manager.submit(**task.model_dump())

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str):
        return manager.get(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str):
        return manager.cancel(task_id)

    @app.post("/api/tasks/{task_id}/browser")
    def browser(task_id: str):
        return manager.approve_browser(task_id)

    @app.post("/api/tasks/{task_id}/focus")
    def focus_browser(task_id: str):
        return manager.focus_browser(task_id)

    @app.post("/api/tasks/{task_id}/retry", status_code=202)
    def retry(task_id: str, options: RetryRequest):
        return manager.retry(task_id, options.force_browser, options.use_current_settings)

    @app.get("/api/tasks/{task_id}/file")
    def task_file(task_id: str):
        with manager.lock:
            path = manager.task_file(task_id)
        if path is None:
            raise HTTPException(404, "该任务没有可获取的 TXT")
        return FileResponse(path, filename=path.name, media_type="text/plain; charset=utf-8")

    @app.get("/api/books/{identifier}/file")
    def book_file(identifier: str):
        with manager.lock:
            path = manager.book_file(identifier)
        if path is None:
            raise HTTPException(404, "该书目没有可获取的 TXT，请先重新生成")
        return FileResponse(path, filename=path.name, media_type="text/plain; charset=utf-8")

    @app.post("/api/shutdown")
    def shutdown(options: ShutdownRequest):
        if not options.confirm_stop:
            raise HTTPException(409, "请先确认停止任务并退出后台")
        manager.stop()
        if on_shutdown:
            def finish():
                manager.thread.join()
                on_shutdown()
            Thread(target=finish, name="book-web-shutdown", daemon=True).start()
        return {"message": "后台正在停止任务，已完成缓存将保留"}

    install_browser_routes(app, manager)
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
    return app
