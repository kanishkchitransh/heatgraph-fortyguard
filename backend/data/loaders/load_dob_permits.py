"""
DOB NOW Active Construction Permits → emitter nodes.

Filters to job_type IN (Full Demolition, New Building, Major Alteration)
which most disrupt local thermal environment (removes canopy, adds
impervious surface, generates dust+heat during construction).
"""
import csv, json, os
from db.database import SessionLocal, Entity, init_db

DATA_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "nyc", "dob_now_permits_recent.csv"
)

# job_type values that represent significant thermal emitters
EMITTER_TYPES = {
    "Full Demolition", "New Building", "Major Alteration",
    "Partial Demolition", "Rebuild",
}

def load_dob_permits():
    init_db()
    db = SessionLocal()
    count = 0
    skipped = 0
    seen_ids = set()

    try:
        with open(DATA_FILE, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lat_s = row.get("latitude", "").strip().strip('"')
                lon_s = row.get("longitude", "").strip().strip('"')
                if not lat_s or not lon_s:
                    skipped += 1
                    continue
                try:
                    lat, lon = float(lat_s), float(lon_s)
                except ValueError:
                    skipped += 1
                    continue

                # NYC bounding box sanity check
                if not (40.4 < lat < 41.0 and -74.3 < lon < -73.7):
                    skipped += 1
                    continue

                work_type = row.get("work_type", "").strip()
                if work_type not in EMITTER_TYPES:
                    skipped += 1
                    continue

                job_id = row.get("job_filing_number", "").strip().strip('"') or str(count)
                entity_id = f"dob-{job_id}"
                if entity_id in seen_ids:
                    continue
                seen_ids.add(entity_id)

                address = f"{row.get('house_no','').strip()} {row.get('street_name','').strip()}, {row.get('borough','').strip()}"

                db.merge(Entity(
                    id=entity_id,
                    name=f"DOB: {work_type} — {row.get('street_name','').strip()}"[:200],
                    entity_type="construction_permit",
                    role="emitter",
                    city="nyc",
                    lat=lat,
                    lon=lon,
                    address=address.strip(", "),
                    extra_json=json.dumps({
                        "work_type":    work_type,
                        "job_type":     row.get("filing_reason", ""),
                        "borough":      row.get("borough", ""),
                        "issued_date":  row.get("issued_date", ""),
                        "expired_date": row.get("expired_date", ""),
                        "description":  row.get("job_description", "")[:300],
                        "est_cost":     row.get("estimated_job_costs", ""),
                        "zip":          row.get("zip_code", ""),
                    }),
                ))
                count += 1
                if count % 2000 == 0:
                    db.commit()
                    print(f"  DOB permits: {count} loaded…")

        db.commit()
        print(f"DOB permits: loaded {count} emitter nodes (skipped {skipped})")
    finally:
        db.close()

if __name__ == "__main__":
    load_dob_permits()
