"""
GET /api/network?city=nyc&role=planner

Returns Cytoscape.js-compatible graph elements for the compound risk subgraph.

Only entities that participate in at least one compound risk are returned —
the user sees the interesting cross-silo connections, not all 9,700 entities.

Filtered by user role: each role sees only the entity types relevant to them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db.database import Entity, get_db
from core.tile_lookup import tile_service
from core.factors import FACTOR_REGISTRY
from core.compound_risk import find_compound_risks

router = APIRouter(prefix="/api", tags=["network"])

# ── Role → allowed entity types (None = all) ──────────────────────────────────
# Note: every role must include at least one emitter type to generate compound risks.
# "community" loads emitters too (construction_permit, capital_project) but then
# filters edges to those where the receptor is a community-care entity.
ROLE_ENTITY_FILTER: dict[str, list[str] | None] = {
    "planner":        None,
    "health":         ["hospital", "shelter", "hvi_zone", "nycha_development",
                       "construction_permit", "capital_project", "subway_station"],
    "schools":        ["school", "construction_permit", "capital_project"],
    "infrastructure": ["subway_station", "capital_project", "construction_permit"],
    "community":      ["school", "shelter", "hvi_zone", "nycha_development",
                       "tree_canopy", "cooling_center",
                       # emitters that affect community receptors:
                       "construction_permit", "capital_project", "subway_station"],
    "multi":          None,
}

# For community role: only keep compound risks where the receptor is one of these
COMMUNITY_RECEPTOR_TYPES = {"school", "shelter", "hvi_zone", "nycha_development"}

# Department → hex colour for node styling
DEPT_COLORS: dict[str, str] = {
    "NYC DOB (Dept of Buildings)":                "#f97316",
    "NYC DDC (Dept of Design & Construction)":    "#f59e0b",
    "NYC DPR (Dept of Parks & Recreation)":       "#22c55e",
    "NYC Parks & Recreation (DPR)":               "#22c55e",
    "NYC DOT (Dept of Transportation)":           "#f97316",
    "NYC DEP (Dept of Environmental Protection)": "#06b6d4",
    "NYC DOE (Dept of Education)":                "#8b5cf6",
    "NYC NYCHA (Housing Authority)":              "#ef4444",
    "NYC MTA (Metropolitan Transportation Authority)": "#3b82f6",
    "NYC MTA (Transit Authority)":                "#3b82f6",
    "NYC Health+Hospitals / DOHMH":               "#f43f5e",
    "NYC DOHMH (Health & Mental Hygiene)":        "#f43f5e",
    "NYC DHS (Dept of Homeless Services)":        "#dc2626",
    "NYC OEM (Office of Emergency Management)":   "#eab308",
    "NYC HHC (Health + Hospitals Corp)":          "#f43f5e",
    "NYC SCA (School Construction Authority)":    "#8b5cf6",
    "NYC HPD (Housing Preservation & Development)": "#a855f7",
}

ROLE_COLORS: dict[str, str] = {
    "emitter":  "#f97316",
    "receptor": "#ef4444",
    "both":     "#8b5cf6",
    "sink":     "#22c55e",
}

ENTITY_ICONS: dict[str, str] = {
    "school":             "🎓",
    "hospital":           "🏥",
    "shelter":            "🏠",
    "tree_canopy":        "🌳",
    "cooling_center":     "❄️",
    "capital_project":    "🏗️",
    "construction_permit":"🚧",
    "nycha_development":  "🏢",
    "subway_station":     "🚇",
    "hvi_zone":           "🌡️",
}


def _dept_color(dept: str) -> str:
    return DEPT_COLORS.get(dept, "#6b7280")


@router.get("/network")
def get_network_graph(
    city:               str   = Query("nyc"),
    role:               str   = Query("planner"),
    max_entities:       int   = Query(2000),
    compound_radius_m:  float = Query(500),
    min_receptor_score: float = Query(20),
    node_limit:         int   = Query(50, le=300),
    min_risk:           float = Query(0),
    db:                 Session = Depends(get_db),
):
    # 1. Load entities with role-based type filter ──────────────────────────────
    q            = db.query(Entity).filter(Entity.city == city)
    allowed      = ROLE_ENTITY_FILTER.get(role)
    if allowed is not None:
        q = q.filter(Entity.entity_type.in_(allowed))
    entities = q.limit(max_entities).all()

    # 2. Compute factor results ─────────────────────────────────────────────────
    factor_results = []
    for entity in entities:
        env_params = tile_service.get_full_env_params(entity.lat, entity.lon)
        if env_params is None:
            continue
        factor_fn = FACTOR_REGISTRY.get(entity.entity_type)
        if factor_fn is None:
            continue
        try:
            result = factor_fn(entity, env_params)
            factor_results.append(result)
        except Exception:
            continue

    # 3. Find compound risks ────────────────────────────────────────────────────
    compounds = find_compound_risks(
        factor_results,
        max_distance_m=compound_radius_m,
        min_receptor_score=min_receptor_score,
    )

    # For community role: filter to risks where the receptor is a community entity
    if role == "community":
        compounds = [
            c for c in compounds
            if c.receptor.entity_type in COMMUNITY_RECEPTOR_TYPES
        ]

    # 4. Build involved-entity set ──────────────────────────────────────────────
    involved_ids: set[str] = set()
    for c in compounds:
        involved_ids.add(c.emitter.entity_id)
        involved_ids.add(c.receptor.entity_id)

    result_map = {r.entity_id: r for r in factor_results}

    # 5. Connection-count per entity ────────────────────────────────────────────
    conn_counts: dict[str, int] = {}
    for c in compounds:
        conn_counts[c.emitter.entity_id]  = conn_counts.get(c.emitter.entity_id, 0)  + 1
        conn_counts[c.receptor.entity_id] = conn_counts.get(c.receptor.entity_id, 0) + 1

    # 6. Build Cytoscape node list ──────────────────────────────────────────────
    nodes = []
    for eid in involved_ids:
        r = result_map.get(eid)
        if r is None:
            continue
        if r.risk_score < min_risk:
            continue
        connections = conn_counts.get(eid, 0)
        nodes.append({
            "data": {
                "id":               eid,
                "label":            r.entity_name[:40],
                "entity_type":      r.entity_type,
                "role":             r.role,
                "risk_score":       round(r.risk_score, 1),
                "temperature_f":    round(r.temperature_f, 1),
                "department":       r.department,
                "explanation":      r.explanation,
                "data_source":      r.data_source,
                "connection_count": connections,
                "icon":             ENTITY_ICONS.get(r.entity_type, "📍"),
                # Cytoscape styling data
                "color":            _dept_color(r.department),
                "border_color":     ROLE_COLORS.get(r.role, "#6b7280"),
                "size":             max(20, 20 + connections * 6),
                # Map-sync
                "lat":              r.lat,
                "lon":              r.lon,
            }
        })

    # Limit to top N most-connected nodes so the graph stays readable
    nodes.sort(key=lambda n: n["data"]["connection_count"], reverse=True)
    nodes = nodes[:node_limit]
    node_ids = {n["data"]["id"] for n in nodes}

    # 7. Build Cytoscape edge list ──────────────────────────────────────────────
    edges = []
    for i, c in enumerate(compounds):
        # Only include edges where both endpoints are in the node set
        if c.emitter.entity_id not in node_ids or c.receptor.entity_id not in node_ids:
            continue
        score = c.compound_score
        edges.append({
            "data": {
                "id":             f"edge-{i}",
                "source":         c.emitter.entity_id,
                "target":         c.receptor.entity_id,
                "compound_score": round(score, 1),
                "distance_m":     round(c.distance_m, 0),
                "insight":        c.insight,
                "departments":    c.departments,
                "width":          1 + (score / 25),   # 1–5 px
                "color":          ("#ef4444" if score >= 70
                                   else "#f59e0b" if score >= 40
                                   else "#6b7280"),
            }
        })

    # 8. Department summary for legend ─────────────────────────────────────────
    dept_counts: dict[str, int] = {}
    for n in nodes:
        dept = n["data"]["department"]
        dept_counts[dept] = dept_counts.get(dept, 0) + 1

    return {
        "elements": {"nodes": nodes, "edges": edges},
        "summary": {
            "node_count":   len(nodes),
            "edge_count":   len(edges),
            "departments":  dept_counts,
            "role_filter":  role,
        },
    }
