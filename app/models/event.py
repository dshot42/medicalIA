from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    CONSULTATION = "Consultation"
    SYMPTOM = "Symptôme rapporté"
    PRESCRIPTION = "Prescription"
    TEST_RESULT = "Résultat d'examen"
    TEST_RECOMMENDED = "Examen recommandé"
    HOSPITALIZATION = "Hospitalisation"
    FOLLOW_UP = "Suivi médical"
    OTHER = "Autre"


class EventStatus(StrEnum):
    EXTRACTED = "extraite"
    VERIFIED = "vérifiée"
    UNCERTAIN = "incertaine"


class DatePrecision(StrEnum):
    EXACT = "jour"
    MONTH = "mois"
    YEAR = "année"
    UNKNOWN = "inconnue"


class MedicalEvent(BaseModel):
    id: str
    event_date: date | None = None
    date_precision: DatePrecision = DatePrecision.UNKNOWN
    event_type: EventType
    description: str = Field(min_length=3)
    source_document_id: str
    source_chunk_id: str
    source_quote: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    status: EventStatus = EventStatus.EXTRACTED
    patient_id: str = Field(default="demo_patient", min_length=1, max_length=100)

