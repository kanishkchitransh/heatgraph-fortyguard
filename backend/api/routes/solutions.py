"""
POST /api/solution — Generate a Gemini solution card for one entity.
GET  /api/solutions/top?city=nyc&role=planner — Pre-generate top-3 compound risks.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from db.database import get_db, Entity
from core.gemini_service import generate_solution

router = APIRouter(prefix="/api", tags=["solutions"])


class SolutionRequest(BaseModel):
    entity_id:        str
    entity_name:      str
    entity_type:      str
    risk_score:       float
    temperature_f:    float
    explanation:      str
    department:       str
    compound_insight: Optional[str] = None
    user_role:        str = "planner"


@router.post("/solution")
def get_solution(req: SolutionRequest):
    """Generate (or return cached) solution card for one entity."""
    result = generate_solution(**req.model_dump())
    return result
