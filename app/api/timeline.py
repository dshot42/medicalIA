from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.container import get_container
from app.models.event import MedicalEvent
from app.models.finding import SourceReference

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("", response_model=list[MedicalEvent])
def get_timeline(patient_id: str | None = None) -> list[MedicalEvent]:
    return get_container().analysis_service.timeline(patient_id)


@router.get("/events/{event_id}/sources", response_model=list[SourceReference])
def get_event_sources(event_id: str, patient_id: str | None = None) -> list[SourceReference]:
    sources = get_container().analysis_service.event_sources(event_id, patient_id)
    if sources is None:
        raise HTTPException(status_code=404, detail="Événement introuvable.")
    return sources

