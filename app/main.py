from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.analysis import router as analysis_router
from app.api.documents import router as documents_router
from app.api.investigation import router as investigation_router
from app.api.timeline import router as timeline_router
from app.container import get_container
from app.core.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    # Build every shared dependency before Uvicorn reports that startup is complete.
    # In particular, /health must not become reachable while SQLite, Qdrant and the
    # embedding model are still waiting for their first request to initialize.
    container = get_container()
    try:
        yield
    finally:
        container.close()
        get_container.cache_clear()


app = FastAPI(
    title="MedConnect AI",
    version=__version__,
    description=(
        "Reconstitution documentaire locale d'une chronologie médicale. "
        "Ce prototype ne fournit aucun diagnostic."
    ),
    lifespan=lifespan,
)
app.include_router(documents_router, prefix="/api")
app.include_router(timeline_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(investigation_router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}

