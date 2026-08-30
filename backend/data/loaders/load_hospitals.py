"""
Load hospitals, clinics, nursing homes, and senior centres from NYC FacDB.

These are RECEPTOR nodes — healthcare facilities where heat-driven ER surges
manifest. Connects the factor graph to NYC Health+Hospitals / DOHMH.

Filters FacDB to health-related facsubgrp values. Excludes fire services
(which appeared in early keyword searches) and school-based clinics (already
captured by the school entity type).

Source: NYC Facilities Database (FacDB) — updated 2026-07
"""
import csv
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Entity, SessionLocal, init_db

# facsubgrp values to INCLUDE (all verified to be real healthcare facilities)
HEALTH_SUBGROUPS = {
    "HOSPITALS AND CLINICS",
    "RESIDENTIAL HEALTH CARE",      # nursing homes
    "SENIOR SERVICES",
    "MENTAL HEALTH",
    "SUBSTANCE USE DISORDER TREATMENT PROGRAMS",
    "OTHER EMERGENCY SERVICES",     # ambulance stations (emitters: generate heat in dense areas)
}

# factype → entity subtype
def _subtype(factype: str) -> str:
    ft = factype.upper()
    if "HOSPITAL" in ft and "EXTENSION" not in ft and "SCHOOL" not in ft:
        return "hospital"
    if "NURSING HOME" in ft:
        return "nursing_home"
    if "SENIOR CENTER" in ft:
        return "senior_center"
    if "MENTAL HEALTH" in ft or "PSYCHIATRIC" in ft:
        return "psychiatric_facility"
    if "SUBSTANCE" in ft or "OPIOID" in ft or "DETOX" in ft:
        return "substance_treatment"
    return "health_clinic"


_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "nyc")


def load_hospitals(filepath: str | None = None) -> None:
    if filepath is None:
        filepath = os.path.join(_DATA_DIR, "facilities_database.csv")
    init_db()
    db = SessionLocal()
    inserted = skipped = 0

    try:
        with open(filepath, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                facsubgrp = (row.get("facsubgrp") or "").strip().upper()
                if facsubgrp not in HEALTH_SUBGROUPS:
                    continue

                # Skip school-based clinics — already folded into school entities
                factype = (row.get("factype") or "").strip()
                if "SCHOOL BASED" in factype.upper():
                    skipped += 1
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

                uid       = (row.get("uid") or "").strip()
                facname   = (row.get("facname") or factype or "Healthcare Facility").strip()
                facgroup  = (row.get("facgroup") or "").strip()
                borough   = (row.get("boro") or "").strip()
                address   = f"{row.get('addressnum','')} {row.get('streetname','')}".strip()
                opname    = (row.get("opname") or "").strip()

                try:
                    capacity = int(row.get("capacity") or 0)
                except (ValueError, TypeError):
                    capacity = 0

                subtype = _subtype(factype)
                entity_id = f"health-{uid}" if uid else f"health-{inserted:05d}"

                extra = {
                    "subtype":   subtype,
                    "factype":   factype,
                    "facgroup":  facgroup,
                    "facsubgrp": facsubgrp,
                    "borough":   borough,
                    "capacity":  capacity if capacity > 0 else None,
                    "operator":  opname,
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
                        entity_type = "hospital",
                        role        = "receptor",
                        city        = "nyc",
                        lat         = lat,
                        lon         = lon,
                        address     = address[:200],
                        extra_json  = json.dumps(extra),
                    ))
                inserted += 1

                if inserted % 200 == 0:
                    db.commit()
                    print(f"  ... {inserted} healthcare facilities")

    except FileNotFoundError:
        print(f"  ERROR: {filepath} not found.")
        db.close()
        return

    db.commit()
    db.close()
    print(f"✅  Healthcare: inserted/updated={inserted}  skipped={skipped}")


if __name__ == "__main__":
    load_hospitals()
