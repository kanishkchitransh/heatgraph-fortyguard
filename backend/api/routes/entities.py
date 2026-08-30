"""
Entity routes — factor graph nodes (receptors, emitters, sinks).

GET /api/entities
  city        — always "nyc"
  type        — entity_type filter (school | construction_permit | …)
  role        — emitter | receptor | both | sink
  limit       — max records (default 1000, max 5000)
  offset      — pagination offset
  bbox        — "min_lon,min_lat,max_lon,max_lat"  (viewport filter)
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db.database import Entity, get_db

router = APIRouter(prefix="/api", tags=["entities"])


@router.get("/entities")
def list_entities(
    city:   str       = Query("nyc",  description="city filter (nyc only)"),
    type:   str | None = Query(None,  description="entity_type filter"),
    role:   str | None = Query(None,  description="emitter | receptor | both | sink"),
    limit:  int       = Query(1000,   le=5000),
    offset: int       = Query(0),
    bbox:   str | None = Query(None,  description="min_lon,min_lat,max_lon,max_lat"),
    db: Session = Depends(get_db),
):
    q = db.query(Entity).filter(Entity.city == city.lower())

    if type:
        q = q.filter(Entity.entity_type == type)
    if role:
        q = q.filter(Entity.role == role)
    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = [float(x) for x in bbox.split(",")]
            q = q.filter(
                Entity.lat >= min_lat, Entity.lat <= max_lat,
                Entity.lon >= min_lon, Entity.lon <= max_lon,
            )
        except (ValueError, IndexError):
            pass

    total = q.count()
    rows  = q.offset(offset).limit(limit).all()

    return {
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "entities": [
            {
                "id":          e.id,
                "name":        e.name,
                "entity_type": e.entity_type,
                "role":        e.role,
                "lat":         e.lat,
                "lon":         e.lon,
                "city":        e.city,
                "address":     e.address,
                "extra":       e.attributes,
            }
            for e in rows
        ],
    }
