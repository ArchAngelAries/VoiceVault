"""
VoiceVault Backend — FastAPI main entry point
Run with: uvicorn main:app --reload --port 7763
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import logging
import os

from api.health      import router as health_router
from api.hardware    import router as hardware_router
from api.settings    import router as settings_router
from api.discover    import router as discover_router
from api.collections import router as collections_router
from api.tiers       import router as tiers_router
from api.tts         import router as tts_router, start_background_discovery
from api.asr         import router as asr_router
from api.converse    import router as converse_router
from api.harvest     import router as harvest_router, startup_harvest_jobs, shutdown_harvest_jobs
from api.train       import router as train_router, shutdown_training_jobs
from api.music       import router as music_router, shutdown_music
from api.voice       import router as voice_router
from api.library import router as library_router
from api.models_manager import router as models_router
from api.jobs import router as jobs_router
from db.client       import ensure_defaults, disconnect, get_db
from db.migrate      import run_pending
from core.paths      import backup_dir, database_path, STATE_DIR
from core.discovery_cache import shutdown_discovery_refreshes
from core.discovery_retention import sync_saved_entities

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_harvest_jobs()
    # Schema migrations run before Prisma connects. A failed migration stops startup on purpose:
    # running against an unknown schema is worse than not starting.
    db_path = database_path()
    if STATE_DIR is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    report = run_pending(db_path, backup_dir())
    if report["applied"]:
        logging.info(f"Applied database migrations {report['applied']} (backup: {report['backup']}).")
    try:
        await ensure_defaults()
        logging.info("Database ready.")
        # Protect pre-existing library records before optional browse caching can
        # evict their catalog metadata. This sidecar never migrates the library.
        await sync_saved_entities(await get_db())
    except Exception as e:
        logging.warning(f"DB startup warning (will retry on first request): {e}")
    # Warm the TTS model registry off the event loop (torch import + discovery take seconds),
    # so the first Generate visit is fast. Skipped under pytest, where tests stub the services.
    if not os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("VOICEVAULT_NO_BACKGROUND_DISCOVERY"):
        start_background_discovery()
    try:
        yield
    finally:
        try:
            for shutdown in (shutdown_discovery_refreshes, shutdown_harvest_jobs, shutdown_training_jobs, shutdown_music):
                try:
                    await shutdown()
                except Exception:
                    logging.exception("Shutdown cleanup failed: %s", shutdown.__name__)
        finally:
            await disconnect()


app = FastAPI(
    title="VoiceVault API",
    description="VoiceVault local backend — voice harvesting, TTS, training, and AI chat",
    version="0.1.1",
    lifespan=lifespan,
)

@app.exception_handler(httpx.HTTPStatusError)
async def upstream_status_error(request, exc: httpx.HTTPStatusError):
    # Registered as handlers (not left to a 500) so the response still passes through CORS.
    return JSONResponse(status_code=502, content={
        "detail": f"An external service returned HTTP {exc.response.status_code}. Try again later; if it keeps happening, check the API keys in Settings."})


@app.exception_handler(httpx.RequestError)
async def upstream_unreachable(request, exc: httpx.RequestError):
    return JSONResponse(status_code=502, content={
        "detail": "Could not reach an external service. Check your internet connection."})


@app.exception_handler(ValueError)
async def value_error(request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# Only answer requests addressed to this PC by name. A web page that re-points its own domain at
# 127.0.0.1 (DNS rebinding) sends its domain as Host and is refused. VOICEVAULT_ALLOWED_HOSTS adds names.
_extra_hosts = [h.strip() for h in os.environ.get("VOICEVAULT_ALLOWED_HOSTS", "").split(",") if h.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "::1", *_extra_hosts])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5179", "http://127.0.0.1:5179", "http://localhost:3000", "app://.", "file://"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-VoiceVault-Snapshot", "Content-Disposition", "X-Measured-Loudness"],
)

app.include_router(health_router,       prefix="/api/health",       tags=["health"])
app.include_router(hardware_router,     prefix="/api/hardware",     tags=["hardware"])
app.include_router(settings_router,     prefix="/api/settings",     tags=["settings"])
app.include_router(discover_router,     prefix="/api/discover",     tags=["discover"])
app.include_router(collections_router,  prefix="/api/collections",  tags=["collections"])
app.include_router(tiers_router,        prefix="/api/tiers",        tags=["tiers"])
app.include_router(tts_router,          prefix="/api/tts",          tags=["tts"])
app.include_router(asr_router,          prefix="/api/asr",          tags=["asr"])
app.include_router(converse_router,     prefix="/api/converse",     tags=["converse"])
app.include_router(harvest_router,      prefix="/api/harvest",      tags=["harvest"])
app.include_router(train_router,        prefix="/api/train",        tags=["train"])
app.include_router(music_router,        prefix="/api/music",        tags=["music"])
app.include_router(voice_router,        prefix="/api/voice",        tags=["voice"])
app.include_router(library_router,      prefix="/api/library",      tags=["library"])
app.include_router(models_router,       prefix="/api/models",       tags=["models"])
app.include_router(jobs_router,         prefix="/api/jobs",         tags=["jobs"])


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=7763, reload=True)
