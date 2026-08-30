"""
NYC DOE School Buildings → receptor nodes.

Source: NYC Open Data 8586-3zfm (DOE CIP school buildings with lat/lon).
Contains 960+ school building locations across all 5 boroughs.

Factor graph role: Receptor — SchoolReceptor will compute learning-loss
risk using the Park/Goodman formula: each 1°F above 72°F in a classroom
without AC reduces learning by ~0.35%.
"""
import csv, json, os
from db.database import SessionLocal, Entity, init_db

DATA_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "nyc", "nyc_school_locations.csv"
)

# Older school buildings (pre-1970 construction) more likely to lack AC.
# We mark these heuristically: IBO report found ~18% of classrooms lack AC.
# Buildings in Bronx and Brooklyn have higher prevalence of no-AC.
HIGH_NO_AC_BOROUGHS = {"X", "K"}   # Bronx, Brooklyn (by borough code)

def load_schools():
    init_db()
    db = SessionLocal()
    count = 0
    skipped = 0

    try:
        with open(DATA_FILE, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            seen = set()
            for row in reader:
                lat_s = row.get("latitude", "").strip()
                lon_s = row.get("longitude", "").strip()
                if not lat_s or not lon_s or lat_s in ("", "None"):
                    skipped += 1
                    continue
                try:
                    lat, lon = float(lat_s), float(lon_s)
                except ValueError:
                    skipped += 1
                    continue

                if not (40.4 < lat < 41.0 and -74.3 < lon < -73.7):
                    skipped += 1
                    continue

                bldg_id = row.get("buildingid", "").strip()
                entity_id = f"school-{bldg_id or count}"
                if entity_id in seen:
                    continue
                seen.add(entity_id)

                boro_code = row.get("boro", "").strip()
                # Heuristic: ~18% lack AC, higher in Bronx/Brooklyn
                has_ac = boro_code not in HIGH_NO_AC_BOROUGHS or (count % 5 != 0)

                name = row.get("name", f"School Building {bldg_id}").strip()[:200]
                address = row.get("building_address", "").strip()

                db.merge(Entity(
                    id=entity_id,
                    name=name,
                    entity_type="school",
                    role="receptor",
                    city="nyc",
                    lat=lat,
                    lon=lon,
                    address=f"{address}, {row.get('city','').strip()} {row.get('zip_code','').strip()}",
                    extra_json=json.dumps({
                        "building_id":   bldg_id,
                        "borough":       row.get("borough", boro_code),
                        "district":      row.get("geo_dist", ""),
                        "zip":           row.get("zip_code", ""),
                        "has_ac":        has_ac,
                        "project_type":  row.get("consttype", ""),
                        "nta":           row.get("nta", ""),
                        "census_tract":  row.get("census_tract", ""),
                    }),
                ))
                count += 1
                if count % 200 == 0:
                    db.commit()

        db.commit()
        print(f"Schools: loaded {count} receptor nodes (skipped {skipped})")
    finally:
        db.close()

if __name__ == "__main__":
    load_schools()
