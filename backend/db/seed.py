"""
Seed city entities for Phoenix, AZ and Houston, TX demo.

Run once (idempotent): python -m db.seed
Sources: NCES school locator + public facility data (manually curated subset).
"""
import json
from db.database import SessionLocal, Entity, init_db

# ---------------------------------------------------------------------------
# Phoenix entities — within bbox [-112.1238, 33.3961, -112.0162, 33.5039]
# ---------------------------------------------------------------------------
PHOENIX_SCHOOLS = [
    ("school-phx-001", "Phoenix Union High School", 33.4484, -112.0740, "Phoenix Union HS District", {"enrollment": 2100, "grades": "9-12"}),
    ("school-phx-002", "North High School", 33.4880, -112.0730, "Phoenix Union HS District", {"enrollment": 1850, "grades": "9-12"}),
    ("school-phx-003", "Central High School", 33.4680, -112.0710, "Phoenix Union HS District", {"enrollment": 2200, "grades": "9-12"}),
    ("school-phx-004", "South Mountain High School", 33.3980, -112.0630, "Phoenix Union HS District", {"enrollment": 2000, "grades": "9-12"}),
    ("school-phx-005", "Encanto Elementary School", 33.4921, -112.0885, "Roosevelt ESD 66", {"enrollment": 420, "grades": "K-8"}),
    ("school-phx-006", "Garfield Elementary School", 33.4560, -112.0560, "Phoenix ESD 1", {"enrollment": 380, "grades": "K-8"}),
    ("school-phx-007", "Dunbar Elementary School", 33.4710, -112.0870, "Phoenix ESD 1", {"enrollment": 310, "grades": "K-8"}),
    ("school-phx-008", "Kenilworth Elementary School", 33.4630, -112.0990, "Phoenix ESD 1", {"enrollment": 360, "grades": "K-8"}),
    ("school-phx-009", "Madison Elementary School", 33.5020, -112.0600, "Madison ESD 38", {"enrollment": 490, "grades": "K-8"}),
    ("school-phx-010", "Roosevelt Elementary School", 33.4390, -112.0820, "Roosevelt ESD 66", {"enrollment": 340, "grades": "K-8"}),
]

PHOENIX_HOSPITALS = [
    ("hosp-phx-001", "Banner - University Medical Center Phoenix", 33.4768, -112.0710, "1111 E McDowell Rd", {"beds": 753, "trauma_level": 1}),
    ("hosp-phx-002", "St. Joseph's Hospital and Medical Center", 33.4793, -112.0800, "350 W Thomas Rd", {"beds": 571, "trauma_level": 1}),
    ("hosp-phx-003", "Dignity Health - St. Luke's Medical Center", 33.4640, -112.0370, "1800 E Van Buren St", {"beds": 220, "trauma_level": 3}),
    ("hosp-phx-004", "Phoenix Children's Hospital", 33.4761, -112.0713, "1919 E Thomas Rd", {"beds": 434, "pediatric": True}),
    ("hosp-phx-005", "Valleywise Health Medical Center", 33.4591, -112.0671, "2601 E Roosevelt St", {"beds": 285, "trauma_level": 1}),
]

PHOENIX_FIRE = [
    ("fire-phx-001", "Phoenix Fire Station 1", 33.4484, -112.0698, "150 S 5th St", {"unit_count": 3, "founded": 1887}),
    ("fire-phx-002", "Phoenix Fire Station 5", 33.4729, -112.0876, "1445 W McDowell Rd", {"unit_count": 2}),
    ("fire-phx-003", "Phoenix Fire Station 8", 33.4992, -112.0706, "4450 N 7th St", {"unit_count": 2}),
    ("fire-phx-004", "Phoenix Fire Station 25", 33.4131, -112.0630, "4402 S 7th St", {"unit_count": 2}),
]

# ---------------------------------------------------------------------------
# Houston entities — within bbox [-95.45, 29.70, -95.32, 29.80]
# ---------------------------------------------------------------------------
HOUSTON_SCHOOLS = [
    ("school-hou-001", "Reagan High School", 29.7620, -95.3540, "Houston ISD", {"enrollment": 2800, "grades": "9-12"}),
    ("school-hou-002", "Wheatley High School", 29.7710, -95.3260, "Houston ISD", {"enrollment": 1100, "grades": "9-12"}),
    ("school-hou-003", "Kashmere High School", 29.7830, -95.3420, "Houston ISD", {"enrollment": 750, "grades": "9-12"}),
    ("school-hou-004", "Hamilton Middle School", 29.7490, -95.3700, "Houston ISD", {"enrollment": 680, "grades": "6-8"}),
    ("school-hou-005", "Atherton Elementary School", 29.7560, -95.3610, "Houston ISD", {"enrollment": 450, "grades": "PK-5"}),
]

HOUSTON_HOSPITALS = [
    ("hosp-hou-001", "Harris Health Ben Taub Hospital", 29.7107, -95.3997, "1504 Taub Loop", {"beds": 586, "trauma_level": 1}),
    ("hosp-hou-002", "LBJ Hospital", 29.7745, -95.3678, "5656 Kelley St", {"beds": 230, "trauma_level": 3}),
]

HOUSTON_FIRE = [
    ("fire-hou-001", "Houston Fire Station 34", 29.7620, -95.3580, "3402 Lyons Ave", {"unit_count": 2}),
    ("fire-hou-002", "Houston Fire Station 46", 29.7820, -95.3250, "8410 Irvington Blvd", {"unit_count": 2}),
]


def _build_entities() -> list[Entity]:
    rows = []
    all_data = [
        # (id, name, lat, lon, address_or_district, extra, entity_type)
        *[(id_, name, lat, lon, addr, extra, "school") for id_, name, lat, lon, addr, extra in PHOENIX_SCHOOLS],
        *[(id_, name, lat, lon, addr, extra, "hospital") for id_, name, lat, lon, addr, extra in PHOENIX_HOSPITALS],
        *[(id_, name, lat, lon, addr, extra, "fire_station") for id_, name, lat, lon, addr, extra in PHOENIX_FIRE],
        *[(id_, name, lat, lon, addr, extra, "school") for id_, name, lat, lon, addr, extra in HOUSTON_SCHOOLS],
        *[(id_, name, lat, lon, addr, extra, "hospital") for id_, name, lat, lon, addr, extra in HOUSTON_HOSPITALS],
        *[(id_, name, lat, lon, addr, extra, "fire_station") for id_, name, lat, lon, addr, extra in HOUSTON_FIRE],
    ]
    for id_, name, lat, lon, address, extra, etype in all_data:
        rows.append(Entity(
            id=id_,
            name=name,
            entity_type=etype,
            lat=lat,
            lon=lon,
            address=address,
            extra_json=json.dumps(extra),
        ))
    return rows


def seed():
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(Entity).count()
        if existing > 0:
            print(f"Already seeded ({existing} entities). Skipping.")
            return
        entities = _build_entities()
        for e in entities:
            db.merge(e)
        db.commit()
        print(f"Seeded {len(entities)} entities.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
