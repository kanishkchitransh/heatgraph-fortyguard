"""
NYCHA Public Housing Developments → receptor nodes.

Source: NYC Open Data phvi-damg (MULTIPOLYGON geometries).
216 developments, 177,000+ apartments. High-thermal-mass masonry construction,
most without central AC. 58% of NYC heat deaths occur in homes without AC.

Centroid is extracted from the MULTIPOLYGON coordinates string.

Factor graph role: Receptor — NychaReceptor computes thermal risk as
  risk = (outdoor_temp_C - 27) * occupancy_density * (1 - ac_penetration)
"""
import csv, json, os, re
from db.database import SessionLocal, Entity, init_db

DATA_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "nyc", "nycha_developments.csv"
)

def _polygon_centroid(geom_str: str) -> tuple[float, float] | None:
    """Extract mean lat/lon from a MULTIPOLYGON WKT string."""
    nums = re.findall(r"(-?\d+\.\d+)\s+(-?\d+\.\d+)", geom_str)
    if not nums:
        return None
    lons = [float(x) for x, _ in nums]
    lats = [float(y) for _, y in nums]
    return sum(lats) / len(lats), sum(lons) / len(lons)

def load_nycha():
    init_db()
    db = SessionLocal()
    count = 0
    skipped = 0

    try:
        with open(DATA_FILE, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                geom = row.get("the_geom", "")
                centroid = _polygon_centroid(geom)
                if not centroid:
                    skipped += 1
                    continue
                lat, lon = centroid

                if not (40.4 < lat < 41.0 and -74.3 < lon < -73.7):
                    skipped += 1
                    continue

                tds = row.get("TDS_NUM", "").strip()
                entity_id = f"nycha-{tds or count}"
                name = row.get("DEVELOPMEN", f"NYCHA Development {tds}").strip()[:200]
                borough = row.get("BOROUGH", "").strip()

                db.merge(Entity(
                    id=entity_id,
                    name=f"NYCHA: {name}",
                    entity_type="nycha_development",
                    role="receptor",
                    city="nyc",
                    lat=lat,
                    lon=lon,
                    address=f"{name}, {borough}",
                    extra_json=json.dumps({
                        "tds_num":          tds,
                        "borough":          borough,
                        "has_central_ac":   False,   # NYCHA buildings lack central AC
                        "ac_penetration":   0.42,     # ~42% have window units (NYCHA survey)
                        "thermal_mass":     "high",   # masonry construction
                    }),
                ))
                count += 1

        db.commit()
        print(f"NYCHA: loaded {count} receptor nodes (skipped {skipped})")
    finally:
        db.close()

if __name__ == "__main__":
    load_nycha()
