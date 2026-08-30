import json as _json
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from datetime import datetime, timezone
from config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # SQLite only
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class ApiCache(Base):
    __tablename__ = "api_cache"

    cache_key = Column(String, primary_key=True)
    response_json = Column(Text, nullable=False)
    credits_used = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Entity(Base):
    """
    A city entity in the Forney factor graph.
    role — emitter | receptor | both | sink
      emitter:  changes the thermal field (construction, demolition)
      receptor: affected by the thermal field (school, NYCHA, hospital)
      both:     subway stations (emit tunnel heat AND suffer high platform temps)
      sink:     lowers temperature (street trees, green infrastructure)
    """
    __tablename__ = "entities"

    id          = Column(String, primary_key=True)
    name        = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)   # "school" | "capital_project" | …
    role        = Column(String, default="receptor")  # "emitter" | "receptor" | "both" | "sink"
    city        = Column(String, default="nyc")
    lat         = Column(Float, nullable=False)
    lon         = Column(Float, nullable=False)
    address     = Column(String, default="")
    extra_json  = Column(Text, default="{}")       # type-specific attributes
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def attributes(self) -> dict:
        """Parsed extra_json — interface for all factor classes."""
        try:
            return _json.loads(self.extra_json or "{}")
        except (_json.JSONDecodeError, TypeError):
            return {}


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
