import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, get_settings
from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.logging_config import RequestLoggingMiddleware, cleanup_old_logs, log_cleanup_loop, setup_logging
from app.routers import accounts, api_pools, audit, auth, import_batches, imports, settings, sub2api_sites, sync, todo_items, users
from app.services.bootstrap import ensure_bootstrap_data, ensure_indexes
from app.services.sub2api_cache import refresh_scheduler_loop


settings_obj = get_settings()
setup_logging(settings_obj)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("app_starting env=%s log_profile=%s", settings_obj.app_env, settings_obj.log_profile)
    removed_logs = cleanup_old_logs(settings_obj)
    if removed_logs:
        logger.info("old_logs_removed count=%s", removed_logs)
    await connect_to_mongo()
    db = get_db()
    await ensure_indexes(db)
    await ensure_bootstrap_data(db)
    refresh_task = asyncio.create_task(refresh_scheduler_loop(db))
    cleanup_task = asyncio.create_task(log_cleanup_loop(settings_obj))
    try:
        logger.info("app_started")
        yield
    finally:
        logger.info("app_stopping")
        for task in (refresh_task, cleanup_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    await close_mongo_connection()
    logger.info("app_stopped")


app = FastAPI(title=settings_obj.app_name, lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware, settings=settings_obj)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings_obj.frontend_origin] if settings_obj.frontend_origin else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")
app.include_router(import_batches.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(api_pools.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(sub2api_sites.router, prefix="/api")
app.include_router(todo_items.router, prefix="/api")
app.include_router(audit.router, prefix="/api")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


frontend_dist = PROJECT_ROOT / "frontend" / "dist"
frontend_assets = frontend_dist / "assets"
frontend_index = frontend_dist / "index.html"

app.mount("/assets", StaticFiles(directory=frontend_assets, check_dir=False), name="frontend-assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API route not found")
    if not frontend_index.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Frontend build not found")
    return FileResponse(frontend_index)
