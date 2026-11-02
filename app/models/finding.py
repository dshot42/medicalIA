from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FindingCategory(StrEnum):
    CONTRADICTION = "Contradiction documentaire potentielle"
    TEMPORAL_PROXIMITY = "Rapprochement temporel"
    MISSING_FOLLOW_UP = "Suivi recommandé non retrouvé"
    MISSING_INFORMATION = "Information mentionnée mais absente"


class UncertaintyLevel(StrEnum):
    LOW = "faible"
    MODERATE = "modérée"
    HIGH = "élevée"


class SourceReference(BaseModel):
    document_id: str
    chunk_id: str
    filename: str
    page: int | None = None
    quote: str = Field(min_length=1)


class Finding(BaseModel):
    id: str
    category: FindingCategory
    description: str
    event_ids: list[str] = Field(min_length=1)
    sources: list[SourceReference] = Field(min_length=1)
    uncertainty: UncertaintyLevel
    confidence: float = Field(ge=0, le=1)
    question_for_professional: str | None = None
    sources_verified: bool = False
    verification_message: str | None = None
    patient_id: str = Field(default="demo_patient", min_length=1, max_length=100)

