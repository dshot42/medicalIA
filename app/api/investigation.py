from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.container import get_container

router = APIRouter(tags=["clinical-investigation"])


class InvestigationRequest(BaseModel):
    patient_id: str = Field(min_length=1, max_length=100)
    consultation_symptoms: str = Field(min_length=10, max_length=10_000)
    max_iterations: int | None = Field(default=None, ge=1, le=50)


class PatientCreateRequest(BaseModel):
    patient_id: str = Field(
        min_length=2,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._ -]{1,99}$",
    )


@router.get("/patients")
def list_patients(q: str | None = Query(default=None, max_length=100)) -> list[str]:
    return get_container().repository.list_patients(q)


@router.post("/patients", status_code=201)
def create_patient(request: PatientCreateRequest) -> dict[str, object]:
    try:
        created = get_container().repository.create_patient(request.patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"patient_id": request.patient_id.strip(), "created": created}


@router.post("/investigations", status_code=201)
def create_investigation(request: InvestigationRequest) -> dict[str, object]:
    try:
        return get_container().investigation_service.run(
            patient_id=request.patient_id,
            consultation_symptoms=request.consultation_symptoms,
            max_iterations=request.max_iterations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/investigations")
def list_investigations(
    patient_id: str = Query(min_length=1, max_length=100),
) -> list[dict[str, object]]:
    return get_container().investigation_service.list(patient_id)


@router.get("/investigations/{investigation_id}")
def get_investigation(
    investigation_id: str,
    patient_id: str = Query(min_length=1, max_length=100),
) -> dict[str, object]:
    result = get_container().investigation_service.get(investigation_id, patient_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Investigation introuvable.")
    return result


@router.post("/investigations/{investigation_id}/rerun", status_code=201)
def rerun_investigation(
    investigation_id: str,
    patient_id: str = Query(min_length=1, max_length=100),
) -> dict[str, object]:
    try:
        return get_container().investigation_service.rerun(investigation_id, patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/investigations/{investigation_id}/hypotheses/{hypothesis_id}/evidence")
def get_hypothesis_evidence(
    investigation_id: str,
    hypothesis_id: str,
    patient_id: str = Query(min_length=1, max_length=100),
) -> dict[str, object]:
    result = get_container().investigation_service.get_hypothesis_evidence(
        investigation_id, hypothesis_id, patient_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Investigation ou hypothèse introuvable.")
    return result

