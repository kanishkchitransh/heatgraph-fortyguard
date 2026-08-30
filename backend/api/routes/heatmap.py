"""
Heatmap + satellite + heat-intelligence routes.

POST /api/heatmap           — FortyGuard temperature heatmap (tcm)
POST /api/heatmap/persistence — longest continuous run above threshold
POST /api/satellite         — land-cover segmentation (CORRECTED: lat/lon point)
POST /api/heat-intelligence — 5-category PDF report (CORRECTED: temp in °F, "date" key)
POST /api/env_params        — environmental parameters for a point
"""
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.fortyguard_client import FortyGuardClient
from core.mock_data import mock_heatmap_response
from config import settings
from db.database import get_db, ApiCache

router = APIRouter(prefix="/api", tags=["heatmap"])


# ── Request models ────────────────────────────────────────────────────────────

class HeatmapRequest(BaseModel):
    bbox:        list[float] = Field(..., min_length=4, max_length=4)
    datetime:    str         = Field(..., description="ISO 8601 UTC e.g. 2024-08-23T15:00:00Z")
    granularity: int         = Field(default=100)


class PersistenceRequest(BaseModel):
    bbox:        list[float] = Field(..., min_length=4, max_length=4)
    threshold_c: float       = Field(default=30.0,  description="Temperature threshold in °C")
    date_str:    str         = Field(default="2024-08-23")
    direction:   str         = Field(default="above")


class SatellitePointRequest(BaseModel):
    latitude:  float
    longitude: float
    date_str:  str = "2024-08-23"
    time_str:  str = "14:00"


class HeatIntelligenceRequest(BaseModel):
    latitude:      float
    longitude:     float
    temperature_f: float       = Field(..., description="Temperature in FAHRENHEIT from heatmap tile")
    date_str:      str         = "2024-08-23"
    analysis_types: list[str]  = ["geographic", "environmental", "urban", "events", "anthropogenic"]


class EnvParamsRequest(BaseModel):
    latitude:      float
    longitude:     float
    temperature_c: float = Field(..., description="Temperature in °C from heatmap tile")
    datetime:      str


# ── /api/heatmap ──────────────────────────────────────────────────────────────

@router.post("/heatmap")
async def get_heatmap(req: HeatmapRequest, db: Session = Depends(get_db)):
    min_lon, min_lat, max_lon, max_lat = req.bbox

    polygon_aoi = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lon, min_lat],
                    [min_lon, max_lat],
                    [max_lon, max_lat],
                    [max_lon, min_lat],
                    [min_lon, min_lat],
                ]],
            },
        }],
    }

    dt_parts   = req.datetime.rstrip("Z").split("T")
    date_part  = dt_parts[0]
    time_hhmm  = dt_parts[1][:5] if len(dt_parts) > 1 else "12:00"

    body = {
        "polygon_aoi":   polygon_aoi,
        "date_time": {
            "start_date": date_part,
            "start_time": time_hhmm,
            "filter_type": 1,
        },
        "granularity":   req.granularity,
        "analytic_type": "tcm",
    }

    if settings.mock_mode:
        return mock_heatmap_response(body)

    try:
        client = FortyGuardClient(db)
        result = await client.heatmap(body)
        if not result.get("_cached"):
            from core.tile_lookup import tile_service
            tile_service.reload()
        return result
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FortyGuard error: {e}")


# ── /api/heatmap/persistence ──────────────────────────────────────────────────

@router.post("/heatmap/persistence")
async def get_persistence_heatmap(req: PersistenceRequest, db: Session = Depends(get_db)):
    """
    Returns the longest continuous run of hours above a temperature threshold
    for each tile over the specified date.

    Critical use cases:
    - Transformers need nighttime cooling to recover. Areas never cooling below
      30°C have the highest failure risk (Con Edison IEEE C57.91 model).
    - School scheduling: which blocks stay above WBGT danger threshold all day?
    - Construction safety: identify safe-work windows.
    - HVI augmentation: persistence reveals intra-ZIP variation the static HVI misses.
    """
    min_lon, min_lat, max_lon, max_lat = req.bbox
    polygon_aoi = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lon, min_lat], [min_lon, max_lat],
                    [max_lon, max_lat], [max_lon, min_lat],
                    [min_lon, min_lat],
                ]],
            },
        }],
    }

    body = {
        "polygon_aoi":   polygon_aoi,
        "date_time": {
            "start_date": req.date_str,
            "start_time": "00:00",
            "filter_type": 3,          # Single Day — covers 00:00-23:59
        },
        "granularity":   100,
        "analytic_type": "persistence",
        "threshold":     req.threshold_c,
        "direction":     req.direction,
    }

    if settings.mock_mode:
        # Return mock tcm heatmap — persistence values would be 0-24 (hours)
        base = mock_heatmap_response(body)
        return base

    try:
        client = FortyGuardClient(db)
        return await client.heatmap(body)
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FortyGuard persistence error: {e}")


# ── /api/satellite — CORRECTED ────────────────────────────────────────────────

def _derive_heat_driver(segments: dict) -> str:
    """
    Plain-English explanation of heat factors based on satellite land cover.
    Handles both the casing variants the FortyGuard API returns:
    e.g. "tree" / "Trees" / "tree_canopy"; "building" / "Building" / "Skyscraper".
    """
    def _sum(*keys: str) -> float:
        total = 0.0
        for k in keys:
            # Case-insensitive partial match against segment keys
            for seg_k, v in segments.items():
                if k.lower() in seg_k.lower():
                    total += float(v)
                    break  # count each segment key once per target
        return round(total, 1)

    trees      = _sum("tree", "grass", "vegetation", "park")
    building   = _sum("building", "skyscraper", "roof")
    road       = _sum("road", "route", "sidewalk", "pavement", "asphalt")
    impervious = round(building + road, 1)

    if impervious > 80:
        return (
            f"Extreme impervious surface ({impervious:.0f}%). Almost no cooling "
            f"vegetation ({trees:.0f}% canopy). Urban heat island at maximum intensity."
        )
    elif trees > 50:
        return (
            f"High vegetation cover ({trees:.0f}%). Tree canopy significantly reduces "
            f"thermal stress at this location. Microclimate cooler than the tile average."
        )
    elif trees < 10:
        return (
            f"Very low vegetation ({trees:.0f}% canopy). High impervious surface "
            f"({impervious:.0f}%). Primary heat driver: lack of evapotranspiration."
        )
    elif building > 50:
        return (
            f"Dense building cover ({building:.0f}%) stores and re-radiates heat. "
            f"Tree cover {trees:.0f}% provides partial mitigation."
        )
    else:
        return (
            f"Mixed urban surface: {impervious:.0f}% impervious, "
            f"{trees:.0f}% vegetation. Moderate heat island effect."
        )


@router.post("/satellite")
async def get_satellite(req: SatellitePointRequest, db: Session = Depends(get_db)):
    """
    Satellite land-cover segmentation for a point location.
    CORRECTED: uses {"sat": {"latitude": ..., "longitude": ...}} NOT polygon_aoi.

    Returns:
      segments           — {ClassName: pct} coverage percentages
      original_image_b64 — Base64 satellite image ("orignal_image" API typo handled)
      segmented_image_b64— Base64 segmentation mask
      image_year         — year of source imagery
      heat_driver        — plain-English explanation of heat factors

    FortyGuard use case: Urban Design & Public Space.
    Dataset context: REPLACES the 2015 NYC Street Tree Census for canopy data.
    The satellite gives current (2024) land-cover including trees, buildings,
    roads, sidewalks — the tree census only had tree locations, not full cover.
    """
    cache_key  = f"satellite:{req.latitude:.4f}:{req.longitude:.4f}:{req.date_str}"
    cached_row = db.get(ApiCache, cache_key)
    if cached_row:
        data = json.loads(cached_row.response_json)
        data["_cached"] = True
        return data

    if settings.mock_mode:
        mock = {
            "_mock":    True,
            "segments": {
                "Building":           62.4,
                "Road_Route":         14.2,
                "Sidewalk_Pavement":   9.1,
                "Trees":               7.8,
                "Grass":               4.1,
                "Skyscraper":          2.4,
            },
            "original_image_b64":  None,
            "segmented_image_b64": None,
            "image_year":          2024,
            "heat_driver": (
                "High building density (62%) and low tree cover (8%) are the "
                "primary heat drivers at this location. Urban heat island sustained "
                "by 76% total impervious surface."
            ),
        }
        return mock

    try:
        client = FortyGuardClient(db)
        result = await client.satellite(
            lat=req.latitude,
            lon=req.longitude,
            date_str=req.date_str,
            time_str=req.time_str,
        )
        # Parse result: segments at result.segmentation.segments
        # API has a typo: "orignal_image" (not "original_image")
        seg_data = result.get("segmentation", {})
        segments = seg_data.get("segments", {})

        parsed = {
            "segments":           segments,
            "original_image_b64": (result.get("orignal_image") or [None])[0]
                                   if isinstance(result.get("orignal_image"), list)
                                   else result.get("orignal_image"),
            "segmented_image_b64": seg_data.get("image_content"),
            "image_year":          result.get("image_year"),
            "heat_driver":         _derive_heat_driver(segments),
        }

        db.merge(ApiCache(cache_key=cache_key, response_json=json.dumps(parsed)))
        db.commit()
        return parsed

    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FortyGuard satellite error: {e}")


# ── /api/heat-intelligence — CORRECTED ───────────────────────────────────────

@router.post("/heat-intelligence")
async def get_heat_intelligence(req: HeatIntelligenceRequest, db: Session = Depends(get_db)):
    """
    FortyGuard Heat Intelligence — 5-category contextual PDF report.

    CORRECTIONS:
    - temperature_f is in FAHRENHEIT (the request model enforces this)
    - API body uses "date" (plain string), NOT "date_time" object
    - Result is a temporary PDF download_link — client downloads immediately
    - Cached as Base64 PDF bytes (not the signed URL which expires)

    FortyGuard use case: Property & Asset Intelligence + Research & Climate Innovation.
    HOLC equity story: combine geographic (historical redlining) + environmental
    (current satellite) + urban (current temperature) analysis for 87-year narrative.
    """
    if settings.mock_mode:
        return {
            "_mock":      True,
            "pdf_base64": None,
            "summary": (
                "Mock: High urban density, low canopy (8%), active construction "
                "within 200m. Historically HOLC-graded 'Hazardous' — current "
                "thermal stress confirms 87-year land-use legacy."
            ),
        }

    try:
        client = FortyGuardClient(db)
        result = await client.heat_intelligence(
            lat=req.latitude,
            lon=req.longitude,
            temp_f=req.temperature_f,
            date_str=req.date_str,
            analysis_types=req.analysis_types,
        )
        return result
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FortyGuard heat intelligence error: {e}")


# ── /api/streetview ──────────────────────────────────────────────────────────

class StreetviewRequest(BaseModel):
    latitude:         float
    longitude:        float
    vertical_angle:   float = 10.0
    horizontal_angle: float = 90.0
    back_view:        bool  = False


@router.post("/streetview")
async def get_streetview(req: StreetviewRequest, db: Session = Depends(get_db)):
    """
    FortyGuard Street View Segmentation for a point location.
    Returns ground-level thermal/urban segmentation:
      front.original_image   — Base64 street photo
      front.segmented_image  — Base64 segmentation mask
      front.segments         — {ClassName: pct} coverage
      front.image_date       — date of street view capture

    Use case: Show what the entity actually looks like from street level and
    what thermal factors (roads, buildings, trees, sky) surround it.
    Adds the "Urban Design" and "Smart Mobility" FortyGuard use cases visually.
    """
    cache_key  = f"streetview:{req.latitude:.4f}:{req.longitude:.4f}"
    cached_row = db.get(ApiCache, cache_key)
    if cached_row:
        data = json.loads(cached_row.response_json)
        data["_cached"] = True
        return data

    if settings.mock_mode:
        return {
            "_mock": True,
            "front": {
                "original_image":  None,
                "segmented_image": None,
                "segments": {
                    "Building": 38.2, "Road": 22.4, "Sky": 18.1,
                    "Trees": 9.3, "Sidewalk": 7.6, "Other": 4.4,
                },
                "image_date": "2024-06-15",
            },
        }

    try:
        client = FortyGuardClient(db)
        result = await client.streetview(
            lat=req.latitude,
            lon=req.longitude,
            vertical_angle=req.vertical_angle,
            horizontal_angle=req.horizontal_angle,
            back_view=req.back_view,
        )
        db.merge(ApiCache(cache_key=cache_key, response_json=json.dumps(result)))
        db.commit()
        return result
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FortyGuard streetview error: {e}")


# ── /api/env_params (legacy raw-body endpoint) ───────────────────────────────

@router.post("/env_params")
async def get_env_params(req: EnvParamsRequest, db: Session = Depends(get_db)):
    dt_parts  = req.datetime.rstrip("Z").split("T")
    date_part = dt_parts[0]
    time_hhmm = dt_parts[1][:5] if len(dt_parts) > 1 else "12:00"
    body = {
        "latitude":    req.latitude,
        "longitude":   req.longitude,
        "temperature": req.temperature_c,
        "date_time": {
            "start_date": date_part,
            "start_time": time_hhmm,
            "filter_type": 1,
        },
    }
    try:
        client = FortyGuardClient(db)
        return await client.env_params(body)
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FortyGuard error: {e}")
