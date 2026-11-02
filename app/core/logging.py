from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Journal JSON sans contenu médical ni contenu de document."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "event",
            "analysis_id",
            "chunk_count",
            "event_count",
            "timeline_count",
            "finding_count",
            "error_count",
            "investigation_id",
            "mode",
            "max_iterations",
            "max_llm_calls",
            "node",
            "iteration",
            "hypothesis_id",
            "hypothesis_count",
            "clinical_evidence_count",
            "retrieved_passage_count",
            "new_evidence_count",
            "llm_call_count",
            "stop_reason",
            "operation",
            "error_type",
            "error_detail",
            "attempt",
            "hypotheses_compared",
            "context_size",
            "gpu_layers",
        ):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    log_directory = Path("data/logs")
    log_directory.mkdir(parents=True, exist_ok=True)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(JsonFormatter())
    file_handler = RotatingFileHandler(
        log_directory / "medconnect.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [console_handler, file_handler]
    root.setLevel(level)

