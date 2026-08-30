"""
Parse FortyGuard /v1/env_params API response into FullEnvParams.

OFFICIAL RESPONSE STRUCTURE (docs-api.fortyguard.com):
  result.locations[0].parameters = {
    "heat_index_celsius":          [float | null],
    "apparent_temperature_celsius":[float | null],
    "wet_bulb_temperature_celsius":[float | null],   ← WBGT proxy
    "relative_humidity_percent":   [float | null],
    "precipitation_mm":            [float | null],
    "cloud_cover_octas":           [float | null],
    "air_quality:idx":             [float | null],   ← Overall AQI
    "air_quality_pm2p5:idx":       [float | null],
    "air_quality_pm10:idx":        [float | null],
    "air_quality_no2:idx":         [float | null],
    "aqi_us_co":                   [float | null],
    "air_quality_o3:idx":          [float | null],
    "air_quality_so2:idx":         [float | null],
    "methane_ppb":                 [float | null],
    "co2_ppm":                     [float | null],
  }
  result.locations[0].solar_irradiance.clear_sky = {
    "ghi": float,   # Global Horizontal Irradiance W/m²
    "dni": float,   # Direct Normal Irradiance W/m²
    "dhi": float,   # Diffuse Horizontal Irradiance W/m²
  }

IMPORTANT:
- null means data unavailable — NEVER treat as 0. Use fallback estimates.
- All parameter values are arrays (time series). For filter_type=1 take index [0].
- temperature passed to env_params is in CELSIUS (from heatmap tile).
- temperature passed to heat_intelligence is in FAHRENHEIT.
"""
from __future__ import annotations
from typing import Optional

from core.tile_lookup import FullEnvParams, TileTemperature, _estimate_env_params_from_tile


def _safe_first(arr, fallback=None):
    """Get first non-null value from an array, returning fallback for null/empty/-999."""
    if arr is None:
        return fallback
    v = arr[0] if isinstance(arr, (list, tuple)) else arr
    if v is None or v == -999:
        return fallback
    try:
        f = float(v)
        return f if (f == f) else fallback   # NaN check
    except (TypeError, ValueError):
        return fallback


def parse_env_params_response(
    result: dict,
    temp_c: float,
    tile_lat: float = 0.0,
    tile_lon: float = 0.0,
) -> FullEnvParams:
    """
    Parse FortyGuard /v1/env_params API result into FullEnvParams.

    Falls back gracefully: any null API field → use meteorological estimate.
    source is set to "fortyguard_api" if we got at least humidity or AQI,
    otherwise "estimated".
    """
    temp_f = temp_c * 9 / 5 + 32
    locations = result.get("locations", [])

    if not locations:
        return _fallback(temp_c, tile_lat, tile_lon)

    loc    = locations[0]
    params = loc.get("parameters", {})
    solar  = loc.get("solar_irradiance", {}).get("clear_sky", {})

    # ── Temperature & humidity ────────────────────────────────────────────
    humidity = _safe_first(params.get("relative_humidity_percent")) or 65.0

    # ── Heat Index ────────────────────────────────────────────────────────
    hi_c = _safe_first(params.get("heat_index_celsius"))
    if hi_c is not None:
        heat_index_f = hi_c * 9 / 5 + 32
    else:
        heat_index_f = _rothfusz(temp_f, humidity)

    # ── WBGT (wet-bulb globe temperature) ─────────────────────────────────
    wb_c = _safe_first(params.get("wet_bulb_temperature_celsius"))
    if wb_c is not None:
        wbgt_f = wb_c * 9 / 5 + 32
    else:
        # OSHA/ACGIH approximation: WBGT ≈ 0.72*Tdry + 0.28*HI
        wbgt_f = 0.72 * temp_f + 0.28 * heat_index_f

    # ── Air quality ───────────────────────────────────────────────────────
    aqi_overall = _safe_first(params.get("air_quality:idx"))
    aqi_pm25    = _safe_first(params.get("air_quality_pm2p5:idx"))
    aqi_no2     = _safe_first(params.get("air_quality_no2:idx"))
    aqi_o3      = _safe_first(params.get("air_quality_o3:idx"))

    # Derive composite AQI if overall is null
    if aqi_overall is None:
        aqi_overall = aqi_pm25 or _temp_to_aqi(temp_f)

    # PM2.5 in μg/m³ from AQI
    pm25 = _aqi_to_pm25(aqi_pm25 or aqi_overall)

    # NO₂: AQI scale 0-200 maps roughly to 0-200 μg/m³ → ÷1.88 → ppb
    no2_ppb = round((aqi_no2 / 1.88), 1) if aqi_no2 else 5.0

    # O₃: AQI 100 = 70 ppb (EPA standard)
    o3_ppb = round((aqi_o3 / 100 * 70), 1) if aqi_o3 else 40.0

    # ── Solar irradiance ─────────────────────────────────────────────────
    solar_ghi = solar.get("ghi") or 750.0

    # ── Source tracking ───────────────────────────────────────────────────
    # Mark as real API data if we got at least one meaningful measurement
    got_real_data = any(v is not None for v in [
        _safe_first(params.get("relative_humidity_percent")),
        _safe_first(params.get("air_quality:idx")),
        wb_c, hi_c,
    ])
    source = "fortyguard_api" if got_real_data else "estimated"

    return FullEnvParams(
        temperature_c=temp_c,
        temperature_f=round(temp_f, 1),
        tile_lat=tile_lat,
        tile_lon=tile_lon,
        distance_m=0.0,
        heat_index_f=round(heat_index_f, 1),
        wbgt_f=round(wbgt_f, 1),
        humidity=round(humidity, 1),
        solar_irradiance=round(solar_ghi, 1),
        aqi=round(aqi_overall, 1),
        pm25=round(pm25, 1),
        no2=round(no2_ppb, 1),
        o3=round(o3_ppb, 1),
        env_params_source=source,
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _fallback(temp_c: float, tile_lat: float, tile_lon: float) -> FullEnvParams:
    """Use tile_lookup estimated params when API gives no data."""
    tile = TileTemperature(temp_c, temp_c * 9 / 5 + 32, tile_lat, tile_lon, 0.0)
    return _estimate_env_params_from_tile(tile)


def _rothfusz(temp_f: float, humidity: float) -> float:
    """NWS Rothfusz heat index equation (valid for T ≥ 80°F, RH ≥ 40%)."""
    if temp_f < 80:
        return temp_f
    return round(
        -42.379
        + 2.04901523 * temp_f
        + 10.14333127 * humidity
        - 0.22475541 * temp_f * humidity
        - 6.83783e-3 * temp_f ** 2
        - 5.481717e-2 * humidity ** 2
        + 1.22874e-3 * temp_f ** 2 * humidity
        + 8.5282e-4 * temp_f * humidity ** 2
        - 1.99e-6 * temp_f ** 2 * humidity ** 2,
        1,
    )


def _temp_to_aqi(temp_f: float) -> float:
    """Estimate AQI from temperature when API data unavailable."""
    if temp_f > 95:
        return 85.0   # Unhealthy for Sensitive Groups (ozone photochemistry)
    elif temp_f > 88:
        return 70.0   # Moderate
    return 55.0       # Moderate (lower bound)


def _aqi_to_pm25(aqi: Optional[float]) -> float:
    """EPA AQI to PM2.5 μg/m³ breakpoint conversion."""
    if not aqi or aqi < 0:
        return 12.0
    if aqi <= 50:
        return round(aqi * 12.0 / 50, 1)
    elif aqi <= 100:
        return round(12.1 + (aqi - 50) * (35.4 - 12.1) / 50, 1)
    elif aqi <= 150:
        return round(35.5 + (aqi - 100) * (55.4 - 35.5) / 50, 1)
    else:
        return round(55.5 + (aqi - 150) * (150.4 - 55.5) / 50, 1)
