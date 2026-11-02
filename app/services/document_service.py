from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.ingestion.chunker import chunk_pages
from app.ingestion.loader import DocumentLoader
from app.models.document import DocumentDetail, DocumentRecord
from app.rag.qdrant_store import QdrantStore
from app.storage.repository import Repository

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(
        self,
        repository: Repository,
        vector_store: QdrantStore,
        loader: DocumentLoader,
        upload_dir: Path,
    ) -> None:
        self.repository = repository
        self.vector_store = vector_store
        self.loader = loader
        self.upload_dir = upload_dir
        upload_dir.mkdir(parents=True, exist_ok=True)

    def import_document(
        self, filename: str, content: bytes, patient_id: str = "demo_patient"
    ) -> tuple[DocumentRecord, bool]:
        loaded = self.loader.load(filename, content)
        existing = self.repository.get_document_by_hash(loaded.sha256, patient_id)
        if existing:
            if self.vector_store.count_document(existing.id) == 0:
                self.vector_store.index_chunks(self.repository.list_chunks(existing.id))
            logger.info("Document dupliqué ignoré", extra={"event": "document_duplicate"})
            return existing, True

        scoped_hash = hashlib.sha256(f"{patient_id}:{loaded.sha256}".encode()).hexdigest()
        document_id = f"doc_{scoped_hash[:16]}"
        chunks = chunk_pages(
            document_id, loaded.filename, loaded.pages, patient_id=patient_id
        )
        document = DocumentRecord(
            id=document_id,
            filename=loaded.filename,
            media_type=loaded.media_type,
            size_bytes=len(content),
            sha256=loaded.sha256,
            text=loaded.text,
            chunk_count=len(chunks),
            patient_id=patient_id,
        )
        self.repository.save_document(document, chunks)
        try:
            self.vector_store.index_chunks(chunks)
            suffix = Path(loaded.filename).suffix.lower()
            (self.upload_dir / f"{document_id}{suffix}").write_bytes(content)
        except Exception:
            self.repository.delete_document(document_id)
            self.vector_store.delete_document(document_id)
            raise
        logger.info("Document importé", extra={"event": "document_imported"})
        return document, False

    def import_archive(
        self, filename: str, content: bytes, patient_id: str
    ) -> tuple[list[tuple[DocumentRecord, bool]], list[str]]:
        loaded_documents, ignored = self.loader.load_archive(filename, content)
        imported = [
            self.import_document(item.filename, item.raw, patient_id)
            for item in loaded_documents
        ]
        return imported, ignored

    def list_documents(self, patient_id: str | None = None) -> list[DocumentRecord]:
        return self.repository.list_documents(patient_id)

    def get_document(
        self, document_id: str, patient_id: str | None = None
    ) -> DocumentDetail | None:
        return self.repository.get_document(document_id, patient_id)

    def delete_document(self, document_id: str) -> bool:
        document = self.repository.get_document(document_id)
        if document is None:
            return False
        self.vector_store.delete_document(document_id)
        deleted = self.repository.delete_document(document_id)
        for path in self.upload_dir.glob(f"{document_id}.*"):
            if path.is_file() and path.parent.resolve() == self.upload_dir.resolve():
                path.unlink(missing_ok=True)
        logger.info("Document supprimé", extra={"event": "document_deleted"})
        return deleted

