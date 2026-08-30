"""
Mock FortyGuard responses — returns the SAME schema as the real API.

Real heatmap result shape:
  {
    "map_data": GeoJSON FeatureCollection (tiles with temperature in °C),
    "stats_data": { temperature_stats: {...}, ... }
  }

Toggle with MOCK_MODE=true in backend/.env
NYC August midday: ~95°F base ≈ 35°C, UHI adds 2-4°C in Midtown/Lower Manhattan.
"""
import math
import random


def _heat_island(lat: float, lon: float) -> float:
    """NYC urban heat island: Midtown Manhattan core is hottest."""
    # Midtown anchor
    midtown_lat, midtown_lon = 40.754, -73.984
    dist = math.sqrt((lat - midtown_lat) ** 2 + (lon - midtown_lon) ** 2)
    midtown_uhi = 3.5 * max(0, 1 - dist / 0.06)

    # Lower Manhattan secondary
    lower_lat, lower_lon = 40.712, -74.005
    dist2 = math.sqrt((lat - lower_lat) ** 2 + (lon - lower_lon) ** 2)
    lower_uhi = 2.0 * max(0, 1 - dist2 / 0.04)

    return midtown_uhi + lower_uhi


def _f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def generate_heatmap_features(
    bbox: tuple[float, float, float, float],
    granularity: int = 100,
) -> list[dict]:
    """
    Generate GeoJSON Feature tiles for an NYC heatmap.
    Each Feature is a small Polygon tile with average_temperature in °C in properties.
    Step size: granularity meters ≈ 0.0009° per 100m.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    step = (granularity / 1000) * 0.009  # deg per granularity-meter step

    features = []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            # NYC August midday: ~95°F base ≈ 35°C
            base_c = _f_to_c(95.0)
            uhi_c  = _heat_island(lat, lon)
            noise_c = random.gauss(0, 0.6)
            temp_c  = round(base_c + uhi_c + noise_c, 2)

            half = step / 2
            features.append({
                "type": "Feature",
                "properties": {
                    "tile_id":           len(features),
                    "average_temperature": temp_c,
                    "min_temperature":   round(temp_c - 0.3, 2),
                    "max_temperature":   round(temp_c + 0.3, 2),
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [lon - half, lat - half],
                        [lon - half, lat + half],
                        [lon + half, lat + half],
                        [lon + half, lat - half],
                        [lon - half, lat - half],
                    ]],
                },
            })
            lon += step
        lat += step

    return features


def mock_heatmap_response(body: dict) -> dict:
    """Return a mock result matching FortyGuard's real result schema."""
    polygon_aoi = body.get("polygon_aoi", {})
    features_in = polygon_aoi.get("features", [])
    if not features_in:
        return {"error": "invalid polygon_aoi"}

    geom   = features_in[0].get("geometry", {})
    coords = geom.get("coordinates", [[]])[0]
    lons   = [c[0] for c in coords]
    lats   = [c[1] for c in coords]
    bbox   = (min(lons), min(lats), max(lons), max(lats))

    granularity   = body.get("granularity", 100)
    tile_features = generate_heatmap_features(bbox, granularity)

    temps  = [f["properties"]["average_temperature"] for f in tile_features]
    mean_c = sum(temps) / len(temps) if temps else 0

    return {
        "_mock":   True,
        "_cached": False,
        "map_data": {
            "type":     "FeatureCollection",
            "features": tile_features,
        },
        "stats_data": {
            "temperature_stats": {
                "Minimum":            round(min(temps), 2) if temps else 0,
                "Maximum":            round(max(temps), 2) if temps else 0,
                "Mean":               round(mean_c, 2),
                "Standard_deviation": round(
                    math.sqrt(sum((t - mean_c) ** 2 for t in temps) / len(temps)), 2
                ) if temps else 0,
            },
            "units": "celsius",
        },
        "metadata": {
            "source":       "mock",
            "city":         "nyc",
            "cell_count":   len(tile_features),
            "granularity_m": granularity,
        },
    }
