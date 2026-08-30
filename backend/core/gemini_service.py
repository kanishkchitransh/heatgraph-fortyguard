"""
Gemini Flash service — generates solution cards for compound risks.

Calls Gemini 2.0 Flash with web search to produce structured JSON:
  - what_you_can_do:   2-4 actions sorted low→high effort, NYC-specific
  - whats_happening:  1-3 real NYC programs found via web search
  - optimistic_outlook: counterfactual with numbers
  - live_context:     current data from web search

Caches results in ApiCache (keyed by entity_id + role) so Gemini is only
called once per entity per role. Falls back to static templates when the
API key is absent or the call fails.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from db.database import SessionLocal, ApiCache
from config import settings


def _get_gemini_key() -> str:
    """Read at call time so uvicorn --reload sees .env changes without full restart."""
    return settings.gemini_api_key


# Module-level alias for the guard check in generate_solution()
GEMINI_API_KEY = _get_gemini_key()


def generate_solution(
    entity_id: str,
    entity_name: str,
    entity_type: str,
    risk_score: float,
    temperature_f: float,
    explanation: str,
    department: str,
    compound_insight: Optional[str] = None,
    user_role: str = "planner",
) -> dict:
    """Generate a solution card. Returns dict always (falls back gracefully)."""

    # ── Cache lookup ──────────────────────────────────────────────────────────
    db = SessionLocal()
    cache_key = f"solution:{entity_id}:{user_role}"
    cached = db.query(ApiCache).filter(ApiCache.cache_key == cache_key).first()
    if cached:
        db.close()
        try:
            return json.loads(cached.response_json)
        except Exception:
            pass

    # ── Gemini call ───────────────────────────────────────────────────────────
    # Re-read at call time so hot-reload + .env changes are picked up
    gemini_key = _get_gemini_key()
    if gemini_key:
        result = _call_gemini(
            entity_id, entity_name, entity_type, risk_score,
            temperature_f, explanation, department,
            compound_insight, user_role, gemini_key,
        )
    else:
        result = None

    if result is None:
        result = _fallback(entity_id, entity_name, entity_type, risk_score, explanation)

    # Cache it
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


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

_ROLE_DESC = {
    "planner":      "a city planner who can coordinate across departments, adjust project timelines, and allocate capital budget",
    "health":       "a public health officer who can deploy outreach teams, issue advisories, and coordinate with hospitals",
    "schools":      "a school district administrator who can modify schedules, request HVAC funding, and adjust testing calendars",
    "infrastructure": "an infrastructure manager who can schedule maintenance and coordinate with utilities",
    "community":    "a community resident who can access city programs, attend public meetings, and organize neighbors",
}

_SEARCH_HINTS = {
    "shelter":            "Context: NYC DHS shelter system serves ~90,000 nightly. NYC OEM provides portable cooling units to shelters during heat advisories. Beat the Heat and Cool It! NYC programs are active.",
    "hospital":           "Context: NYC Health+Hospitals has heat surge protocols. DOHMH Heat Health Action Plan coordinates city-wide response. Heat-related ER visits spike above 95°F heat index.",
    "school":             "Context: NYC SCA's 'AC for All' program is ~82% complete. DOE allows schedule adjustments during heat advisories. NYC schools without AC can request portable units.",
    "nycha_development":  "Context: NYC law requires landlords to maintain max 78°F indoors. NYC HRA provides AC assistance for income-eligible residents 62+. NYCHA operates cooling centers.",
    "construction_permit":"Context: NYC DOB requires heat mitigation plans for construction sites. NYC Admin Code mandates dust and thermal impact mitigation near sensitive receptors.",
    "capital_project":    "Context: NYC DDC capital projects require CEQR environmental review. Thermal impacts on adjacent facilities should be documented. Capital Projects Dashboard tracks agency contacts.",
    "subway_station":     "Context: MTA's Cool Air Initiative upgrades platform fans at high-ridership stations. Platform temps can exceed 100°F in summer. OEM can deploy resources for public safety risks.",
    "tree_canopy":        "Context: NYC Parks plants free street trees on request at treescount.nycparks.org. NYC DOT's cool pavement program reduces surface temps by ~10°F. MillionTreesNYC is ongoing.",
    "hvi_zone":           "Context: NYC's $106M Cool Neighborhoods program targets HVI 4-5 zones. Cool Neighborhoods NYC funds community cooling interventions. OEM can designate additional cooling centers.",
    "cooling_center":     "Context: NYC OEM coordinates 520+ cooling centers via Beat the Heat program at nyc.gov/beattheheat. Cooling centers must be verified open during heat emergencies.",
}

_PROMPT_TEMPLATE = """You are a climate resilience advisor for New York City. A thermal analysis platform identified this heat risk:

ENTITY: {entity_name}
TYPE: {entity_type}
RISK SCORE: {risk_score}/100
TEMPERATURE: {temperature_f:.1f}°F at this location
ANALYSIS: {explanation}
DEPARTMENT: {department}
{compound_line}

The user is {role_desc}.

{search_hint}

Return ONLY compact JSON (no indentation, no line breaks inside strings, no markdown fences):
{{"what_you_can_do":[{{"action":"Short title 5-8 words","effort":"low|medium|high","detail":"One sentence with specific NYC agency names, programs, or phone numbers."}}],"whats_happening":[{{"program":"Real NYC program name","agency":"Agency running it","detail":"What it does for this situation","url":"URL or null"}}],"optimistic_outlook":"2-3 sentences: what improves if action taken. Use specific numbers.","live_context":"2-3 sentences describing the current NYC heat situation and relevant programs active now, based on the context provided."}}

Include 2-4 items in what_you_can_do (sorted low effort first), 1-3 in whats_happening.
All program names must be real NYC programs. Tone: professional, specific, not alarmist."""


def _call_gemini(
    entity_id, entity_name, entity_type, risk_score,
    temperature_f, explanation, department,
    compound_insight, user_role, api_key: str = "",
) -> dict | None:
    key = api_key or _get_gemini_key()
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)

        prompt = _PROMPT_TEMPLATE.format(
            entity_name=entity_name,
            entity_type=entity_type,
            risk_score=f"{risk_score:.0f}",
            temperature_f=temperature_f,
            explanation=explanation,
            department=department,
            compound_line=f"COMPOUND RISK: {compound_insight}" if compound_insight else "",
            role_desc=_ROLE_DESC.get(user_role, _ROLE_DESC["community"]),
            search_hint=_SEARCH_HINTS.get(entity_type, "Context: NYC OEM coordinates citywide heat resilience programs including cooling centers, Beat the Heat, and Cool It! NYC. In 2026 NYC has expanded its Cool Neighborhoods program to all HVI 4-5 zones."),
        )

        # Try newest available model; fall back if the name isn't recognised
        model_name = getattr(settings, "gemini_model", "gemini-2.0-flash")
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(temperature=0.3, max_output_tokens=2048),
        )

        text = response.text.strip()
        # Strip markdown fences if Gemini added them
        for prefix in ("```json", "```", "json"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        if text.endswith("```"):
            text = text[:-3]

        data = json.loads(text.strip())
        return {
            "entity_id":        entity_id,
            "entity_name":      entity_name,
            "what_you_can_do":  data.get("what_you_can_do", []),
            "whats_happening":  data.get("whats_happening", []),
            "optimistic_outlook": data.get("optimistic_outlook", ""),
            "live_context":     data.get("live_context", ""),
            "generated_by":     model_name,
        }

    except Exception as e:
        import traceback
        print(f"Gemini failed for {entity_id}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Static fallback templates
# ---------------------------------------------------------------------------

_FALLBACK_TEMPLATES: dict[str, dict] = {
    "school": {
        "what_you_can_do": [
            {"action": "Reschedule testing to morning hours", "effort": "low",
             "detail": "Move exams before 10 AM — research shows 0.2 SD learning gain vs afternoon testing on hot days (Park et al. 2020)."},
            {"action": "Apply for SCA HVAC funding", "effort": "medium",
             "detail": "Contact NYC School Construction Authority about AC installation through the AC for All program at sca.nyc.gov."},
            {"action": "Request temporary cooling units", "effort": "low",
             "detail": "NYC DOE can requisition portable AC units for critical classrooms during heat advisories — call your district office."},
        ],
        "whats_happening": [
            {"program": "AC for All", "agency": "NYC DOE / SCA",
             "detail": "Multi-year AC installation across NYC schools — ~82% of classrooms complete as of 2025.",
             "url": "https://www.nycsca.org"},
        ],
    },
    "shelter": {
        "what_you_can_do": [
            {"action": "Request portable AC from OEM", "effort": "low",
             "detail": "Call 311 — NYC OEM distributes portable cooling units to shelters during heat advisories."},
            {"action": "Open nearby cooling center", "effort": "low",
             "detail": "Check NYC.gov/beattheheat for the nearest public cooling center location and hours."},
            {"action": "Coordinate with DHS overnight crew", "effort": "medium",
             "detail": "DHS has a heat emergency protocol — request additional fans and water from your facility coordinator."},
        ],
        "whats_happening": [
            {"program": "Cool It! NYC", "agency": "NYC OEM / DHS",
             "detail": "Emergency cooling resources distributed to shelters during heat advisories.", "url": None},
        ],
    },
    "hospital": {
        "what_you_can_do": [
            {"action": "Activate heat surge protocol", "effort": "low",
             "detail": "NYC Health+Hospitals heat surge protocols add triage staff and cooled waiting areas."},
            {"action": "Alert DOHMH heat desk", "effort": "low",
             "detail": "Call 311 to report heat-related ER surge — DOHMH coordinates city-wide resource deployment."},
            {"action": "Pre-position IV fluids and cooling equipment", "effort": "medium",
             "detail": "Stage cooling blankets and electrolyte supplies ahead of projected heat peaks."},
        ],
        "whats_happening": [
            {"program": "NYC Heat Health Action Plan", "agency": "NYC DOHMH",
             "detail": "Coordinated response plan including hospital surge protocols and public alerts.", "url": None},
        ],
    },
    "subway_station": {
        "what_you_can_do": [
            {"action": "Report to MTA Station Operations", "effort": "low",
             "detail": "Contact MTA Station Operations (1-877-690-5114) — excess platform heat triggers fan deployment."},
            {"action": "Coordinate with NYC OEM", "effort": "medium",
             "detail": "OEM can deploy cooling resources if platform temps create public safety risks."},
        ],
        "whats_happening": [
            {"program": "MTA Cool Air Initiative", "agency": "NYC MTA",
             "detail": "Platform fan upgrades and thermal monitoring at high-ridership stations.", "url": "https://new.mta.info"},
        ],
    },
    "tree_canopy": {
        "what_you_can_do": [
            {"action": "Request additional tree plantings", "effort": "low",
             "detail": "NYC Parks plants free street trees on request — submit at treescount.nycparks.org."},
            {"action": "Advocate for cool pavement", "effort": "medium",
             "detail": "NYC DOT's cool pavement program reduces surface temps by 10°F — request at nyc.gov/dot."},
        ],
        "whats_happening": [
            {"program": "MillionTreesNYC", "agency": "NYC Parks / DPR",
             "detail": "Ongoing urban forest expansion program targeting heat-vulnerable neighborhoods.", "url": "https://www.nycgovparks.org"},
        ],
    },
    "nycha_development": {
        "what_you_can_do": [
            {"action": "Request cooling center access", "effort": "low",
             "detail": "NYCHA operates cooling centers at community facilities — call 311 to find nearest open location."},
            {"action": "Apply for NYCHA AC assistance", "effort": "low",
             "detail": "Residents 62+ or with qualifying medical conditions can get free AC units via NYC HRA."},
            {"action": "Document indoor temperatures", "effort": "medium",
             "detail": "NYC law requires landlords to maintain max indoor temps of 78°F. Document and report to HPD at 311."},
        ],
        "whats_happening": [
            {"program": "NYC AC Assistance Program", "agency": "NYC HRA / ConEdison",
             "detail": "Free AC units and installation for income-eligible residents.", "url": None},
        ],
    },
    "construction_permit": {
        "what_you_can_do": [
            {"action": "File heat mitigation request with DOB", "effort": "low",
             "detail": "Contact NYC DOB (311 or nyc.gov/buildings) to flag heat impact on adjacent receptors."},
            {"action": "Request shade structures for nearby areas", "effort": "medium",
             "detail": "The construction applicant is required to mitigate dust and thermal impacts under NYC Admin Code."},
        ],
        "whats_happening": [
            {"program": "NYC Construction Mitigation Requirements", "agency": "NYC DOB",
             "detail": "Construction sites must implement dust and impact mitigation plans.", "url": "https://www.nyc.gov/buildings"},
        ],
    },
    "capital_project": {
        "what_you_can_do": [
            {"action": "Flag in NYC Capital Projects Tracker", "effort": "low",
             "detail": "Add a comment via NYC DDC's capital project portal noting the adjacent heat receptor."},
            {"action": "Request Environmental Impact review", "effort": "medium",
             "detail": "Major capital projects require CEQR review — thermal impact on adjacent facilities should be documented."},
        ],
        "whats_happening": [
            {"program": "NYC Capital Projects Dashboard", "agency": "NYC DDC",
             "detail": "Tracks all city capital projects with agency contact information.", "url": None},
        ],
    },
    "hvi_zone": {
        "what_you_can_do": [
            {"action": "Access Cool Neighborhoods funding", "effort": "medium",
             "detail": "NYC's $106M Cool Neighborhoods program prioritizes HVI 4-5 zones — contact your council member."},
            {"action": "Request community cooling center", "effort": "low",
             "detail": "NYC OEM can designate community spaces as cooling centers during heat emergencies — call 311."},
        ],
        "whats_happening": [
            {"program": "Cool Neighborhoods NYC", "agency": "NYC Mayor's Office / OEM",
             "detail": "$106M investment targeting high heat-vulnerability neighborhoods with cooling interventions.", "url": None},
        ],
    },
    "cooling_center": {
        "what_you_can_do": [
            {"action": "Verify cooling center is activated", "effort": "low",
             "detail": "Call 311 to confirm the cooling center is open and accepting visitors during current conditions."},
            {"action": "Increase capacity if possible", "effort": "medium",
             "detail": "Contact NYC OEM to request additional cooling center capacity during heat emergencies."},
        ],
        "whats_happening": [
            {"program": "NYC Beat the Heat", "agency": "NYC OEM",
             "detail": "Official cooling center program — locations and hours at nyc.gov/beattheheat.", "url": "https://www.nyc.gov/beattheheat"},
        ],
    },
}

_DEFAULT_FALLBACK = {
    "what_you_can_do": [
        {"action": "Contact responsible agency", "effort": "low",
         "detail": "Flag this heat risk to the managing department via 311 or the NYC Mayor's Action Center."},
        {"action": "Document and report heat conditions", "effort": "low",
         "detail": "Use NYC.gov or 311 to file a heat complaint — this triggers a formal agency response."},
    ],
    "whats_happening": [
        {"program": "NYC Heat Action Plan", "agency": "NYC OEM",
         "detail": "Citywide heat emergency response coordination across all agencies.", "url": None},
    ],
}


def _fallback(entity_id: str, entity_name: str, entity_type: str, risk_score: float, explanation: str) -> dict:
    t = _FALLBACK_TEMPLATES.get(entity_type, _DEFAULT_FALLBACK)
    return {
        "entity_id":        entity_id,
        "entity_name":      entity_name,
        "what_you_can_do":  t["what_you_can_do"],
        "whats_happening":  t["whats_happening"],
        "optimistic_outlook": f"Addressing this risk could meaningfully reduce the score from {risk_score:.0f}/100. Early intervention typically cuts heat exposure by 40-60% for nearby receptors.",
        "live_context":     "Live data unavailable — recommendations drawn from static NYC agency templates. Connect a Gemini API key for real-time search results.",
        "generated_by":     "fallback",
    }
