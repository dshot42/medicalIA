from __future__ import annotations

from functools import lru_cache

from app.agents.graph import AnalysisGraph
from app.agents.llm import StructuredLLM
from app.agents.local_gguf import LocalGGUFClient
from app.core.config import Settings, get_settings
from app.ingestion.loader import DocumentLoader
from app.investigation.graph import ClinicalInvestigationGraph
from app.investigation.knowledge import MedicalKnowledgeService
from app.investigation.retrieval import PatientHistoryRetriever
from app.rag.embeddings import create_embedder
from app.rag.qdrant_store import QdrantStore
from app.rag.retrieval import MedicalRetriever
from app.services.analysis_service import AnalysisService
from app.services.document_service import DocumentService
from app.services.investigation_service import ClinicalInvestigationService
from app.storage.repository import Repository


class Container:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_directories()
        self.settings = settings
        self.repository = Repository(settings.database_path)
        self.embedder = create_embedder(settings.embedding_model)
        self.vector_store = QdrantStore(
            settings.qdrant_path, settings.qdrant_collection, self.embedder
        )
        self.knowledge_vector_store = None
        self.retriever = MedicalRetriever(self.vector_store)
        self.knowledge_retriever = None
        self.document_service = DocumentService(
            self.repository,
            self.vector_store,
            DocumentLoader(settings.max_file_size_mb * 1024 * 1024),
            settings.upload_dir,
        )
        self.local_gguf = LocalGGUFClient(settings)
        self.analysis_graph = AnalysisGraph(
            self.repository,
            settings.data_dir / "analysis_checkpoints.db",
            StructuredLLM(settings, self.local_gguf),
        )
        self.analysis_service = AnalysisService(self.repository, self.analysis_graph)
        self.knowledge_service = None
        if settings.investigation_mode in {"grounded", "qwen_grounded"}:
            self.knowledge_vector_store = QdrantStore(
                settings.qdrant_path,
                settings.knowledge_collection,
                self.embedder,
                client=self.vector_store.client,
            )
            self.knowledge_retriever = MedicalRetriever(self.knowledge_vector_store)
            self.knowledge_service = MedicalKnowledgeService(
                settings.knowledge_base_path, self.knowledge_vector_store
            )
        self.patient_history_retriever = PatientHistoryRetriever(
            self.repository, self.retriever
        )
        self.clinical_graph = ClinicalInvestigationGraph(
            repository=self.repository,
            patient_retriever=self.patient_history_retriever,
            knowledge=self.knowledge_service,
            checkpoint_path=settings.data_dir / "clinical_checkpoints.db",
            model_version=settings.investigation_model_version,
            investigation_mode=settings.investigation_mode,
            local_llm=self.local_gguf,
        )
        self.investigation_service = ClinicalInvestigationService(
            self.repository, self.clinical_graph, settings
        )

    def close(self) -> None:
        self.analysis_graph.close()
        self.clinical_graph.close()
        self.local_gguf.close()
        if self.knowledge_vector_store is not None:
            self.knowledge_vector_store.close()
        self.vector_store.close()


@lru_cache
def get_container() -> Container:
    return Container(get_settings())

