"""
api/health.py — Health check endpoint
"""

from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime, timezone

from core.version import APP_VERSION

router = APIRouter()

class HealthResponse(BaseModel):
    service: str = "voicevault-api"
    status: str
    version: str
    timestamp: str

@router.get("/", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        timestamp=datetime.now(timezone.utc).isoformat()
    )
