"""
GET /api/live-context?city=nyc

Returns current NYC heat situation, sourced from Gemini web search.
Cached 1 hour — shows as a live banner in the insight panel.

Example response:
  "NYC currently 89°F. 523 cooling centers open. DHS reports 87,200 in shelters tonight."
"""
import os
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from db.database import SessionLocal, ApiCache
from config import settings

router = APIRouter(prefix="/api", tags=["live"])

GEMINI_API_KEY = settings.gemini_api_key

_FALLBACK_CONTEXT = {
    "headline":       "NYC summer heat monitoring active — thermal risk data from FortyGuard",
    "temperature":    "August 2026 avg 85–92°F (29–33°C) across NYC",
    "cooling_centers":"520+ designated NYC cooling centers; call 311 for nearest open location",
    "shelter_census": "~90,000 people in NYC shelter system nightly (NYC DHS 2026)",
    "recent_events":  "DOHMH heat health advisory protocol active above 95°F heat index",
    "active_programs":"Cool Neighborhoods NYC, Beat the Heat, NYC Schools Cool program",
}


@router.get("/live-context")
def get_live_context(city: str = Query("nyc")):
    db = SessionLocal()

    # Cache key = city + current UTC hour
    hour_key  = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
    cache_key = f"live:{city}:{hour_key}"

    cached = db.query(ApiCache).filter(ApiCache.cache_key == cache_key).first()
    if cached:
        db.close()
        try:
            return json.loads(cached.response_json)
        except Exception:
            pass

    if not GEMINI_API_KEY:
        db.close()
        return {"context": _FALLBACK_CONTEXT, "source": "fallback", "cached_at": hour_key}

    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)

        prompt = """Search the web for current NYC heat conditions (August 2026).
Find: 1) Current temperature and any NWS heat advisories for NYC
2) How many NYC cooling centers are currently open
3) NYC DHS shelter census — how many people in shelters tonight
4) Any recent heat-related incidents or deaths this week
5) Which city heat relief programs are currently active

Return ONLY valid JSON (no markdown, no backticks):
{
  "headline": "One punchy sentence — the most important current heat fact",
  "temperature": "Current temp + conditions (e.g. '91°F feels like 98°F, heat advisory active')",
  "cooling_centers": "Count and open/closed status",
  "shelter_census": "Tonight's shelter population + trend vs last week",
  "recent_events": "Heat-related news this week (deaths, ER visits, advisories)",
  "active_programs": "Which city programs are currently running"
}"""

        model  = genai.GenerativeModel(settings.gemini_model)
        resp   = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(temperature=0.2, max_output_tokens=1024),
        )
        text = resp.text.strip()
        for p in ("```json", "```", "json"):
            if text.startswith(p):
                text = text[len(p):]
        if text.endswith("```"):
            text = text[:-3]

        context_data = json.loads(text.strip())
        result = {"context": context_data, "source": settings.gemini_model, "cached_at": hour_key}

    except Exception as e:
        print(f"Live context Gemini failed: {e}")
        result = {"context": _FALLBACK_CONTEXT, "source": "fallback", "cached_at": hour_key, "error": str(e)}

    # Cache
    try:
        existing = db.query(ApiCache).filter(ApiCache.cache_key == cache_key).first()
        if existing:
            existing.response_json = json.dumps(result)
        else:
            db.add(ApiCache(cache_key=cache_key, response_json=json.dumps(result)))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    return result
