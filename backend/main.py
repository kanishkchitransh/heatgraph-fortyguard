import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes.heatmap      import router as heatmap_router
from api.routes.entities     import router as entities_router
from api.routes.analysis     import router as analysis_router
from api.routes.solutions    import router as solutions_router
from api.routes.live_context import router as live_context_router
from api.routes.network      import router as network_router
from db.database import init_db

app = FastAPI(title="ImpactGraph API", version="0.2.0")

# CORS: allow localhost in dev + any Railway/production origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    from config import settings
    fg_ok = "✅" if settings.fortyguard_api_key else "❌ MISSING"
    gm_ok = "✅" if settings.gemini_api_key else "❌ MISSING — solutions will use fallback"
    print(f"FORTYGUARD_API_KEY: {fg_ok}  MOCK_MODE: {settings.mock_mode}")
    print(f"GEMINI_API_KEY:     {gm_ok}  MODEL: {settings.gemini_model}")
    init_db()
    # Auto-load NYC datasets if entities table is empty
    from db.database import SessionLocal, Entity
    db = SessionLocal()
    nyc_count = db.query(Entity).filter(Entity.city == "nyc").count()
    db.close()
    if nyc_count == 0:
        print("No NYC entities found — running data loader…")
        from data.load_all import load_all
        load_all()
    else:
        print(f"NYC entities: {nyc_count} loaded (startup skip)")


@app.get("/debug/config")
def debug_config():
    from config import settings
    import core.gemini_service as gs
    return {
        "gemini_key_set":    bool(settings.gemini_api_key),
        "gemini_key_prefix": settings.gemini_api_key[:10] if settings.gemini_api_key else "EMPTY",
        "gemini_model":      settings.gemini_model,
        "module_key_set":    bool(gs.GEMINI_API_KEY),
        "module_key_prefix": gs.GEMINI_API_KEY[:10] if gs.GEMINI_API_KEY else "EMPTY",
        "get_fn_key_set":    bool(gs._get_gemini_key()),
        "mock_mode":         settings.mock_mode,
    }


@app.get("/debug/registry")
def debug_registry():
    from core.factors import FACTOR_REGISTRY
    return {"registry_keys": list(FACTOR_REGISTRY.keys())}



@app.get("/api/data-source")
def data_source():
    """
    Returns info about the temperature data source — how many FortyGuard tiles are
    cached, whether they are real or mock, and total entity count.
    Used by the frontend to show a trust badge in the header.
    """
    import json
    from db.database import SessionLocal, ApiCache, Entity
    db = SessionLocal()
    try:
        tile_count  = 0
        cache_date  = None
        is_mock     = False

        for entry in db.query(ApiCache).all():
            try:
                data = json.loads(entry.response_json or "{}")
            except Exception:
                continue
            map_data = data.get("map_data")
            if not isinstance(map_data, dict):
                continue
            features = map_data.get("features", [])
            if features:
                tile_count = len(features)
                cache_date = str(entry.created_at)[:16] if entry.created_at else None
                is_mock    = bool(data.get("_mock"))
                break

        entity_count = db.query(Entity).filter(Entity.city == "nyc").count()
    finally:
        db.close()

    return {
        "temperature_source": "FortyGuard Large Temperature Model (LTM)",
        "resolution":         "2-meter ambient air temperature, 100m grid",
        "tile_count":         tile_count,
        "entity_count":       entity_count,
        "cached_at":          cache_date,
        "is_real_data":       tile_count > 100 and not is_mock,
        "is_mock":            is_mock,
    }


@app.get("/health")
def health():
    from db.database import SessionLocal, Entity
    db = SessionLocal()
    counts = {}
    for etype in ("construction_permit", "school", "nycha_development",
                  "subway_station", "hvi_zone"):
        counts[etype] = db.query(Entity).filter(
            Entity.city == "nyc", Entity.entity_type == etype
        ).count()
    db.close()
    return {"status": "ok", "entity_counts": counts}


app.include_router(heatmap_router)
app.include_router(entities_router)
app.include_router(analysis_router)
app.include_router(solutions_router)
app.include_router(live_context_router)
app.include_router(network_router)

# ── Static frontend (production) ─────────────────────────────────────────────
# Serve the built React app from frontend/dist/. The frontend must be built
# before deployment: cd frontend && npm run build
_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Catch-all: serve index.html for client-side routing."""
        index = os.path.join(_DIST, "index.html")
        return FileResponse(index)
