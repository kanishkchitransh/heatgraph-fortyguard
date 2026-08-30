"""
Load NYC 2015 Street Tree Census, aggregated to geohash-6 blocks.

We do NOT load 683,788 individual trees as entities. Instead:
  1. Read every living tree's lat/lon and trunk diameter (tree_dbh)
  2. Assign each tree to a geohash-6 cell (~610m × 610m)
  3. For each cell: tree_count, avg_dbh, estimated cooling_delta_c
  4. Store one Entity per cell as a SINK node

SINK nodes cool the temperature field — they counteract nearby emitters.
Each 10 % increase in canopy cover → −0.5 to −1.3 °C local cooling
(Akbari et al. 2001; NYC CAPA urban heat study).

Source: NYC Parks & Recreation 2015 Street Tree Census
Dept  : NYC Parks & Recreation (DPR)
"""
import csv
import json
import math
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Entity, SessionLocal, init_db

# ── Geohash (precision-6 only) ─────────────────────────────────────────────
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def _geohash6(lat: float, lon: float) -> str:
    lat_r = [-90.0, 90.0]
    lon_r = [-180.0, 180.0]
    buf, bit, ch, even = [], 0, 0, True
    while len(buf) < 6:
        if even:
            mid = (lon_r[0] + lon_r[1]) / 2
            if lon >= mid:
                ch |= 1 << (4 - bit); lon_r[0] = mid
            else:
                lon_r[1] = mid
        else:
            mid = (lat_r[0] + lat_r[1]) / 2
            if lat >= mid:
                ch |= 1 << (4 - bit); lat_r[0] = mid
            else:
                lat_r[1] = mid
        even = not even
        bit += 1
        if bit == 5:
            buf.append(_BASE32[ch]); bit = 0; ch = 0
    return "".join(buf)


_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "nyc")


def load_trees(filepath: str | None = None) -> None:
    if filepath is None:
        filepath = os.path.join(_DATA_DIR, "street_trees_2015.csv")
    init_db()

    print("  Reading street tree census (683K rows — ~60 s)…")
    cells: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "dbh_sum": 0.0, "lats": [], "lons": [], "nta": ""
    })

    total = skipped = 0
    try:
        with open(filepath, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                # Skip dead / stump trees
                status = (row.get("status") or "").strip().lower()
                if status in ("dead", "stump", ""):
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

                try:
                    dbh = float(row.get("tree_dbh") or 0)
                except (ValueError, TypeError):
                    dbh = 0.0

                nta = row.get("nta_name") or row.get("nta") or ""
                gh  = _geohash6(lat, lon)
                c   = cells[gh]
                c["n"]        += 1
                c["dbh_sum"]  += dbh
                c["lats"].append(lat)
                c["lons"].append(lon)
                if nta and not c["nta"]:
                    c["nta"] = nta

                if total % 100_000 == 0:
                    print(f"  … {total:,} trees read, {len(cells):,} cells so far")

    except FileNotFoundError:
        print(f"  ERROR: {filepath} not found.")
        return

    print(f"  Done reading: {total:,} rows, {skipped:,} skipped, {len(cells):,} geohash-6 cells")

    # ── Write one Entity per cell ─────────────────────────────────────────────
    db     = SessionLocal()
    stored = 0

    for gh, c in cells.items():
        n       = c["n"]
        avg_dbh = c["dbh_sum"] / n if n else 0.0
        c_lat   = sum(c["lats"]) / n
        c_lon   = sum(c["lons"]) / n

        # Crown radius ≈ dbh_inches × 0.152 m  (allometric, USFS)
        avg_crown_r_m  = avg_dbh * 0.152
        avg_canopy_m2  = math.pi * avg_crown_r_m ** 2
        total_canopy_m2 = avg_canopy_m2 * n

        # Cooling estimate (conservative Akbari et al.)
        if n >= 100:
            density = "dense";    cooling_c = 2.0
        elif n >= 50:
            density = "moderate"; cooling_c = 1.2
        elif n >= 20:
            density = "sparse";   cooling_c = 0.6
        else:
            density = "minimal";  cooling_c = 0.2

        entity_id = f"trees-{gh}"
        extra = {
            "tree_count":      n,
            "avg_dbh_inches":  round(avg_dbh, 1),
            "total_canopy_m2": round(total_canopy_m2),
            "canopy_density":  density,
            "cooling_c":       cooling_c,
            "geohash":         gh,
        }

        existing = db.get(Entity, entity_id)
        if existing:
            existing.name       = f"Tree canopy · {n} trees ({density})"
            existing.lat        = c_lat
            existing.lon        = c_lon
            existing.extra_json = json.dumps(extra)
        else:
            db.add(Entity(
                id          = entity_id,
                name        = f"Tree canopy · {n} trees ({density})",
                entity_type = "tree_canopy",
                role        = "sink",
                city        = "nyc",
                lat         = c_lat,
                lon         = c_lon,
                address     = c["nta"],
                extra_json  = json.dumps(extra),
            ))
        stored += 1
        if stored % 500 == 0:
            db.commit()

    db.commit()
    db.close()
    print(f"✅  Tree canopy: {stored} block entities stored (from {total:,} trees)")


if __name__ == "__main__":
    load_trees()
