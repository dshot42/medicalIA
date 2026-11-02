from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

from app.container import get_container
from app.core.config import get_settings
from app.main import app


def test_fastapi_user_journey(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEDCONNECT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEDCONNECT_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("MEDCONNECT_DATABASE_PATH", str(tmp_path / "data" / "api.db"))
    monkeypatch.setenv("MEDCONNECT_QDRANT_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setenv("MEDCONNECT_QDRANT_COLLECTION", "api_test")
    monkeypatch.setenv("MEDCONNECT_EMBEDDING_MODEL", "hashing")
    monkeypatch.setenv("MEDCONNECT_LLM_PROVIDER", "disabled")
    monkeypatch.setenv("MEDCONNECT_INVESTIGATION_MODE", "grounded")
    get_container.cache_clear()
    get_settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        created_client = client.post(
            "/api/patients", json={"patient_id": "demo-client-27"}
        )
        assert created_client.status_code == 201
        assert created_client.json()["created"] is True
        assert client.get("/api/patients", params={"q": "client-27"}).json() == [
            "demo-client-27"
        ]

        archive_bytes = BytesIO()
        with ZipFile(archive_bytes, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("consultation.txt", "Consultation fictive avec suivi clinique.")
            archive.writestr("results.md", "Résultat médical fictif.")
        archive_result = client.post(
            "/api/documents",
            data={"patient_id": "demo-client-27"},
            files=[("files", ("bundle.zip", archive_bytes.getvalue(), "application/zip"))],
        )
        assert archive_result.status_code == 201
        assert len(archive_result.json()) == 2
        assert all(
            item["document"]["patient_id"] == "demo-client-27"
            for item in archive_result.json()
        )
        assert client.get("/api/documents", params={"patient_id": "demo-client-27"}).json()

        response = client.post(
            "/api/documents",
            files=[
                (
                    "files",
                    (
                        "consultation.txt",
                        b"Date du document : 12 mars 2024. Prescription d'un traitement fictif.",
                        "text/plain",
                    ),
                )
            ],
        )
        assert response.status_code == 201
        document_id = response.json()[0]["document"]["id"]
        duplicate = client.post(
            "/api/documents",
            files=[
                (
                    "files",
                    (
                        "copy.txt",
                        b"Date du document : 12 mars 2024. Prescription d'un traitement fictif.",
                        "text/plain",
                    ),
                )
            ],
        )
        assert duplicate.json()[0]["duplicate"] is True
        assert client.post("/api/analysis/run", json={}).status_code == 200
        assert client.get("/api/timeline").json()
        dashboard = client.get("/api/dashboard", params={"patient_id": "demo_patient"})
        assert dashboard.status_code == 200
        assert "recent_events" in dashboard.json()
        assert "follow_up_prompts" in dashboard.json()
        assert client.get("/api/search", params={"query": "traitement"}).json()
        investigation = client.post(
            "/api/investigations",
            json={
                "patient_id": "demo_patient",
                "consultation_symptoms": (
                    "Douleurs abdominales intermittentes avec fatigue et nausées."
                ),
                "max_iterations": 2,
            },
        )
        assert investigation.status_code == 201
        payload = investigation.json()
        assert payload["iteration_count"] == 2
        assert payload["consultation_symptoms"] == (
            "Douleurs abdominales intermittentes avec fatigue et nausées."
        )
        assert len(payload["hypotheses"]) >= 3
        stored_investigation = client.get(
            f"/api/investigations/{payload['investigation_id']}",
            params={"patient_id": "demo_patient"},
        )
        assert stored_investigation.status_code == 200
        assert stored_investigation.json()["consultation_symptoms"] == payload[
            "consultation_symptoms"
        ]
        assert client.get("/api/investigations").status_code == 422
        assert client.get(
            f"/api/investigations/{payload['investigation_id']}",
            params={"patient_id": "demo-client-27"},
        ).status_code == 404
        patient_archives = client.get(
            "/api/investigations", params={"patient_id": "demo_patient"}
        ).json()
        other_patient_archives = client.get(
            "/api/investigations", params={"patient_id": "demo-client-27"}
        ).json()
        assert [item["investigation_id"] for item in patient_archives] == [
            payload["investigation_id"]
        ]
        assert other_patient_archives == []
        assert client.delete(f"/api/documents/{document_id}").status_code == 204

