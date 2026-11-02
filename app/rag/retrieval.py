from __future__ import annotations

from app.rag.qdrant_store import QdrantStore


class MedicalRetriever:
    def __init__(self, store: QdrantStore) -> None:
        self.store = store

    def search(
        self,
        query: str,
        limit: int = 5,
        document_id: str | None = None,
        patient_id: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        return self.store.search(
            clean_query,
            min(max(limit, 1), 20),
            document_id,
            patient_id,
            filters,
        )

