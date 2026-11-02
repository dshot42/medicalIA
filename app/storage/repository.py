from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.models.document import DocumentChunk, DocumentDetail, DocumentRecord
from app.models.event import MedicalEvent
from app.models.finding import Finding


class Repository:
    """Stockage métier isolé par patient, distinct de l'index vectoriel."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        # Les noms v2 permettent une migration non destructive depuis la première version.
        with self.connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS patients (
                    patient_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS patient_documents (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(patient_id, sha256)
                );
                CREATE INDEX IF NOT EXISTS idx_patient_documents_patient
                    ON patient_documents(patient_id);
                CREATE TABLE IF NOT EXISTS patient_chunks (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    page INTEGER,
                    position INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES patient_documents(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_patient_chunks_patient
                    ON patient_chunks(patient_id);
                CREATE INDEX IF NOT EXISTS idx_patient_chunks_document
                    ON patient_chunks(document_id);
                CREATE TABLE IF NOT EXISTS patient_events (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES patient_documents(id) ON DELETE CASCADE,
                    FOREIGN KEY(chunk_id) REFERENCES patient_chunks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_patient_events_patient
                    ON patient_events(patient_id);
                CREATE TABLE IF NOT EXISTS patient_findings (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investigations (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    consultation_symptoms TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    final_json TEXT,
                    parent_investigation_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_investigations_patient
                    ON investigations(patient_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS metadata_v2 (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                """
            )

            connection.execute(
                """INSERT OR IGNORE INTO patients (patient_id, created_at)
                   SELECT patient_id, MIN(created_at) FROM patient_documents GROUP BY patient_id"""
            )

    def create_patient(self, patient_id: str) -> bool:
        patient_id = patient_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{1,99}", patient_id):
            raise ValueError(
                "L'identifiant client doit contenir 2 à 100 caractères alphanumériques, espaces, points, tirets ou underscores."
            )
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO patients (patient_id, created_at) VALUES (?, ?)",
                (patient_id, datetime.now(UTC).isoformat()),
            )
        return cursor.rowcount > 0

    def get_document_by_hash(
        self, sha256: str, patient_id: str = "demo_patient"
    ) -> DocumentRecord | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM patient_documents WHERE sha256 = ? AND patient_id = ?",
                (sha256, patient_id),
            ).fetchone()
        return self._row_to_document(row) if row else None

    def save_document(self, document: DocumentRecord, chunks: list[DocumentChunk]) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO patients (patient_id, created_at) VALUES (?, ?)",
                (document.patient_id, datetime.now(UTC).isoformat()),
            )
            connection.execute(
                """INSERT INTO patient_documents
                (id, patient_id, filename, media_type, size_bytes, sha256, text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document.id,
                    document.patient_id,
                    document.filename,
                    document.media_type,
                    document.size_bytes,
                    document.sha256,
                    document.text,
                    document.created_at.isoformat(),
                ),
            )
            connection.executemany(
                """INSERT INTO patient_chunks
                (id, patient_id, document_id, filename, page, position, text)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        chunk.chunk_id,
                        chunk.patient_id,
                        chunk.document_id,
                        chunk.filename,
                        chunk.page,
                        chunk.position,
                        chunk.text,
                    )
                    for chunk in chunks
                ],
            )

    def list_patients(self, query: str | None = None, limit: int = 100) -> list[str]:
        search = (query or "").strip()
        with self.connection() as connection:
            if search:
                rows = connection.execute(
                    """SELECT patient_id FROM patients WHERE patient_id LIKE ? COLLATE NOCASE
                       ORDER BY CASE WHEN patient_id = ? COLLATE NOCASE THEN 0
                                     WHEN patient_id LIKE ? COLLATE NOCASE THEN 1 ELSE 2 END,
                                patient_id COLLATE NOCASE LIMIT ?""",
                    (f"%{search}%", search, f"{search}%", max(1, min(limit, 100))),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT patient_id FROM patients ORDER BY patient_id COLLATE NOCASE LIMIT ?",
                    (max(1, min(limit, 100)),),
                ).fetchall()
        return [str(row["patient_id"]) for row in rows]

    def list_documents(self, patient_id: str | None = None) -> list[DocumentRecord]:
        where = "WHERE d.patient_id = ?" if patient_id else ""
        params: tuple[str, ...] = (patient_id,) if patient_id else ()
        with self.connection() as connection:
            rows = connection.execute(
                f"""SELECT d.*, COUNT(c.id) AS chunk_count FROM patient_documents d
                LEFT JOIN patient_chunks c ON c.document_id = d.id {where}
                GROUP BY d.id ORDER BY d.created_at DESC""",
                params,
            ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def get_document(
        self, document_id: str, patient_id: str | None = None
    ) -> DocumentDetail | None:
        query = "SELECT * FROM patient_documents WHERE id = ?"
        params: tuple[str, ...] = (document_id,)
        if patient_id:
            query += " AND patient_id = ?"
            params = (document_id, patient_id)
        with self.connection() as connection:
            row = connection.execute(query, params).fetchone()
            if not row:
                return None
            chunk_rows = connection.execute(
                "SELECT * FROM patient_chunks WHERE document_id = ? ORDER BY position",
                (document_id,),
            ).fetchall()
        chunks = [self._row_to_chunk(item) for item in chunk_rows]
        return DocumentDetail(**self._row_to_document(row).model_dump(), chunks=chunks)

    def list_chunks(
        self, document_id: str | None = None, patient_id: str | None = None
    ) -> list[DocumentChunk]:
        conditions: list[str] = []
        params: list[str] = []
        if document_id:
            conditions.append("document_id = ?")
            params.append(document_id)
        if patient_id:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        query = "SELECT * FROM patient_chunks"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY document_id, position"
        with self.connection() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def search_chunks_by_keywords(
        self, patient_id: str, keywords: list[str], limit: int = 20
    ) -> list[DocumentChunk]:
        clean = [keyword.strip().lower() for keyword in keywords if len(keyword.strip()) >= 3]
        if not clean:
            return []
        conditions = " OR ".join("LOWER(text) LIKE ?" for _ in clean)
        params: list[object] = [patient_id, *[f"%{word}%" for word in clean], limit]
        with self.connection() as connection:
            rows = connection.execute(
                f"""SELECT * FROM patient_chunks WHERE patient_id = ?
                AND ({conditions}) ORDER BY document_id, position LIMIT ?""",
                params,
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def get_chunk(
        self, chunk_id: str, patient_id: str | None = None
    ) -> DocumentChunk | None:
        query = "SELECT * FROM patient_chunks WHERE id = ?"
        params: tuple[str, ...] = (chunk_id,)
        if patient_id:
            query += " AND patient_id = ?"
            params = (chunk_id, patient_id)
        with self.connection() as connection:
            row = connection.execute(query, params).fetchone()
        return self._row_to_chunk(row) if row else None

    def delete_document(self, document_id: str) -> bool:
        document = self.get_document(document_id)
        if document is None:
            return False
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM patient_findings WHERE patient_id = ?", (document.patient_id,)
            )
            cursor = connection.execute(
                "DELETE FROM patient_documents WHERE id = ?", (document_id,)
            )
        return cursor.rowcount > 0

    def replace_analysis(self, events: list[MedicalEvent], findings: list[Finding]) -> None:
        patient_ids = {event.patient_id for event in events} | {
            finding.patient_id for finding in findings
        }
        if not patient_ids:
            patient_ids = {"demo_patient"}
        with self.connection() as connection:
            for patient_id in patient_ids:
                connection.execute(
                    "DELETE FROM patient_findings WHERE patient_id = ?", (patient_id,)
                )
                connection.execute(
                    "DELETE FROM patient_events WHERE patient_id = ?", (patient_id,)
                )
            connection.executemany(
                "INSERT INTO patient_events VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        event.id,
                        event.patient_id,
                        event.source_document_id,
                        event.source_chunk_id,
                        event.model_dump_json(),
                    )
                    for event in events
                ],
            )
            connection.executemany(
                "INSERT INTO patient_findings VALUES (?, ?, ?)",
                [
                    (finding.id, finding.patient_id, finding.model_dump_json())
                    for finding in findings
                ],
            )

    def list_events(self, patient_id: str | None = None) -> list[MedicalEvent]:
        query = "SELECT payload FROM patient_events"
        params: tuple[str, ...] = ()
        if patient_id:
            query += " WHERE patient_id = ?"
            params = (patient_id,)
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        events = [MedicalEvent.model_validate_json(row["payload"]) for row in rows]
        return sorted(
            events, key=lambda event: (event.event_date is None, event.event_date, event.id)
        )

    def get_event(
        self, event_id: str, patient_id: str | None = None
    ) -> MedicalEvent | None:
        query = "SELECT payload FROM patient_events WHERE id = ?"
        params: tuple[str, ...] = (event_id,)
        if patient_id:
            query += " AND patient_id = ?"
            params = (event_id, patient_id)
        with self.connection() as connection:
            row = connection.execute(query, params).fetchone()
        return MedicalEvent.model_validate_json(row["payload"]) if row else None

    def list_findings(self, patient_id: str | None = None) -> list[Finding]:
        query = "SELECT payload FROM patient_findings"
        params: tuple[str, ...] = ()
        if patient_id:
            query += " WHERE patient_id = ?"
            params = (patient_id,)
        query += " ORDER BY id"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Finding.model_validate_json(row["payload"]) for row in rows]

    def save_investigation(
        self,
        investigation_id: str,
        patient_id: str,
        symptoms: str,
        status: str,
        model_version: str,
        state: dict[str, object],
        final_assessment: dict[str, object] | None,
        parent_investigation_id: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO investigations
                (id, patient_id, created_at, consultation_symptoms, status, model_version,
                 state_json, final_json, parent_investigation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    investigation_id,
                    patient_id,
                    datetime.now(UTC).isoformat(),
                    symptoms,
                    status,
                    model_version,
                    json.dumps(state, ensure_ascii=False),
                    json.dumps(final_assessment, ensure_ascii=False)
                    if final_assessment
                    else None,
                    parent_investigation_id,
                ),
            )

    def get_investigation(
        self, investigation_id: str, patient_id: str | None = None
    ) -> dict[str, object] | None:
        query = "SELECT * FROM investigations WHERE id = ?"
        params = [investigation_id]
        if patient_id is not None:
            query += " AND patient_id = ?"
            params.append(patient_id)
        with self.connection() as connection:
            row = connection.execute(query, params).fetchone()
        return self._row_to_investigation(row) if row else None

    def list_investigations(self, patient_id: str | None = None) -> list[dict[str, object]]:
        query = "SELECT * FROM investigations"
        params: tuple[str, ...] = ()
        if patient_id:
            query += " WHERE patient_id = ?"
            params = (patient_id,)
        query += " ORDER BY created_at DESC"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_investigation(row) for row in rows]

    def dashboard(self, patient_id: str | None = None) -> dict[str, object]:
        events = self.list_events(patient_id)
        dates = [event.event_date for event in events if event.event_date]
        documents = self.list_documents(patient_id)
        findings = self.list_findings(patient_id)
        investigations = self.list_investigations(patient_id)
        latest = investigations[0] if investigations else None
        follow_up_prompts: list[str] = []
        for finding in findings[:3]:
            if finding.question_for_professional:
                follow_up_prompts.append(finding.question_for_professional)
        if not documents:
            follow_up_prompts.append("Importer les comptes rendus et résultats médicaux du patient.")
        if not investigations:
            follow_up_prompts.append(
                "Résumer les symptômes actuels, leur durée, leur évolution et les signes absents explicitement."
            )
        if documents and not events:
            follow_up_prompts.append(
                "Extraire les dates, examens, traitements et prochaines étapes des documents du patient."
            )
        return {
            "document_count": len(documents),
            "event_count": len(events),
            "finding_count": len(findings),
            "period_start": min(dates).isoformat() if dates else None,
            "period_end": max(dates).isoformat() if dates else None,
            "recent_events": [item.model_dump(mode="json") for item in events[-5:][::-1]],
            "recent_documents": [
                {
                    "id": item.id,
                    "filename": item.filename,
                    "created_at": item.created_at.isoformat(),
                    "chunk_count": item.chunk_count,
                }
                for item in documents[:5]
            ],
            "open_findings": [item.model_dump(mode="json") for item in findings[:5]],
            "latest_investigation": (
                {
                    "investigation_id": latest["investigation_id"],
                    "created_at": latest["created_at"],
                    "status": latest["status"],
                    "conclusion": (latest.get("final_assessment") or {}).get("conclusion"),
                }
                if latest
                else None
            ),
            "investigation_count": len(investigations),
            "follow_up_prompts": list(dict.fromkeys(follow_up_prompts)),
        }

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> DocumentRecord:
        data = dict(row)
        data["chunk_count"] = data.get("chunk_count", 0)
        return DocumentRecord(**data)

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> DocumentChunk:
        return DocumentChunk(
            document_id=row["document_id"],
            filename=row["filename"],
            page=row["page"],
            chunk_id=row["id"],
            text=row["text"],
            position=row["position"],
            patient_id=row["patient_id"],
        )

    @staticmethod
    def _row_to_investigation(row: sqlite3.Row) -> dict[str, object]:
        return {
            "investigation_id": row["id"],
            "patient_id": row["patient_id"],
            "created_at": row["created_at"],
            "consultation_symptoms": row["consultation_symptoms"],
            "status": row["status"],
            "model_version": row["model_version"],
            "state": json.loads(row["state_json"]),
            "final_assessment": json.loads(row["final_json"]) if row["final_json"] else None,
            "parent_investigation_id": row["parent_investigation_id"],
        }

