from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MEDCONNECT_", extra="ignore"
    )

    data_dir: Path = Path("data")
    upload_dir: Path = Path("uploads")
    database_path: Path = Path("data/medconnect.db")
    qdrant_path: Path = Path("data/qdrant")
    qdrant_collection: str = "patient_medical_records"
    knowledge_collection: str = "medical_knowledge_base"
    knowledge_base_path: Path = Path("data/knowledge/medical_knowledge.json")
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    max_file_size_mb: int = Field(default=10, ge=1, le=100)
    llm_provider: Literal["disabled", "gguf", "ollama", "openai_compatible"] = "gguf"
    llm_model: str = "llama3.1:8b"
    gguf_model_path: Path = Path(
        "C:/Users/come/Desktop/ia-llm/phi3/ia_llm/ressources/models/Qwen3.5-9B-Q6_K.gguf"
    )
    gguf_context_size: int = Field(default=65536, ge=512, le=131072)
    gguf_threads: int = Field(default=8, ge=1, le=128)
    gguf_gpu_layers: int = Field(default=-1, ge=-1, le=200)
    gguf_max_tokens: int = Field(default=512, ge=32, le=4096)
    gguf_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    evidence_verifier_model: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str = "http://localhost:1234/v1"
    openai_api_key: str = ""
    api_url: str = "http://127.0.0.1:8000"
    max_investigation_iterations: int = Field(default=12, ge=1, le=50)
    max_investigation_llm_calls: int = Field(default=8, ge=0, le=50)
    investigation_timeout_seconds: int = Field(default=600, ge=5, le=1800)
    investigation_model_version: str = "agentic-clinical-engine-v4-qwen-only"
    investigation_mode: Literal["qwen_grounded", "grounded", "llm_only"] = (
        "qwen_grounded"
    )

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.upload_dir,
            self.database_path.parent,
            self.qdrant_path,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings

