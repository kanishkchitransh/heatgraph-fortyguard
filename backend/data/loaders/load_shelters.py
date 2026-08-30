"""
Load homeless shelters and supportive housing from two sources:

  1. NYC FacDB — facsubgrp "NON-RESIDENTIAL HOUSING AND HOMELESS SERVICES"
     (supportive SRO, housing support, drop-in centres, rapid re-housing)

  2. NYC Evacuation Centers (p5md-weyf) — public schools and community
     centres that double as cooling centres; loaded as role="sink"
     (they REDUCE heat-related mortality when activated)

Shelters are RECEPTOR nodes — DHS responsibility. NYC's 90,000+ nightly
shelter residents are among the city's most heat-vulnerable population.

Cooling/evacuation centres are SINK nodes — heat mitigation infrastructure.
"""
import csv
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Entity, SessionLocal, init_db

SHELTER_SUBGROUPS = {
    "NON-RESIDENTIAL HOUSING AND HOMELESS SERVICES",
}

_POINT_RE = re.compile(r"POINT\s*\(\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*\)")


def _parse_point(wkt: str):
    """Parse 'POINT (lon lat)' → (lat, lon) or None."""
    m = _POINT_RE.match(wkt.strip())
    if m:
        return float(m.group(2)), float(m.group(1))
    return None, None


_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "nyc")


def load_shelters(
    facdb_path: str | None = None,
    cooling_path: str | None = None,
) -> None:
    if facdb_path is None:
        facdb_path = os.path.join(_DATA_DIR, "facilities_database.csv")
    if cooling_path is None:
        cooling_path = os.path.join(_DATA_DIR, "evacuation_centers.csv")
    init_db()
    db = SessionLocal()
    shelters_in = skipped = cooling_in = 0

    # ── Part 1: FacDB homeless services ───────────────────────────────────────
    try:
        with open(facdb_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                facsubgrp = (row.get("facsubgrp") or "").strip().upper()
                if facsubgrp not in SHELTER_SUBGROUPS:
                    continue

                try:
                    lat = float(row.get("latitude") or "")
                    lon = float(row.get("longitude") or "")
                except (ValueError, TypeError):
                    skipped += 1
                    continue

                if not (40.4 < lat < 41.0 and -74.3 < lon < -73.7):
                    skipped += 1
                    continue

                uid      = (row.get("uid") or "").strip()
                facname  = (row.get("facname") or "Shelter").strip()
                factype  = (row.get("factype") or "").strip()
                borough  = (row.get("boro") or "").strip()
                address  = f"{row.get('addressnum','')} {row.get('streetname','')}".strip()
                opname   = (row.get("opname") or "").strip()

                try:
                    capacity = int(row.get("capacity") or 0)
                except (ValueError, TypeError):
                    capacity = 0

                entity_id = f"shelter-{uid}" if uid else f"shelter-{shelters_in:05d}"

                extra = {
                    "factype":   factype,
                    "facsubgrp": facsubgrp,
                    "borough":   borough,
                    "capacity":  capacity if capacity > 0 else None,
                    "operator":  opname,
                    "has_ac":    False,   # conservative: assume no AC unless known
                    "source":    "facdb",
                }

                existing = db.get(Entity, entity_id)
                if existing:
                    existing.name       = facname[:200]
                    existing.lat        = lat
                    existing.lon        = lon
                    existing.extra_json = json.dumps(extra)
                else:
                    db.add(Entity(
                        id          = entity_id,
                        name        = facname[:200],
                        entity_type = "shelter",
                        role        = "receptor",
                        city        = "nyc",
                        lat         = lat,
                        lon         = lon,
                        address     = address[:200],
                        extra_json  = json.dumps(extra),
                    ))
                shelters_in += 1

    except FileNotFoundError:
        print(f"  WARNING: {facdb_path} not found — skipping FacDB shelters.")

    db.commit()
    print(f"  FacDB shelters: {shelters_in} (skipped {skipped})")

    # ── Part 2: Evacuation / cooling centres ──────────────────────────────────
    try:
        with open(cooling_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                geom = row.get("the_geom", "").strip()
                lat, lon = _parse_point(geom)
                if lat is None:
                    skipped += 1
                    continue

                if not (40.4 < lat < 41.0 and -74.3 < lon < -73.7):
                    skipped += 1
                    continue

                name     = (row.get("EC_Name") or "Evacuation Center").strip()
                address  = (row.get("ADDRESS") or "").strip()
                city_v   = (row.get("CITY") or "").strip()
                zipcode  = str(row.get("ZIP_CODE") or "").strip().split(".")[0]
                accessible = (row.get("ACCESSIBLE") or "N").strip().upper() == "Y"

                entity_id = f"cooling-{i:04d}"
                extra = {
                    "accessible": accessible,
                    "zip_code":   zipcode,
                    "city":       city_v,
                    "source":     "nyc_evacuation_centers",
                }

                existing = db.get(Entity, entity_id)
                if existing:
                    existing.name       = name[:200]
                    existing.lat        = lat
                    existing.lon        = lon
                    existing.extra_json = json.dumps(extra)
                else:
                    db.add(Entity(
                        id          = entity_id,
                        name        = name[:200],
                        entity_type = "cooling_center",
                        role        = "sink",
                        city        = "nyc",
                        lat         = lat,
                        lon         = lon,
                        address     = f"{address}, {city_v} {zipcode}".strip(", "),
                        extra_json  = json.dumps(extra),
                    ))
                cooling_in += 1

    except FileNotFoundError:
        print(f"  WARNING: {cooling_path} not found — skipping cooling centres.")

    db.commit()
    db.close()
    print(f"  Cooling/evacuation centres: {cooling_in}")
    print(f"✅  Shelters+cooling: shelter={shelters_in}  cooling={cooling_in}  skipped={skipped}")


if __name__ == "__main__":
    load_shelters()
