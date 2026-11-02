from __future__ import annotations

import hashlib
import re

from app.models.document import DocumentChunk


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def chunk_pages(
    document_id: str,
    filename: str,
    pages: list[str],
    chunk_size: int = 900,
    overlap: int = 120,
    patient_id: str = "demo_patient",
) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    position = 0
    for page_number, page_text in enumerate(pages, start=1):
        clean = re.sub(r"[ \t]+", " ", page_text).strip()
        if not clean:
            continue
        start = 0
        while start < len(clean):
            end = min(start + chunk_size, len(clean))
            if end < len(clean):
                boundary = max(clean.rfind("\n", start, end), clean.rfind(". ", start, end))
                if boundary > start + chunk_size // 2:
                    end = boundary + 1
            text = clean[start:end].strip()
            if text:
                chunk_id = _stable_id(
                    "chunk", f"{document_id}:{page_number}:{position}:{text}"
                )
                chunks.append(
                    DocumentChunk(
                        document_id=document_id,
                        filename=filename,
                        page=page_number,
                        chunk_id=chunk_id,
                        text=text,
                        position=position,
                        patient_id=patient_id,
                    )
                )
                position += 1
            if end >= len(clean):
                break
            start = max(end - overlap, start + 1)
    return chunks

