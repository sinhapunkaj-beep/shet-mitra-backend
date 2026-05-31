import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.mandi import router as mandi_router

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# APScheduler lifecycle. Runs the trader jobs + the daily Supabase keepalive
# ping at 03:00 IST (prevents free-tier auto-pause). Defined here so we can
# pass it to the FastAPI constructor below.
# ---------------------------------------------------------------------------
_scheduler = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _scheduler
    # --- startup ---
    try:
        from pipelines.scheduler import build_scheduler
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler not started (import failed): %s", exc)
    else:
        try:
            _scheduler = build_scheduler()
            _scheduler.start()
            jobs = [j.id for j in _scheduler.get_jobs()]
            logger.info("APScheduler started with jobs: %s", jobs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler not started (build/start failed): %s", exc)
            _scheduler = None

    try:
        yield
    finally:
        # --- shutdown ---
        if _scheduler is not None:
            try:
                _scheduler.shutdown(wait=False)
                logger.info("APScheduler stopped")
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler shutdown noisy: %s", exc)
            _scheduler = None


app = FastAPI(
    title="ShetMitra API",
    version="2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(mandi_router, prefix="/api")


# ---------------------------------------------------------------------------
# Variety-collection routers (Agents 2 + 3). Each import is guarded so a
# partially deployed swarm does not break startup — if Agent 2 has not yet
# landed ``api/webhooks_variety.py`` we log a warning and continue.
# ---------------------------------------------------------------------------

try:
    from routes.internal import router as internal_router
    app.include_router(internal_router)
except ImportError as exc:
    logger.warning("routes.internal not mounted: %s", exc)

try:
    from routes.models import router as models_router
    app.include_router(models_router)
except ImportError as exc:
    logger.warning("routes.models not mounted: %s", exc)

try:
    from routes.trader import router as trader_router
    app.include_router(trader_router)
except ImportError as exc:
    logger.warning("trader_router not mounted: %s", exc)

try:
    from api.webhooks_variety import router as variety_router
    app.include_router(variety_router)
except ImportError as exc:
    logger.warning("api.webhooks_variety not mounted: %s", exc)

try:
    from api.webhooks_harvest import router as harvest_router
    app.include_router(harvest_router)
except ImportError as exc:
    logger.warning("api.webhooks_harvest not mounted: %s", exc)

try:
    from api.webhooks_aisensy import router as aisensy_router
    app.include_router(aisensy_router)
except ImportError as exc:
    logger.warning("api.webhooks_aisensy not mounted: %s", exc)

try:
    from api.webhooks_booking import router as booking_router
    app.include_router(booking_router)
except ImportError as exc:
    logger.warning("api.webhooks_booking not mounted: %s", exc)


@app.get("/")
def home():
    return {"message": "🚀 ShetMitra Running"}


@app.get("/health")
def health():
    return {"status": "ok"}
