"""
Master data loader — runs all NYC dataset loaders.

Idempotent: only runs if the entities table is empty.
Called from main.py startup.
"""
import os, sys

# Ensure backend/ is on the path when run directly
_here = os.path.dirname(__file__)
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def load_all(force: bool = False) -> int:
    """Load all available NYC datasets. Returns total entity count inserted."""
    from db.database import SessionLocal, Entity, init_db
    init_db()

    db = SessionLocal()
    existing = db.query(Entity).filter(Entity.city == "nyc").count()
    db.close()

    if existing > 0 and not force:
        print(f"NYC entities already loaded ({existing} rows). Skipping.")
        return existing

    print("=== Loading NYC datasets into factor graph ===")

    from data.loaders.load_dob_permits import load_dob_permits
    from data.loaders.load_schools     import load_schools
    from data.loaders.load_nycha       import load_nycha
    from data.loaders.load_subway      import load_subway
    from data.loaders.load_hvi         import load_hvi
    from data.loaders.load_cpdb        import load_cpdb
    from data.loaders.load_hospitals   import load_hospitals
    from data.loaders.load_shelters    import load_shelters
    from data.loaders.load_trees       import load_trees

    load_dob_permits()
    load_schools()
    load_nycha()
    load_subway()
    load_hvi()
    load_cpdb()
    load_hospitals()
    load_shelters()
    load_trees()

    db = SessionLocal()
    total = db.query(Entity).filter(Entity.city == "nyc").count()
    db.close()
    print(f"=== NYC data loaded: {total} total entities ===")
    return total


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="Reload even if data exists")
    args = p.parse_args()
    load_all(force=args.force)
