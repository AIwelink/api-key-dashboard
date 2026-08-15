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
from app.routers import accounts, agent, api_pools, api_tokens, audit, auth, auto_replenishment, client_metrics, client_sites, event_records, growth, import_batches, imports, notifications, operations, plus_self_produced, presence, settings, sub2api_sites, sync, todo_items, users, work_plans
from app.modules.client_metrics.sampler import client_metric_sampler_loop
from app.modules.operations.sync import operations_sync_loop
from app.modules.system.bootstrap import ensure_bootstrap_data, ensure_indexes
from app.modules.agent.scheduler import start_agent_scheduler, stop_agent_scheduler
from app.modules.sub2api.account_probe import probe_scheduler_loop
from app.modules.sub2api.account_test_scheduler import account_test_scheduler_loop
from app.modules.sub2api.cache import refresh_account_caches_for_all_sites, refresh_scheduler_loop
from app.modules.sub2api.capacity_sampler import capacity_sampler_loop
from app.modules.sub2api.hourly_forecast_evaluation_service import forecast_accuracy_evaluator_loop
from app.modules.sub2api.plus_self_produced import scheduler_loop as plus_self_produced_scheduler_loop
from app.modules.sub2api.tpm_sampler import tpm_sampler_loop


settings_obj = get_settings()
setup_logging(settings_obj)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    logger.info("app_starting env=%s log_profile=%s", settings_obj.app_env, settings_obj.log_profile)
    removed_logs = cleanup_old_logs(settings_obj)
    if removed_logs:
        logger.info("old_logs_removed count=%s", removed_logs)
    await connect_to_mongo()
    db = get_db()
    await ensure_indexes(db)
    await ensure_bootstrap_data(db)
    app_instance.state.agent_scheduler_db = db
    account_cache_startup_task = asyncio.create_task(refresh_account_caches_for_all_sites(db))
    refresh_task = asyncio.create_task(refresh_scheduler_loop(db))
    account_probe_task = asyncio.create_task(probe_scheduler_loop(db))
    account_test_task = asyncio.create_task(account_test_scheduler_loop(db))
    plus_self_produced_task = asyncio.create_task(plus_self_produced_scheduler_loop(db))
    tpm_sampler_task = asyncio.create_task(tpm_sampler_loop(db))
    capacity_sampler_task = asyncio.create_task(capacity_sampler_loop(db))
    forecast_accuracy_task = asyncio.create_task(forecast_accuracy_evaluator_loop(db))
    client_metric_sampler_task = asyncio.create_task(client_metric_sampler_loop(db))
    operations_sync_task = asyncio.create_task(operations_sync_loop(db))
    cleanup_task = asyncio.create_task(log_cleanup_loop(settings_obj))
    await start_agent_scheduler(app_instance)
    try:
        logger.info("app_started")
        yield
    finally:
        logger.info("app_stopping")
        await stop_agent_scheduler(app_instance)
        background_tasks = (
            account_cache_startup_task,
            refresh_task,
            account_probe_task,
            account_test_task,
            plus_self_produced_task,
            tpm_sampler_task,
            capacity_sampler_task,
            forecast_accuracy_task,
            client_metric_sampler_task,
            operations_sync_task,
            cleanup_task,
        )
        for task in background_tasks:
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
app.include_router(presence.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")
app.include_router(import_batches.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(api_pools.router, prefix="/api")
app.include_router(plus_self_produced.router, prefix="/api")
app.include_router(api_tokens.router, prefix="/api")
app.include_router(auto_replenishment.router, prefix="/api")
app.include_router(client_sites.router, prefix="/api")
app.include_router(client_metrics.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(event_records.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(growth.router, prefix="/api")
app.include_router(operations.router, prefix="/api")
app.include_router(sub2api_sites.router, prefix="/api")
app.include_router(todo_items.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(work_plans.router, prefix="/api")


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


#python -m uv --directory backend run uvicorn app.main:app --reload
