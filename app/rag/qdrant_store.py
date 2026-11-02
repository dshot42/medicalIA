from __future__ import annotations

import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models

from app.models.document import DocumentChunk
from app.rag.embeddings import Embedder


class QdrantStore:
    def __init__(
        self,
        path: Path | str,
        collection_name: str,
        embedder: Embedder,
        client: QdrantClient | None = None,
    ) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        self._owns_client = client is None
        self.client = client or QdrantClient(path=str(path))
        self.collection_name = collection_name
        self.embedder = embedder
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.embedder.dimension, distance=models.Distance.COSINE
                ),
            )

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"medconnect:{chunk_id}"))

    def index_chunks(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        vectors = self.embedder.encode([chunk.text for chunk in chunks])
        points = [
            models.PointStruct(
                id=self._point_id(chunk.chunk_id),
                vector=vector,
                payload=chunk.model_dump(),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points, wait=True)

    def index_records(self, records: list[dict[str, object]]) -> None:
        """Indexe des objets génériques contenant id, text et payload."""
        if not records:
            return
        vectors = self.embedder.encode([str(record["text"]) for record in records])
        points = [
            models.PointStruct(
                id=self._point_id(str(record["id"])),
                vector=vector,
                payload=dict(record["payload"]),
            )
            for record, vector in zip(records, vectors, strict=True)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points, wait=True)

    def search(
        self,
        query: str,
        limit: int = 5,
        document_id: str | None = None,
        patient_id: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        conditions: list[models.FieldCondition] = []
        if document_id:
            conditions.append(
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document_id)
                )
            )
        if patient_id:
            conditions.append(
                models.FieldCondition(
                    key="patient_id", match=models.MatchValue(value=patient_id)
                )
            )
        for key, value in (filters or {}).items():
            conditions.append(
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
            )
        query_filter = models.Filter(must=conditions) if conditions else None
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=self.embedder.encode([query])[0],
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [
            {"score": point.score, **(point.payload or {})}
            for point in response.points
        ]

    def delete_document(self, document_id: str) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id", match=models.MatchValue(value=document_id)
                    )
                ]
            ),
            wait=True,
        )

    def count_document(self, document_id: str) -> int:
        result = self.client.count(
            collection_name=self.collection_name,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id", match=models.MatchValue(value=document_id)
                    )
                ]
            ),
            exact=True,
        )
        return result.count

    def count(self, filters: dict[str, str] | None = None) -> int:
        conditions = [
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in (filters or {}).items()
        ]
        result = self.client.count(
            collection_name=self.collection_name,
            count_filter=models.Filter(must=conditions) if conditions else None,
            exact=True,
        )
        return result.count

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

