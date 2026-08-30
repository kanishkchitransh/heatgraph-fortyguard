"""
MTA Subway Stations → both (emitter + receptor) nodes.

Source: data.ny.gov 5f5g-n3cz (445 station complexes).

Emitter side: underground stations discharge hot tunnel air through sidewalk
  grates, raising street-level temps 2-5°C within 10m of the grate.
Receptor side: platform temps reach 96°F (36°C); passengers and workers
  are exposed to dangerous heat. Elevated tracks buckle above 90°F ambient.

Factor graph role: Both
"""
import csv, json, os
from db.database import SessionLocal, Entity, init_db

DATA_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "nyc", "mta_subway_stations.csv"
)

def load_subway():
    init_db()
    db = SessionLocal()
    count = 0
    skipped = 0

    try:
        with open(DATA_FILE, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lat_s = row.get("Latitude", "").strip()
                lon_s = row.get("Longitude", "").strip()
                if not lat_s or not lon_s:
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

                cplx_id = row.get("Complex ID", "").strip()
                entity_id = f"subway-{cplx_id or count}"
                name = row.get("Stop Name", row.get("Display Name", f"Station {cplx_id}")).strip()
                structure = row.get("Structure Type", "Subway").strip()
                routes = row.get("Daytime Routes", "").strip()
                borough = row.get("Borough", "").strip()

                # Elevated tracks have different risk profile (buckle in heat)
                is_elevated = structure in ("Elevated", "Open Cut", "Viaduct")

                db.merge(Entity(
                    id=entity_id,
                    name=f"🚇 {name}",
                    entity_type="subway_station",
                    role="both",
                    city="nyc",
                    lat=lat,
                    lon=lon,
                    address=f"{name}, {borough}",
                    extra_json=json.dumps({
                        "complex_id":    cplx_id,
                        "routes":        routes,
                        "borough":       borough,
                        "structure":     structure,
                        "is_elevated":   is_elevated,
                        "is_underground": structure == "Subway",
                        "ada":           row.get("ADA", "0") == "1",
                        # Emitter: underground grates raise street temp 2-5°C
                        "grate_temp_delta_c": 0 if is_elevated else 3.0,
                        # Receptor: platform temp above ambient
                        "platform_temp_delta_c": 8.0 if not is_elevated else 2.0,
                    }),
                ))
                count += 1

        db.commit()
        print(f"Subway: loaded {count} both-role nodes (skipped {skipped})")
    finally:
        db.close()

if __name__ == "__main__":
    load_subway()
