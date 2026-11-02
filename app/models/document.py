from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    document_id: str
    filename: str
    page: int | None = Field(default=None, ge=1)
    chunk_id: str
    text: str = Field(min_length=1)
    position: int = Field(ge=0)
    patient_id: str = Field(default="demo_patient", min_length=1, max_length=100)


class DocumentRecord(BaseModel):
    id: str
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    chunk_count: int = Field(default=0, ge=0)
    patient_id: str = Field(default="demo_patient", min_length=1, max_length=100)


class DocumentDetail(DocumentRecord):
    chunks: list[DocumentChunk] = Field(default_factory=list)

