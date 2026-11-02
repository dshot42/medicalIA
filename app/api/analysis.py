from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.container import get_container
from app.models.finding import Finding

router = APIRouter(tags=["analysis"])


class AnalysisRequest(BaseModel):
    analysis_id: str | None = Field(default=None, min_length=1, max_length=100)
    patient_id: str = Field(default="demo_patient", min_length=1, max_length=100)


@router.post("/analysis/run")
def run_analysis(request: AnalysisRequest | None = None) -> dict[str, object]:
    return get_container().analysis_service.run(
        request.analysis_id if request else None,
        request.patient_id if request else "demo_patient",
    )


@router.get("/analysis/findings", response_model=list[Finding])
def get_findings(patient_id: str | None = None) -> list[Finding]:
    return get_container().analysis_service.findings(patient_id)


@router.get("/search")
def search_documents(
    query: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=5, ge=1, le=20),
    document_id: str | None = None,
    patient_id: str | None = None,
) -> list[dict[str, object]]:
    return get_container().retriever.search(query, limit, document_id, patient_id)


@router.get("/dashboard")
def dashboard(patient_id: str | None = None) -> dict[str, object]:
    return get_container().repository.dashboard(patient_id)

