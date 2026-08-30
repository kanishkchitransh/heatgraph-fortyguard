"""
POST /api/analysis — Factor Graph computation endpoint.

1. Loads entities for a city (optionally filtered by viewport bbox)
2. For each entity, gets FullEnvParams (temperature + WBGT + AQI + PM2.5 + solar)
   OPTION B: checks ApiCache for real FortyGuard env_params first; falls back to estimates
3. Runs the entity-type-specific factor function to compute risk score
4. Detects compound risks (emitter-receptor pairs sharing a thermal zone)
5. Returns everything ranked by severity

This is where the Forney factor graph earns its keep: two entities from
different departments, sharing the same temperature edge, producing a
cross-silo insight neither agency can see alone.
"""
import json as _json
import math
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from db.database import Entity, get_db, ApiCache
from core.tile_lookup import tile_service, _estimate_env_params_from_tile, TileTemperature
from core.factors import FACTOR_REGISTRY, FactorResult
from core.compound_risk import find_compound_risks, haversine_m
from core.env_params_parser import parse_env_params_response

router = APIRouter(prefix="/api", tags=["analysis"])


class AnalysisRequest(BaseModel):
    city:               str   = "nyc"
    bbox:               Optional[str] = None   # "min_lon,min_lat,max_lon,max_lat"
    max_entities:       int   = 2000
    compound_radius_m:  float = 500.0
    min_receptor_score: float = 20.0


def _entity_to_dict(r: FactorResult) -> dict:
    return {
        "entity_id":    r.entity_id,
        "entity_name":  r.entity_name,
        "entity_type":  r.entity_type,
        "role":         r.role,
        "risk_score":   r.risk_score,
        "temperature_c": r.temperature_c,
        "temperature_f": r.temperature_f,
        "metric_name":  r.metric_name,
        "metric_value": r.metric_value,
        "explanation":  r.explanation,
        "data_source":  r.data_source,
        "department":   r.department,
        "lat":          r.lat,
        "lon":          r.lon,
        # Enhanced FortyGuard environmental parameters
        "wbgt_f":           r.wbgt_f,
        "heat_index_f":     r.heat_index_f,
        "humidity":         r.humidity,
        "aqi":              r.aqi,
        "pm25":             r.pm25,
        "solar_irradiance": r.solar_irradiance,
        "no2":              r.no2,
        "env_params_source": r.env_params_source,
        # Alias used by frontend dot indicator (green = real, gray = estimated)
        "env_source": r.env_params_source,
    }


@router.post("/analysis")
def run_analysis(req: AnalysisRequest, db: Session = Depends(get_db)):
    # ── 1. Load entities ──────────────────────────────────────────────────────
    q = db.query(Entity).filter(Entity.city == req.city.lower())

    if req.bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = (float(x) for x in req.bbox.split(","))
            q = q.filter(
                Entity.lat >= min_lat, Entity.lat <= max_lat,
                Entity.lon >= min_lon, Entity.lon <= max_lon,
            )
        except (ValueError, IndexError):
            pass   # bad bbox → ignore filter

    entities = q.limit(req.max_entities).all()

    # ── 2. Run factor functions with FullEnvParams ────────────────────────────
    factor_results: list[FactorResult] = []
    skipped_no_tile   = 0
    skipped_no_factor = 0

    for entity in entities:
        # Get FortyGuard tile temperature (required — skip if no coverage)
        tile = tile_service.get_temperature(entity.lat, entity.lon)
        if tile is None:
            skipped_no_tile += 1
            continue

        factor_fn = FACTOR_REGISTRY.get(entity.entity_type)
        if factor_fn is None:
            skipped_no_factor += 1
            continue

        # ── OPTION B: use cached FortyGuard env_params when available ──────
        # Cache key matches what /api/entity-env-params saves after a "View Details" click
        env_params = None
        env_cache_key = f"entity_env:{entity.lat:.4f}:{entity.lon:.4f}:2024-08-23"
        cached_env_row = db.get(ApiCache, env_cache_key)
        if cached_env_row:
            try:
                raw = _json.loads(cached_env_row.response_json)
                env_params = parse_env_params_response(raw, tile.temperature_c, entity.lat, entity.lon)
                # Override source tag so frontend can show green dot
                env_params.env_params_source = "fortyguard_api_cached"
            except Exception:
                env_params = None  # fall through to estimate

        if env_params is None:
            env_params = _estimate_env_params_from_tile(tile)

        result = factor_fn(entity, env_params)
        factor_results.append(result)

    # ── 3. Compound risk detection ────────────────────────────────────────────
    compounds = find_compound_risks(
        factor_results,
        max_distance_m=req.compound_radius_m,
        min_receptor_score=req.min_receptor_score,
    )

    # ── 4. Sort by severity ───────────────────────────────────────────────────
    factor_results.sort(key=lambda x: x.risk_score, reverse=True)

    # ── 5. Build response ─────────────────────────────────────────────────────
    high_risk   = [r for r in factor_results if r.risk_score >= 70]
    medium_risk = [r for r in factor_results if 40 <= r.risk_score < 70]

    depts_in_compounds = list({dept for c in compounds for dept in c.departments})

    return {
        "summary": {
            "city":               req.city,
            "total_entities":     len(entities),
            "scored_entities":    len(factor_results),
            "skipped_no_tile":    skipped_no_tile,
            "skipped_no_factor":  skipped_no_factor,
            "high_risk_count":    len(high_risk),
            "medium_risk_count":  len(medium_risk),
            "compound_risk_count": len(compounds),
            "departments_in_compounds": depts_in_compounds,
            "tile_count":         tile_service.tile_count,
        },
        "entity_scores": [_entity_to_dict(r) for r in factor_results],
        "compound_risks": [
            {
                "emitter_id":          c.emitter.entity_id,
                "emitter_name":        c.emitter.entity_name,
                "emitter_type":        c.emitter.entity_type,
                "emitter_department":  c.emitter.department,
                "emitter_delta_t":     c.emitter.metric_value,
                "emitter_lat":         c.emitter.lat,
                "emitter_lon":         c.emitter.lon,
                "receptor_id":         c.receptor.entity_id,
                "receptor_name":       c.receptor.entity_name,
                "receptor_type":       c.receptor.entity_type,
                "receptor_department": c.receptor.department,
                "receptor_risk_score": c.receptor.risk_score,
                "receptor_lat":        c.receptor.lat,
                "receptor_lon":        c.receptor.lon,
                "distance_m":          c.distance_m,
                "compound_score":      c.compound_score,
                "insight":             c.insight,
                "departments":         c.departments,
                "mid_lat": round((c.emitter.lat + c.receptor.lat) / 2, 6),
                "mid_lon": round((c.emitter.lon + c.receptor.lon) / 2, 6),
            }
            for c in compounds
        ],
    }


# ---------------------------------------------------------------------------
# GET /api/forecast — 6-hour risk preview using diurnal temperature model
# ---------------------------------------------------------------------------
@router.get("/forecast")
def get_forecast(
    city: str = Query("nyc"),
    entity_id: str = Query(...),
    hours_ahead: int = Query(6, le=12),
    db: Session = Depends(get_db),
):
    """
    Returns risk score projections for an entity over the next N hours.
    Temperature uses current FortyGuard tile + diurnal adjustment model.
    Answers "when is it safe to reschedule outdoor work?"
    """
    from datetime import datetime, timezone, timedelta
    from core.tile_lookup import _estimate_env_params_from_tile, TileTemperature

    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entity not found")

    factor_fn = FACTOR_REGISTRY.get(entity.entity_type)
    if not factor_fn:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="No factor for this entity type")

    # Get baseline tile temperature
    tile = tile_service.get_temperature(entity.lat, entity.lon)
    baseline_c = tile.temperature_c if tile else 32.0

    now = datetime.now(timezone.utc)
    forecasts = []

    for hour_offset in range(0, hours_ahead + 1):
        forecast_time = now + timedelta(hours=hour_offset)
        hour_utc = forecast_time.hour
        hour_edt = (hour_utc - 4) % 24  # NYC EDT offset

        # Diurnal model: NYC peak heat at 14:00-15:00 EDT
        # Range: ±3.5°C from baseline (peak at 14:00, coolest at 06:00)
        hours_from_peak = abs(hour_edt - 14)
        if hours_from_peak > 12:
            hours_from_peak = 24 - hours_from_peak
        diurnal_delta_c = -3.5 * hours_from_peak / 8.0

        adjusted_c = baseline_c + diurnal_delta_c
        adjusted_f = adjusted_c * 9 / 5 + 32

        # Build a synthetic tile for env estimation
        synthetic_tile = TileTemperature(
            temperature_c=adjusted_c,
            temperature_f=adjusted_f,
            tile_lat=entity.lat,
            tile_lon=entity.lon,
            distance_m=0,
        )
        env_params = _estimate_env_params_from_tile(synthetic_tile)
        result = factor_fn(entity, env_params)

        forecasts.append({
            "hour_offset":    hour_offset,
            "time_utc":       forecast_time.strftime("%H:00 UTC"),
            "time_local":     f"{hour_edt:02d}:00 EDT",
            "temperature_f":  round(adjusted_f, 1),
            "temperature_c":  round(adjusted_c, 1),
            "wbgt_f":         round(env_params.wbgt_f, 1),
            "risk_score":     result.risk_score,
        })

    peak    = max(forecasts, key=lambda x: x["risk_score"])
    safest  = min(forecasts, key=lambda x: x["risk_score"])
    peak_h  = peak["time_local"]
    safe_h  = safest["time_local"]

    recommendation = (
        f"Peak risk at {peak_h} ({peak['risk_score']:.0f}/100, {peak['temperature_f']:.0f}°F). "
        f"Lowest risk at {safe_h} ({safest['risk_score']:.0f}/100, {safest['temperature_f']:.0f}°F). "
    )
    if peak["hour_offset"] in range(3, 9):
        recommendation += "Schedule outdoor work before 10:00 EDT or after 19:00 EDT."
    else:
        recommendation += "Evening hours show progressively lower risk."

    return {
        "entity_id":    entity_id,
        "entity_name":  entity.name,
        "entity_type":  entity.entity_type,
        "forecasts":    forecasts,
        "peak_risk":    peak,
        "safest_window": safest,
        "recommendation": recommendation,
        "data_source":  "FortyGuard temperature tiles + NYC diurnal heat model",
    }


# ---------------------------------------------------------------------------
# GET /api/energy-insight — Solar irradiance + transformer thermal aging
# ---------------------------------------------------------------------------
@router.get("/energy-insight")
def get_energy_insight(city: str = Query("nyc"), db: Session = Depends(get_db)):
    """
    Uses FortyGuard solar irradiance + temperature to estimate cooling demand
    and Con Edison transformer thermal aging.
    FortyGuard use case: "Combine irradiance, temperature, and humidity data to
    forecast energy demand and support energy resilience strategies."
    """
    # Use Manhattan centroid tile as city representative
    tile = tile_service.get_temperature(40.7128, -74.0060)
    from core.tile_lookup import _estimate_env_params_from_tile, TileTemperature
    if tile:
        env = _estimate_env_params_from_tile(tile)
    else:
        synthetic = TileTemperature(32.0, 89.6, 40.7128, -74.0060, 0)
        env = _estimate_env_params_from_tile(synthetic)

    temp_f = env.temperature_f
    solar  = env.solar_irradiance
    humidity = env.humidity

    # Cooling degree hours: energy demand rises ~2.5% per °F above 65°F baseline
    cooling_demand_pct = max(0.0, (temp_f - 65) * 2.5)

    # Solar amplification: high irradiance increases AC load from solar gain through windows
    # Each 100 W/m² above 400 W/m² adds ~1.5% to commercial cooling demand
    solar_demand_pct = max(0.0, (solar - 400) / 100 * 1.5)
    total_demand_pct = cooling_demand_pct + solar_demand_pct

    # Transformer thermal aging — IEEE C57.91 insulation aging factor
    # FAA = exp(15000/383 - 15000/(Θ_hotspot + 273))
    # At rated load, winding hotspot ≈ ambient + 80°C (65°C oil rise + 15°C hot-spot gradient)
    # Extra demand adds additional rise: each 10% above rated → +5°C hotspot
    base_hotspot_rise_c = 80.0   # IEC 60076-7 ONAN-cooled transformer rated hotspot rise
    demand_rise_c = (total_demand_pct / 100) * 20  # demand-driven additional rise
    winding_hotspot_c = env.temperature_c + base_hotspot_rise_c + demand_rise_c
    try:
        faa = math.exp(15000 / 383 - 15000 / (winding_hotspot_c + 273))
        faa = max(0.01, faa)  # floor at 0.01
    except Exception:
        faa = 1.0

    if faa > 2.5:
        transformer_risk = "CRITICAL — operating at >2.5× rated aging rate"
    elif faa > 1.5:
        transformer_risk = "ELEVATED — operating at >1.5× rated aging rate"
    else:
        transformer_risk = "NORMAL"

    grid_insight = (
        f"At {temp_f:.0f}°F with {solar:.0f} W/m² solar load ({humidity:.0f}% humidity), "
        f"cooling demand is ~{total_demand_pct:.0f}% above baseline. "
        f"Con Edison transformers aging at {faa:.1f}× normal rate (IEEE C57.91 model). "
    )
    if faa > 2.0:
        grid_insight += "Pre-emptive load shedding recommended in high-density corridors."
    elif faa > 1.5:
        grid_insight += "Monitor feeder loads closely. Increase inspection frequency."

    return {
        "temperature_f":              round(temp_f, 1),
        "solar_irradiance_wm2":       round(solar, 1),
        "humidity_pct":               round(humidity, 1),
        "cooling_demand_increase_pct": round(total_demand_pct, 1),
        "transformer_aging_factor":   round(faa, 2),
        "transformer_risk":           transformer_risk,
        "grid_insight":               grid_insight,
        "data_source": (
            "FortyGuard temperature + solar irradiance; "
            "IEEE C57.91 transformer thermal aging model; "
            "EPA Energy Star cooling degree day methodology"
        ),
    }


# ---------------------------------------------------------------------------
# GET /api/entity-env-params — on-demand FortyGuard env_params for one entity
# ---------------------------------------------------------------------------
@router.get("/entity-env-params")
async def get_entity_env_params(
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    On-demand FortyGuard /v1/env_params call for a single entity.
    Called lazily when the user expands an entity's detail panel.
    Cached per location so subsequent calls are instant.

    Returns the full 15-parameter environmental reading:
    heat_index, WBGT, humidity, AQI, PM2.5, NO2, O3, SO2, CO2, methane,
    solar GHI/DNI/DHI — everything from FortyGuard's env_params endpoint.

    Dataset context: upgrades every hardcoded estimate in the factor graph to
    a real measured value. WBGT replaces the 0.72T+0.28HI approximation;
    AQI/PM2.5 replaces the temperature-threshold heuristic; solar GHI
    replaces the hardcoded 750 W/m² baseline.
    """
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    tile = tile_service.get_temperature(entity.lat, entity.lon)
    temp_c = tile.temperature_c if tile else 32.0
    tile_lat = entity.lat
    tile_lon = entity.lon

    date_str = "2024-08-23"
    time_str = "14:00"

    # Check if we have cached env_params for this location
    env_cache_key = f"entity_env:{entity.lat:.4f}:{entity.lon:.4f}:{date_str}"
    cached_row = db.get(ApiCache, env_cache_key)
    if cached_row:
        import json as _json
        raw = _json.loads(cached_row.response_json)
        env = parse_env_params_response(raw, temp_c, tile_lat, tile_lon)
        return {
            "entity_id":   entity_id,
            "entity_name": entity.name,
            "entity_type": entity.entity_type,
            "env_params":  env.to_display_dict(),
        }

    from config import settings as _settings
    if _settings.mock_mode:
        # Return estimated params in mock mode
        env = tile_service.get_full_env_params(entity.lat, entity.lon)
        return {
            "entity_id":   entity_id,
            "entity_name": entity.name,
            "entity_type": entity.entity_type,
            "env_params":  env.to_display_dict() if env else {},
        }

    # Live FortyGuard call
    try:
        from core.fortyguard_client import FortyGuardClient
        import json as _json
        client = FortyGuardClient(db)
        result = await client.env_params_for_point(
            lat=entity.lat, lon=entity.lon,
            temp_c=temp_c,
            date_str=date_str, time_str=time_str,
        )
        # Cache the raw result for future entity lookups at this location
        db.merge(ApiCache(
            cache_key=env_cache_key,
            response_json=_json.dumps(result),
        ))
        db.commit()

        env = parse_env_params_response(result, temp_c, tile_lat, tile_lon)
        return {
            "entity_id":   entity_id,
            "entity_name": entity.name,
            "entity_type": entity.entity_type,
            "env_params":  env.to_display_dict(),
        }
    except Exception as e:
        # Fall back gracefully — return estimated params
        env = tile_service.get_full_env_params(entity.lat, entity.lon)
        return {
            "entity_id":      entity_id,
            "entity_name":    entity.name,
            "entity_type":    entity.entity_type,
            "env_params":     env.to_display_dict() if env else {},
            "warning":        f"FortyGuard env_params unavailable: {e}; showing estimates",
        }


# ---------------------------------------------------------------------------
# POST /api/project-impact — Track 4 centerpiece: pre-approval thermal impact
# ---------------------------------------------------------------------------

class NewProjectSpec(BaseModel):
    """
    Specification for a hypothetical new capital project.
    Not saved to the database — computed in memory only.

    The city planner drops a pin, describes the project, and HeatGraph
    propagates the thermal perturbation through the Forney graph to show
    which existing entities are affected and which new compound risks emerge.
    """
    name:                    str   = "New Capital Project"
    latitude:                float
    longitude:               float
    job_type:                str   = "A1"           # NB / DM / A1
    project_size_m2:         float = 2000.0
    canopy_removal_pct:      float = 20.0
    estimated_duration_months: int = 12
    managing_dept:           str   = "NYC DOB"
    compound_radius_m:       float = 500.0


@router.post("/project-impact")
def simulate_project_impact(spec: NewProjectSpec, db: Session = Depends(get_db)):
    """
    TRACK 4 CENTERPIECE: Simulates the thermal impact of a proposed new project
    on the existing Forney factor graph.

    1. Gets current FortyGuard temperature at the proposed site
    2. Estimates ΔT from the project (construction emitter formula)
    3. Finds existing receptors within compound_radius_m
    4. Computes before/after risk scores for each receptor
    5. Identifies new cross-silo compound risks the project creates
    6. Returns a plain-English "before you approve" briefing

    Thermal decay model: ΔT at receptor = ΔT_source × exp(−distance / 200m)
    — urban street canyon thermal diffusion constant (Arnfield 2003).
    """
    # ── 1. Get site FortyGuard temperature ──────────────────────────────────
    tile = tile_service.get_temperature(spec.latitude, spec.longitude)
    current_temp_c = tile.temperature_c if tile else 32.0
    current_temp_f = current_temp_c * 9 / 5 + 32

    # Try to get real env_params from site cache
    env_cache_key = f"entity_env:{spec.latitude:.4f}:{spec.longitude:.4f}:2024-08-23"
    cached_env_row = db.get(ApiCache, env_cache_key)
    if cached_env_row:
        try:
            raw = _json.loads(cached_env_row.response_json)
            site_env = parse_env_params_response(raw, current_temp_c, spec.latitude, spec.longitude)
        except Exception:
            site_env = _estimate_env_params_from_tile(
                TileTemperature(current_temp_c, current_temp_f, spec.latitude, spec.longitude, 0.0)
            )
    else:
        site_env = _estimate_env_params_from_tile(
            TileTemperature(current_temp_c, current_temp_f, spec.latitude, spec.longitude, 0.0)
        )

    # ── 2. Compute thermal footprint (ΔT) ────────────────────────────────────
    # Base ΔT by job type, amplified by solar load and project footprint
    base_delta_f = {"NB": 2.5, "DM": 1.8, "A1": 1.0}.get(spec.job_type, 1.0)
    solar_amplifier = 1.0 + (site_env.solar_irradiance / 750.0) * 0.3
    size_amplifier  = (spec.project_size_m2 / 2000.0) ** 0.5  # sqrt to avoid runaway scaling
    delta_t_f = round(base_delta_f * solar_amplifier * size_amplifier, 2)
    delta_t_c = delta_t_f * 5 / 9

    # PM2.5 increase from diesel equipment + soil disturbance
    dust_pm25 = {"NB": 18.0, "DM": 25.0, "A1": 10.0}.get(spec.job_type, 10.0) * (spec.project_size_m2 / 2000.0)

    new_temp_f = round(current_temp_f + delta_t_f, 1)
    new_temp_c = round(current_temp_c + delta_t_c, 2)

    # ── 3. Find all receptors within radius ───────────────────────────────────
    all_receptors = db.query(Entity).filter(
        Entity.city == "nyc",
        Entity.role.in_(["receptor", "both"]),
    ).all()

    nearby: list[tuple[Entity, float]] = []
    for entity in all_receptors:
        dist = haversine_m(spec.latitude, spec.longitude, entity.lat, entity.lon)
        if dist <= spec.compound_radius_m:
            nearby.append((entity, dist))
    nearby.sort(key=lambda x: x[1])

    # ── 4. Before / after risk scores ─────────────────────────────────────────
    DECAY_LENGTH_M = 200.0  # Arnfield 2003 urban thermal diffusion constant

    receptor_impacts: list[dict] = []
    for entity, dist_m in nearby:
        factor_fn = FACTOR_REGISTRY.get(entity.entity_type)
        if not factor_fn:
            continue

        # BEFORE: current tile temperature, estimated env_params
        tile_here = tile_service.get_temperature(entity.lat, entity.lon)
        t_c = tile_here.temperature_c if tile_here else current_temp_c
        before_env = _estimate_env_params_from_tile(
            TileTemperature(t_c, t_c * 9 / 5 + 32, entity.lat, entity.lon, 0.0)
        )
        before_result = factor_fn(entity, before_env)

        # AFTER: temperature raised by ΔT decaying with distance
        decay = math.exp(-dist_m / DECAY_LENGTH_M)
        effective_dt_c  = delta_t_c  * decay
        effective_dt_f  = delta_t_f  * decay
        effective_pm25  = dust_pm25  * decay

        after_temp_c = t_c + effective_dt_c
        after_env = _estimate_env_params_from_tile(
            TileTemperature(after_temp_c, after_temp_c * 9 / 5 + 32, entity.lat, entity.lon, 0.0)
        )
        after_env.pm25 = round(before_env.pm25 + effective_pm25, 1)
        after_result = factor_fn(entity, after_env)

        risk_change = after_result.risk_score - before_result.risk_score
        if abs(risk_change) < 1.0:
            continue  # no meaningful change — skip

        def _tier(score: float) -> str:
            return "HIGH" if score >= 70 else ("MODERATE" if score >= 40 else "LOW")

        tier_before  = _tier(before_result.risk_score)
        tier_after   = _tier(after_result.risk_score)
        tier_changed = tier_before != tier_after

        receptor_impacts.append({
            "entity_id":       entity.id,
            "entity_name":     entity.name,
            "entity_type":     entity.entity_type,
            "department":      before_result.department,
            "distance_m":      round(dist_m, 1),
            "risk_before":     round(before_result.risk_score, 1),
            "risk_after":      round(after_result.risk_score, 1),
            "risk_change":     round(risk_change, 1),
            "tier_before":     tier_before,
            "tier_after":      tier_after,
            "tier_changed":    tier_changed,
            "effective_delta_t_f": round(effective_dt_f, 2),
            "lat":             entity.lat,
            "lon":             entity.lon,
        })

    # ── 5. New compound risks created by the project ──────────────────────────
    existing_emitters = db.query(Entity).filter(
        Entity.city == "nyc",
        Entity.role.in_(["emitter", "both"]),
    ).all()

    new_compound_risks: list[dict] = []
    impact_by_id = {r["entity_id"]: r for r in receptor_impacts}

    for emitter_entity in existing_emitters:
        for impact in receptor_impacts:
            receptor_entity = next((e for e, _ in nearby if e.id == impact["entity_id"]), None)
            if receptor_entity is None:
                continue
            dist_to_emitter = haversine_m(
                receptor_entity.lat, receptor_entity.lon,
                emitter_entity.lat, emitter_entity.lon,
            )
            if dist_to_emitter <= spec.compound_radius_m:
                new_compound_risks.append({
                    "emitter_1":      spec.name,
                    "emitter_1_dept": spec.managing_dept,
                    "emitter_2":      emitter_entity.name,
                    "receptor":       impact["entity_name"],
                    "receptor_dept":  impact["department"],
                    "risk_after":     impact["risk_after"],
                    "delta_t_f":      impact["effective_delta_t_f"],
                    "insight": (
                        f"Your proposed '{spec.name}' ({spec.managing_dept}) and existing "
                        f"'{emitter_entity.name}' will simultaneously affect "
                        f"'{impact['entity_name']}' ({impact['department']}). "
                        f"Combined temperature impact: +{impact['effective_delta_t_f']:.1f}°F. "
                        f"Risk: {impact['risk_before']:.0f} → {impact['risk_after']:.0f}/100."
                    ),
                })

    # ── 6. Plain-English summary ───────────────────────────────────────────────
    tier_changes    = [r for r in receptor_impacts if r["tier_changed"]]
    affected_depts  = list(set(r["department"] for r in receptor_impacts))

    summary_lines = [
        f"Your proposed project '{spec.name}' adds an estimated +{delta_t_f:.1f}°F to the area "
        f"(FortyGuard baseline: {current_temp_f:.1f}°F → new: {new_temp_f:.1f}°F, "
        f"solar load: {site_env.solar_irradiance:.0f} W/m²)."
    ]
    for r in tier_changes:
        summary_lines.append(
            f"⚠ {r['entity_name']} ({r['entity_type']}) crosses from {r['tier_before']} "
            f"to {r['tier_after']} risk tier: {r['risk_before']:.0f} → {r['risk_after']:.0f}/100. "
            f"({r['department']} — currently unaware of this project.)"
        )
    if affected_depts:
        summary_lines.append(
            f"Departments affected without being notified: {', '.join(affected_depts)}."
        )

    return {
        "project": {
            "name":               spec.name,
            "lat":                spec.latitude,
            "lon":                spec.longitude,
            "job_type":           spec.job_type,
            "managing_dept":      spec.managing_dept,
            "baseline_temp_f":    round(current_temp_f, 1),
            "estimated_delta_t_f": delta_t_f,
            "new_temp_f":         new_temp_f,
            "dust_pm25_increase": round(dust_pm25, 1),
            "env_source":         site_env.env_params_source,
        },
        "receptor_impacts":    receptor_impacts,
        "new_compound_risks":  new_compound_risks[:20],
        "summary":             " ".join(summary_lines),
        "stats": {
            "receptors_in_range":    len(nearby),
            "receptors_affected":    len(receptor_impacts),
            "tier_changes":          len(tier_changes),
            "new_compound_risks":    len(new_compound_risks),
            "departments_unaware":   len(affected_depts),
        },
    }
