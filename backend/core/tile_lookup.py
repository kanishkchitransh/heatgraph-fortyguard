"""
Tile Lookup — finds the FortyGuard temperature at any (lat, lon)
by matching against cached heatmap tiles, then enriches with estimated
environmental parameters (WBGT, AQI, PM2.5, solar irradiance, humidity).

The FortyGuardClient stores poll results directly in ApiCache.response_json.
The result structure (from FortyGuard status poll data.result) is:
  { "map_data": { "type": "FeatureCollection", "features": [...] }, "stats_data": {...} }

Each feature polygon has:
  { "properties": { "average_temperature": <float in °C> }, "geometry": { ... } }

Strategy: On first call, load all tile centroids into memory from the cache.
For each entity lookup, find the nearest tile centroid using Euclidean
distance (fast for ~12K tiles, <1ms per lookup).

Cache keys are SHA-256 hashes (no prefix) — scan all rows and pick
any entry that has map_data with features.
"""
import json
import math
from dataclasses import dataclass, field
from db.database import SessionLocal, ApiCache


@dataclass
class TileTemperature:
    temperature_c: float
    temperature_f: float
    tile_lat: float
    tile_lon: float
    distance_m: float   # approximate distance from query point to tile centroid


@dataclass
class FullEnvParams:
    """
    Complete environmental parameters for a location.
    temperature_c/f always come from the nearest cached FortyGuard heatmap tile.
    All other fields are estimated from temperature using standard meteorological
    formulas (Option B). When FortyGuard /v1/env_params is fetched live and cached,
    env_params_source switches to 'fortyguard_api' or 'cached'.
    """
    # From heatmap tile (always available)
    temperature_c: float
    temperature_f: float
    tile_lat: float
    tile_lon: float
    distance_m: float

    # Environmental parameters (estimated or from FortyGuard API)
    heat_index_f: float = 0.0       # NWS Rothfusz heat index
    wbgt_f: float = 0.0             # Wet-bulb globe temperature (OSHA metric)
    humidity: float = 0.0           # Relative humidity %
    solar_irradiance: float = 0.0   # W/m²
    aqi: float = 0.0                # EPA Air Quality Index
    pm25: float = 0.0               # PM2.5 μg/m³
    no2: float = 0.0                # NO₂ ppb
    o3: float = 0.0                 # O₃ ppb

    # Source tracking for trust indicators
    env_params_source: str = "estimated"  # "fortyguard_api" | "cached" | "estimated"

    @property
    def heat_index_c(self) -> float:
        return (self.heat_index_f - 32) * 5 / 9

    @property
    def wbgt_c(self) -> float:
        return (self.wbgt_f - 32) * 5 / 9

    def to_display_dict(self) -> dict:
        """For frontend display in entity detail panels."""
        return {
            "temperature_f": round(self.temperature_f, 1),
            "temperature_c": round(self.temperature_c, 1),
            "heat_index_f": round(self.heat_index_f, 1),
            "wbgt_f": round(self.wbgt_f, 1),
            "humidity_pct": round(self.humidity, 1),
            "solar_irradiance_wm2": round(self.solar_irradiance, 1),
            "aqi": round(self.aqi, 0),
            "pm25_ugm3": round(self.pm25, 1),
            "no2_ppb": round(self.no2, 1),
            "o3_ppb": round(self.o3, 1),
            "source": self.env_params_source,
        }


def _estimate_env_params_from_tile(tile: TileTemperature) -> FullEnvParams:
    """
    Estimate full environmental parameters from a heatmap tile temperature.
    Uses standard meteorological formulas calibrated for NYC August conditions.

    This is Option B from the spec: synchronous, no extra API call.
    The /v1/env_params live call is done on-demand (lazy) for detailed views.
    """
    temp_c = tile.temperature_c
    temp_f = tile.temperature_f

    # NYC August baseline humidity: ~65% (NOAA climatology)
    humidity = 65.0

    # Heat Index — NWS Rothfusz equation (valid ≥ 80°F, ≥ 40% humidity)
    if temp_f >= 80:
        HI = (
            -42.379
            + 2.04901523 * temp_f
            + 10.14333127 * humidity
            - 0.22475541 * temp_f * humidity
            - 6.83783e-3 * temp_f ** 2
            - 5.481717e-2 * humidity ** 2
            + 1.22874e-3 * temp_f ** 2 * humidity
            + 8.5282e-4 * temp_f * humidity ** 2
            - 1.99e-6 * temp_f ** 2 * humidity ** 2
        )
        heat_index_f = round(HI, 1)
    else:
        heat_index_f = temp_f

    # WBGT approximation (outdoor, shaded probe proxy)
    # WBGT ≈ 0.72 × T_dry + 0.28 × HI  (simplified Bernard & Pourmoghani)
    wbgt_f = round(0.72 * temp_f + 0.28 * heat_index_f, 1)

    # Solar irradiance: NYC August peak-hour estimate (W/m²)
    # Varies 600–900 W/m² depending on cloud cover; use 750 as nominal noon value
    solar_irradiance = 750.0

    # AQI rises in hot weather due to ozone photochemistry
    if temp_f > 95:
        aqi = 85.0   # Moderate-Unhealthy for Sensitive Groups
    elif temp_f > 88:
        aqi = 70.0   # Moderate
    else:
        aqi = 55.0   # Moderate (lower end)

    # PM2.5 (μg/m³) from AQI — EPA linear interpolation for 12–35.4 μg/m³ range
    pm25 = round(
        (aqi - 50) * 23.4 / 50 + 12.0 if aqi > 50 else 12.0,
        1,
    )

    return FullEnvParams(
        temperature_c=temp_c,
        temperature_f=temp_f,
        tile_lat=tile.tile_lat,
        tile_lon=tile.tile_lon,
        distance_m=tile.distance_m,
        heat_index_f=heat_index_f,
        wbgt_f=wbgt_f,
        humidity=humidity,
        solar_irradiance=solar_irradiance,
        aqi=aqi,
        pm25=pm25,
        no2=5.0,   # NYC background NO₂ (ppb)
        o3=40.0,   # NYC background O₃ (ppb)
        env_params_source="estimated",
    )


class TileLookupService:
    def __init__(self):
        self._tiles: list[tuple[float, float, float]] = []  # (lat, lon, temp_c)
        self._loaded = False

    def _load_tiles(self):
        """Load cached heatmap tiles into memory. Idempotent — safe to call many times."""
        if self._loaded:
            return

        db = SessionLocal()
        try:
            # The cache key is a raw SHA-256 hash — no prefix to filter by.
            # Scan all rows and pick the one(s) with map_data.features.
            cache_entries = db.query(ApiCache).all()

            for entry in cache_entries:
                try:
                    data = json.loads(entry.response_json)
                except (json.JSONDecodeError, TypeError):
                    continue

                if not isinstance(data, dict):
                    continue

                # Navigate to GeoJSON features.
                # FortyGuard stores: result = { map_data: { features: [...] }, stats_data: {...} }
                map_data = data.get("map_data")
                if not isinstance(map_data, dict):
                    continue
                features = map_data.get("features")
                if not isinstance(features, list) or not features:
                    continue

                # Confirmed: this entry has heatmap tiles
                before = len(self._tiles)
                for feature in features:
                    props = feature.get("properties", {})
                    temp_c = props.get("average_temperature")
                    if temp_c is None:
                        continue

                    geom = feature.get("geometry", {})
                    coords = geom.get("coordinates", [[]])
                    ring = coords[0] if coords else []
                    if not ring:
                        continue

                    # Polygon ring: [[lon, lat], [lon, lat], ...]
                    lons = [p[0] for p in ring]
                    lats = [p[1] for p in ring]
                    centroid_lat = sum(lats) / len(lats)
                    centroid_lon = sum(lons) / len(lons)

                    self._tiles.append((centroid_lat, centroid_lon, float(temp_c)))

                after = len(self._tiles)
                print(f"TileLookupService: loaded {after - before} tiles from cache key {entry.cache_key[:12]}…")

        finally:
            db.close()

        if self._tiles:
            self._loaded = True   # only lock once we have real data
            print(f"TileLookupService: {len(self._tiles)} total tile centroids ready.")
        else:
            # Don't set _loaded — retry on next call (tiles may be fetched soon)
            print(
                "TileLookupService: no heatmap tiles in cache yet. "
                "Click 'Fetch Heatmap' in the frontend; analysis will work after that."
            )

    def reload(self):
        """Force a reload (call after a new heatmap is fetched)."""
        self._loaded = False
        self._tiles = []
        self._load_tiles()

    def get_temperature(self, lat: float, lon: float) -> TileTemperature | None:
        """Return the temperature at (lat, lon) from the nearest cached tile."""
        self._load_tiles()

        if not self._tiles:
            return None

        best_dist_sq = float("inf")
        best = None

        for t_lat, t_lon, t_temp in self._tiles:
            dlat = lat - t_lat
            dlon = lon - t_lon
            dist_sq = dlat * dlat + dlon * dlon
            if dist_sq < best_dist_sq:
                best_dist_sq = dist_sq
                best = (t_lat, t_lon, t_temp)

        if best is None:
            return None

        t_lat, t_lon, t_temp = best
        dist_m = math.sqrt(best_dist_sq) * 111_000  # ~111 km per degree latitude

        return TileTemperature(
            temperature_c=t_temp,
            temperature_f=t_temp * 9 / 5 + 32,
            tile_lat=t_lat,
            tile_lon=t_lon,
            distance_m=round(dist_m, 1),
        )

    def get_full_env_params(self, lat: float, lon: float) -> FullEnvParams | None:
        """
        Return full environmental parameters at (lat, lon).
        Temperature from nearest FortyGuard tile; other params estimated.
        This is the primary entry point for all factor function calls.
        """
        tile = self.get_temperature(lat, lon)
        if tile is None:
            return None
        return _estimate_env_params_from_tile(tile)

    @property
    def tile_count(self) -> int:
        self._load_tiles()
        return len(self._tiles)


# Module-level singleton — loaded once per process
tile_service = TileLookupService()
