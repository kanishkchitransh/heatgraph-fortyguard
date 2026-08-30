"""
CPDB Capital Projects Loader — cpdb_26exec edition
===================================================
Reads cpdb_projects_pts.shp (EPSG:2263 NY State Plane Long Island Feet)
+ cpdb_projects_pts.dbf, reprojects to WGS84, and upserts Entity rows
with entity_type="capital_project" and role="emitter".

Capital projects are active city infrastructure works that raise ambient
temperature on nearby blocks — the same cross-silo emitter role as DOB
permits but at larger scale and longer duration.

Usage:
    cd backend
    python data/loaders/load_cpdb.py --shp C:/path/to/cpdb_projects_pts.shp
    # default path is the temp extract location used during dev
"""
import argparse
import json
import sys
import os

# ── ensure backend/ is on the path when run directly ──────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shapefile
from pyproj import Transformer

from db.database import Entity, SessionLocal, init_db

# NY State Plane Long Island Feet → WGS84
_TRANSFORMER = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True)

# City-of-NY agency acronym → friendly department label
AGENCY_DEPT = {
    "DDC":  "NYC DDC (Dept of Design & Construction)",
    "DEP":  "NYC DEP (Dept of Environmental Protection)",
    "DPR":  "NYC DPR (Dept of Parks & Recreation)",
    "DOT":  "NYC DOT (Dept of Transportation)",
    "HPD":  "NYC HPD (Housing Preservation & Development)",
    "HHC":  "NYC HHC (Health + Hospitals Corp)",
    "ACS":  "NYC ACS (Admin for Children's Services)",
    "DOE":  "NYC DOE (Dept of Education)",
    "NYCTA": "NYC MTA (Transit Authority)",
    "ORE":  "NYC ORE (Office of Real Estate)",
    "DCAS": "NYC DCAS (Citywide Admin Services)",
    "DHS":  "NYC DHS (Dept of Homeless Services)",
    "DOHMH": "NYC DOHMH (Health & Mental Hygiene)",
    "NYCHA": "NYC NYCHA (Housing Authority)",
    "SCA":  "NYC SCA (School Construction Authority)",
}


def _dept(acro: str) -> str:
    return AGENCY_DEPT.get(acro.strip().upper(), f"NYC {acro.strip()} (Capital Projects)")


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default


def load_cpdb(shp_path: str, dry_run: bool = False) -> None:
    if not os.path.exists(shp_path):
        print(f"ERROR: SHP not found: {shp_path}")
        sys.exit(1)

    dbf_path = shp_path.replace(".shp", ".dbf")
    if not os.path.exists(dbf_path):
        print(f"ERROR: DBF not found: {dbf_path}")
        sys.exit(1)

    init_db()
    db = SessionLocal()

    reader = shapefile.Reader(shp_path)
    fields = [f[0] for f in reader.fields[1:]]  # skip deletion flag
    print(f"SHP records : {reader.numRecords}")
    print(f"DBF fields  : {', '.join(fields)}")

    inserted = 0
    skipped  = 0
    errors   = 0

    for sr in reader.shapeRecords():
        # Geometry ───────────────────────────────────────────────────────────
        if sr.shape.shapeType == 0:       # null shape
            skipped += 1
            continue
        state_x, state_y = sr.shape.points[0]   # feet, EPSG:2263
        try:
            lon, lat = _TRANSFORMER.transform(state_x, state_y)
        except Exception:
            skipped += 1
            continue

        # Basic NYC bounding box sanity check
        if not (40.45 <= lat <= 40.95 and -74.30 <= lon <= -73.65):
            skipped += 1
            continue

        # Attributes ─────────────────────────────────────────────────────────
        rec = dict(zip(fields, sr.record))

        maprojid   = str(rec.get("maprojid", "")).strip()
        magenacro  = str(rec.get("magenacro", "")).strip()
        magenname  = str(rec.get("magenname", "")).strip()
        descript   = str(rec.get("descript", "")).strip()
        typecat    = str(rec.get("typecat",   "")).strip()   # "Fixed Asset" | "Lump Sum"
        mindate    = str(rec.get("mindate",   "")).strip()
        maxdate    = str(rec.get("maxdate",   "")).strip()
        pctotal    = _safe_float(rec.get("pctotal", 0))

        if not maprojid:
            skipped += 1
            continue

        entity_id   = f"cpdb_{maprojid}"
        entity_name = descript[:120] if descript else maprojid
        department  = _dept(magenacro)

        extra = {
            "maprojid":   maprojid,
            "agency":     magenacro,
            "agency_name": magenname,
            "description": descript,
            "typecat":    typecat,
            "mindate":    mindate,
            "maxdate":    maxdate,
            "pctotal":    pctotal,
            "department": department,
            # Thermal perturbation estimate:
            # Fixed-asset construction (roads, sewers, parks) → +1.5°F local delta
            # Lump-sum / administrative allocations → +0.5°F (indirect)
            "delta_t_f":  1.5 if "fixed" in typecat.lower() else 0.5,
        }

        if dry_run:
            print(f"  DRY {entity_id}: {entity_name[:60]} @ ({lat:.4f}, {lon:.4f})  dept={department}")
            inserted += 1
            continue

        existing = db.get(Entity, entity_id)
        if existing:
            existing.name       = entity_name
            existing.lat        = lat
            existing.lon        = lon
            existing.extra_json = json.dumps(extra)
        else:
            db.add(Entity(
                id          = entity_id,
                name        = entity_name,
                entity_type = "capital_project",
                role        = "emitter",
                city        = "nyc",
                lat         = lat,
                lon         = lon,
                address     = "",
                extra_json  = json.dumps(extra),
            ))
        inserted += 1

        if inserted % 500 == 0:
            db.commit()
            print(f"  ... committed {inserted} so far")

    if not dry_run:
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"ERROR on final commit: {e}")
            errors += 1
        finally:
            db.close()

    print(f"\n✅  Done — inserted/updated: {inserted}  skipped: {skipped}  errors: {errors}")
    reader.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load CPDB capital project points into HeatGraph entities table")
    parser.add_argument(
        "--shp",
        default=os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "nyc", "cpdb_projects_pts.shp"),
        help="Path to cpdb_projects_pts.shp",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print rows without writing to DB")
    args = parser.parse_args()
    load_cpdb(args.shp, dry_run=args.dry_run)
