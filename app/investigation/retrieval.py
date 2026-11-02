from __future__ import annotations

from app.investigation.parsing import expanded_search_terms
from app.models.document import DocumentChunk
from app.models.investigation import MedicalKnowledgeProfile, ParsedConsultation
from app.rag.retrieval import MedicalRetriever
from app.storage.repository import Repository


class PatientHistoryRetriever:
    def __init__(self, repository: Repository, semantic_retriever: MedicalRetriever) -> None:
        self.repository = repository
        self.semantic_retriever = semantic_retriever

    def initial_search(
        self, patient_id: str, parsed: ParsedConsultation
    ) -> tuple[list[DocumentChunk], list[str]]:
        symptom_terms = expanded_search_terms(parsed)
        queries = [
            *symptom_terms,
            "résultats biologiques laboratoire sodium potassium cortisol TSH",
            "imagerie échographie scanner IRM abdomen",
            "évolution dates épisodes facteurs déclenchants traitements",
        ]
        return self._combined_search(patient_id, queries, symptom_terms)

    def inventory(self, patient_id: str) -> tuple[list[dict[str, object]], list[DocumentChunk]]:
        """Return the authoritative patient corpus, independently from vector ranking."""
        documents = self.repository.list_documents(patient_id)
        inventory = [
            {
                "document_id": item.id,
                "filename": item.filename,
                "chunk_count": item.chunk_count,
                "size_bytes": item.size_bytes,
                "created_at": item.created_at.isoformat(),
            }
            for item in documents
        ]
        return inventory, self.repository.list_chunks(patient_id=patient_id)

    def targeted_search(
        self, patient_id: str, profile: MedicalKnowledgeProfile
    ) -> tuple[list[DocumentChunk], list[str]]:
        queries = [
            " ".join(profile.typical_manifestations[:5]),
            " ".join(profile.discriminating_features),
            " ".join([*profile.useful_tests, *profile.contradiction_markers]),
        ]
        keywords = [
            item
            for phrase in [
                *profile.typical_manifestations,
                *profile.discriminating_features,
                *profile.useful_tests,
                *profile.contradiction_markers,
            ]
            for item in phrase.split()
            if len(item) >= 5
        ]
        return self._combined_search(patient_id, queries, keywords)

    def semantic_patient_search(
        self, patient_id: str, queries: list[str]
    ) -> tuple[list[DocumentChunk], list[str]]:
        keywords = [
            token
            for query in queries
            for token in query.split()
            if len(token.strip()) >= 5
        ]
        return self._combined_search(patient_id, queries, keywords)

    def _combined_search(
        self, patient_id: str, queries: list[str], keywords: list[str]
    ) -> tuple[list[DocumentChunk], list[str]]:
        chunks: dict[str, DocumentChunk] = {}
        executed: list[str] = []
        for query in dict.fromkeys(item.strip() for item in queries if item.strip()):
            executed.append(query)
            results = self.semantic_retriever.search(
                query, limit=8, patient_id=patient_id
            )
            for result in results:
                if result.get("patient_id") != patient_id:
                    continue
                chunk = DocumentChunk.model_validate(
                    {key: value for key, value in result.items() if key != "score"}
                )
                chunks[chunk.chunk_id] = chunk
        for chunk in self.repository.search_chunks_by_keywords(patient_id, keywords, limit=30):
            chunks[chunk.chunk_id] = chunk
        return list(chunks.values()), executed

