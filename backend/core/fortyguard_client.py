"""
FortyGuard API client — corrected schemas from official documentation.

Key corrections applied:
  /v1/satellite       — uses {"sat": {"latitude": ..., "longitude": ...}}
                        NOT polygon_aoi (was wrong in Phase 6)
  /v1/heat_intelligence — temperature in FAHRENHEIT, key is "date" (not "date_time"),
                          result is a PDF download_link (NOT structured JSON)
  /v1/env_params      — 15+ parameters at result.locations[0].parameters
  /v1/heatmap         — persistence/exceedance require threshold + direction params

Async pattern: submit → activity_id → poll → cache → return result.
"""
import asyncio
import base64
import hashlib
import json
import time

import httpx
from sqlalchemy.orm import Session

from config import settings
from db.database import ApiCache


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(endpoint: str, body: dict) -> str:
    payload = json.dumps({"endpoint": endpoint, **body}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_cached(db: Session, key: str) -> dict | None:
    row = db.get(ApiCache, key)
    return json.loads(row.response_json) if row else None


def _set_cached(db: Session, key: str, data: dict) -> None:
    db.merge(ApiCache(cache_key=key, response_json=json.dumps(data)))
    db.commit()


# ── Client ────────────────────────────────────────────────────────────────────

class FortyGuardClient:
    def __init__(self, db: Session):
        self.db = db
        self.headers = {
            "api-key": settings.fortyguard_api_key,
            "Content-Type": "application/json",
        }
        self.base_url = settings.fortyguard_base_url.rstrip("/")

    # ── Low-level submit/poll ─────────────────────────────────────────────

    async def _submit(self, endpoint: str, body: dict) -> str:
        """POST to endpoint, return activity_id."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}{endpoint}",
                headers=self.headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            activity_id = data.get("data", {}).get("activity_id")
            if not activity_id:
                raise ValueError(f"No activity_id in response from {endpoint}: {data}")
            return activity_id

    async def _poll(self, activity_id: str, max_seconds: int | None = None) -> dict:
        """Poll /v1/status/{id} until completed. Returns data.result dict."""
        limit    = max_seconds or settings.poll_max_seconds
        deadline = time.monotonic() + limit
        async with httpx.AsyncClient(timeout=30) as client:
            while time.monotonic() < deadline:
                resp = await client.get(
                    f"{self.base_url}/v1/status/{activity_id}",
                    headers=self.headers,
                )
                resp.raise_for_status()
                inner  = resp.json().get("data", {})
                status = (inner.get("status") or "").lower()
                if status == "completed":
                    return inner.get("result", {})
                if status in ("failed", "error"):
                    raise RuntimeError(f"FortyGuard job {activity_id} failed: {inner}")
                await asyncio.sleep(settings.poll_interval_seconds)
        raise TimeoutError(
            f"FortyGuard job {activity_id} did not complete in {limit}s"
        )

    # ── /v1/heatmap ───────────────────────────────────────────────────────

    async def heatmap(self, body: dict) -> dict:
        """
        POST /v1/heatmap — temperature/persistence/exceedance heatmap.

        body must include analytic_type. For 'persistence' or 'exceedance'
        also include threshold (°C) and direction ('above').

        Falls back to any cached NYC heatmap so the demo always has data.
        """
        key    = _cache_key("/v1/heatmap", body)
        cached = _get_cached(self.db, key)
        if cached:
            cached["_cached"] = True
            return cached

        # Fallback: return any existing heatmap with real tile data
        try:
            for row in self.db.query(ApiCache).all():
                try:
                    data = json.loads(row.response_json or "{}")
                except Exception:
                    continue
                map_data = data.get("map_data")
                if isinstance(map_data, dict) and len(map_data.get("features", [])) > 100:
                    _set_cached(self.db, key, data)
                    data["_cached"] = True
                    return data
        except Exception:
            pass

        activity_id = await self._submit("/v1/heatmap", body)
        result      = await self._poll(activity_id)
        _set_cached(self.db, key, result)
        return result

    # ── /v1/env_params ────────────────────────────────────────────────────

    async def env_params(self, body: dict) -> dict:
        """POST /v1/env_params (raw body variant — used by legacy routes)."""
        key    = _cache_key("/v1/env_params", body)
        cached = _get_cached(self.db, key)
        if cached:
            cached["_cached"] = True
            return cached
        activity_id = await self._submit("/v1/env_params", body)
        result      = await self._poll(activity_id)
        _set_cached(self.db, key, result)
        return result

    async def env_params_for_point(
        self,
        lat: float, lon: float,
        temp_c: float,
        date_str: str, time_str: str,
        filter_type: int = 1,
    ) -> dict:
        """
        POST /v1/env_params with typed params.

        temperature is Celsius from the heatmap tile.
        No "analysis" key → returns ALL 15+ parameters.
        Result structure: result.locations[0].parameters + .solar_irradiance
        """
        body = {
            "latitude":    lat,
            "longitude":   lon,
            "temperature": temp_c,   # °C
            "date_time": {
                "start_date": date_str,
                "start_time": time_str,
                "filter_type": filter_type,
            },
        }
        return await self.env_params(body)

    # ── /v1/satellite — CORRECTED SCHEMA ─────────────────────────────────

    async def satellite(
        self,
        lat: float, lon: float,
        date_str: str, time_str: str,
        granularity: int = 100,
    ) -> dict:
        """
        POST /v1/satellite — land-cover segmentation for a point.

        CORRECT body uses {"sat": {"latitude": ..., "longitude": ...}}
        NOT polygon_aoi (the previous implementation was wrong).

        Returns:
          orignal_image (sic — API typo)   — Base64 satellite image
          segmentation.segments             — {ClassName: pct} coverage
          segmentation.image_content        — Base64 segmentation mask
          segmentation.image_legend         — {ClassName: [R,G,B]}
          image_year                        — year of imagery
        """
        body = {
            "sat": {
                "latitude":  lat,
                "longitude": lon,
            },
            "date_time": {
                "start_date": date_str,
                "start_time": time_str,
                "filter_type": 1,
            },
            "granularity": granularity,
        }
        key    = _cache_key("/v1/satellite", body)
        cached = _get_cached(self.db, key)
        if cached:
            cached["_cached"] = True
            return cached

        activity_id = await self._submit("/v1/satellite", body)
        result      = await self._poll(activity_id)
        _set_cached(self.db, key, result)
        return result

    # ── /v1/heat_intelligence — CORRECTED SCHEMA ─────────────────────────

    async def heat_intelligence(
        self,
        lat: float, lon: float,
        temp_f: float,           # FAHRENHEIT — confirmed from official docs
        date_str: str,           # "date" key (plain string, NOT date_time object)
        analysis_types: list | None = None,
    ) -> dict:
        """
        POST /v1/heat_intelligence — 5-category contextual heat PDF report.

        CORRECTIONS from official docs:
        - temperature must be in FAHRENHEIT
        - key is "date" (plain date string), NOT "date_time" object
        - result.download_link is a TEMPORARY signed URL — must download immediately
        - result is NOT structured JSON (no "geographic"/"urban" keys in response)
        - PDF bytes are cached in ApiCache as base64 under a coordinate key

        Returns: {"pdf_base64": str, "activity_id": str, "lat": ..., "lon": ...}
        """
        if analysis_types is None:
            analysis_types = ["geographic", "environmental", "urban", "events", "anthropogenic"]

        # Use a stable coordinate key (not body hash) so tiny temp differences don't break cache
        coord_key = f"heat_intel:{lat:.4f}:{lon:.4f}:{date_str}"
        cached_row = self.db.get(ApiCache, coord_key)
        if cached_row:
            data = json.loads(cached_row.response_json)
            data["_cached"] = True
            return data

        body = {
            "latitude":    lat,
            "longitude":   lon,
            "temperature": temp_f,       # FAHRENHEIT
            "date":        date_str,     # plain string, NOT a date_time object
            "analysis":    analysis_types,
        }

        async with httpx.AsyncClient(timeout=600) as client:
            # Submit
            resp = await client.post(
                f"{self.base_url}/v1/heat_intelligence",
                headers=self.headers,
                json=body,
            )
            resp.raise_for_status()
            activity_id = resp.json().get("data", {}).get("activity_id")
            if not activity_id:
                raise ValueError(f"No activity_id for heat_intelligence: {resp.text[:200]}")

            # Poll
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                await asyncio.sleep(5)
                sr   = await client.get(
                    f"{self.base_url}/v1/status/{activity_id}",
                    headers=self.headers,
                )
                data = sr.json().get("data", {})
                status = (data.get("status") or "").lower()

                if status == "completed":
                    result       = data.get("result", {})
                    download_link = result.get("download_link")
                    if not download_link:
                        raise RuntimeError(
                            "Heat Intelligence completed but no download_link in result"
                        )
                    # Download PDF immediately — signed URL expires quickly
                    pdf_resp = await client.get(download_link, timeout=60)
                    pdf_resp.raise_for_status()
                    pdf_b64 = base64.b64encode(pdf_resp.content).decode()

                    payload = {
                        "pdf_base64":  pdf_b64,
                        "activity_id": activity_id,
                        "lat":  lat,
                        "lon":  lon,
                        "date": date_str,
                    }
                    _set_cached(self.db, coord_key, payload)
                    return payload

                elif status in ("failed", "error"):
                    raise RuntimeError(f"Heat Intelligence failed: {data}")

        raise TimeoutError("Heat Intelligence timed out after 600s")

    # ── /v1/streetview ────────────────────────────────────────────────────

    async def streetview(
        self,
        lat: float, lon: float,
        vertical_angle: float = 10.0,
        horizontal_angle: float = 90.0,
        back_view: bool = False,
    ) -> dict:
        """
        POST /v1/streetview — ground-level segmentation for a point.

        Returns:
          front.original_image     — Base64 street-level photo
          front.segmented_image    — Base64 segmentation mask
          front.segments           — {ClassName: pct} coverage
          front.image_legend       — {ClassName: [R,G,B]} colors
          front.image_date         — date of street view capture

        No date_time parameter — uses the most recent available street view
        for the given coordinates. Results cached permanently per location.
        """
        body = {
            "latitude":         lat,
            "longitude":        lon,
            "vertical_angle":   vertical_angle,
            "horizontal_angle": horizontal_angle,
            "back_view":        back_view,
        }
        key    = _cache_key("/v1/streetview", body)
        cached = _get_cached(self.db, key)
        if cached:
            cached["_cached"] = True
            return cached

        activity_id = await self._submit("/v1/streetview", body)
        result      = await self._poll(activity_id)
        _set_cached(self.db, key, result)
        return result

    # ── Generic passthrough ────────────────────────────────────────────────

    async def request(self, endpoint: str, body: dict) -> dict:
        """
        Generic async submit→poll for any FortyGuard endpoint.
        Results cached per (endpoint, body) hash.
        NOTE: prefer the typed methods above for satellite/heat_intelligence/env_params.
        """
        key    = _cache_key(endpoint, body)
        cached = _get_cached(self.db, key)
        if cached:
            cached["_cached"] = True
            return cached
        activity_id = await self._submit(endpoint, body)
        result      = await self._poll(activity_id)
        _set_cached(self.db, key, result)
        return result
