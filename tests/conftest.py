from __future__ import annotations

from pathlib import Path

import pytest

from app.container import Container
from app.core.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "uploads",
        database_path=tmp_path / "data" / "test.db",
        qdrant_path=tmp_path / "qdrant",
        qdrant_collection="test_chunks",
        embedding_model="hashing",
        llm_provider="disabled",
        investigation_mode="grounded",
    )


@pytest.fixture
def container(settings: Settings):
    value = Container(settings)
    yield value
    value.close()


@pytest.fixture
def sample_dir() -> Path:
    return Path(__file__).parents[1] / "data" / "samples"


def import_samples(container: Container, sample_dir: Path) -> None:
    for path in sorted(sample_dir.glob("*")):
        if path.suffix in {".txt", ".md"}:
            container.document_service.import_document(path.name, path.read_bytes())

