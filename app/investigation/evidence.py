from __future__ import annotations

import hashlib
import re

from app.agents.nodes.extract_events import parse_date
from app.investigation.parsing import normalize
from app.models.document import DocumentChunk
from app.models.investigation import (
    ClinicalConceptType,
    EvidencePolarity,
    EvidenceSourceOccurrence,
    EvidenceType,
    NumericObservation,
    PatientEvidence,
    RawClinicalEvidence,
    VerificationStatus,
)

UNPERFORMED_MARKERS = (
    "non realise",
    "pas ete realise",
    "pas ete mesure",
    "non mesure",
    "non disponible",
    "non recherche",
    "pas ete recherche",
    "n'ont pas ete recherches",
    "n'a pas ete mesure",
)


def classify_evidence(text: str) -> EvidenceType:
    value = normalize(text)
    if any(term in value for term in UNPERFORMED_MARKERS):
        return EvidenceType.UNPERFORMED_TEST
    if re.search(
        r"(?:episode|crise).{0,80}(?:dure|jour|heure|semaine|mois)",
        value,
    ):
        return EvidenceType.DURATION
    if any(
        term in value
        for term in (
            "sodium", "potassium", "tsh", "cortisol", "crp", "mmol",
            "serologie", "helicobacter", "porphobilinogene", "aminolevulinique",
            "acth", "aldosterone", "renine", "osmolarite", "creatinine", "uree",
            "transaminase", "bilirubine", "albumine", "ammoniac",
        )
    ):
        return EvidenceType.LABORATORY
    if any(term in value for term in ("echographie", "scanner", "irm", "radiographie")):
        return EvidenceType.IMAGING
    if any(
        term in value
        for term in (
            "pression arterielle", "examen clinique", "abdomen est", "deficit sensitif",
            "force des membres", "test ", "dosage",
        )
    ):
        return EvidenceType.EXAMINATION
    if any(term in value for term in ("traitement", "medicament", "prescription", "antibiotique")):
        return EvidenceType.TREATMENT
    if any(term in value for term in ("declenche", "jeune", "restriction", "stress", "apres")):
        return EvidenceType.TRIGGER
    if "famil" in value:
        return EvidenceType.FAMILY_HISTORY
    if any(term in value for term in ("antecedent", "historique")):
        return EvidenceType.HISTORY
    if any(term in value for term in ("evolution", "persiste", "continue", "recidive", "regresse")):
        return EvidenceType.TEMPORAL
    if any(
        term in value
        for term in (
            "diagnostic explicatif", "diagnostic retenu", "conclusion", "reevaluation medicale",
        )
    ):
        return EvidenceType.MEDICAL_CONCLUSION
    if any(
        term in value
        for term in (
            "douleur", "nausee", "vomissement", "fatigue", "faiblesse", "force",
            "constipation", "diarrhee", "vertige", "selles", "transit", "saignement",
            "perte de poids", "pression arterielle", "hyperpigmentation", "ictere",
        )
    ):
        return EvidenceType.SYMPTOM
    return EvidenceType.OTHER


def concept_type_for_evidence(evidence_type: EvidenceType) -> ClinicalConceptType:
    if evidence_type in {EvidenceType.SYMPTOM, EvidenceType.EXPLICIT_NEGATIVE}:
        return ClinicalConceptType.SYMPTOM
    return ClinicalConceptType.FINDING


def polarity(text: str) -> EvidencePolarity:
    value = normalize(text)
    if any(term in value for term in UNPERFORMED_MARKERS):
        return EvidencePolarity.NEUTRAL
    if any(
        term in value
        for term in (
            "absence de", "aucun ", "aucune ", "ne montre pas", "n'est pas",
            "ne sont pas", "negatif", "negative", "normale", "normal ", "sans anomalie",
        )
    ):
        return EvidencePolarity.NEGATIVE
    return EvidencePolarity.SUPPORTING


def extract_numeric_observations(text: str) -> list[NumericObservation]:
    pattern = re.compile(
        r"(?P<name>sodium|potassium|tsh|t4 libre|cortisol|crp|hemoglobine)?\s*"
        r"(?P<value>\d+(?:[.,]\d+)?)\s*"
        r"(?P<unit>mmol/l|g/l|mg/l|mu/l|mui/l|µui/ml|nmol/l|µg/dl)?"
        r"(?:\s*\((?:reference|norme)\s*[:：]?\s*(?P<reference>[^)]+)\))?",
        re.IGNORECASE,
    )
    observations: list[NumericObservation] = []
    for match in pattern.finditer(normalize(text)):
        if not match.group("name") and not match.group("unit"):
            continue
        observations.append(
            NumericObservation(
                name=match.group("name"),
                value=float(match.group("value").replace(",", ".")),
                unit=match.group("unit"),
                reference_interval=match.group("reference"),
            )
        )
    return observations


def _sentences(chunk: DocumentChunk) -> list[str]:
    return [
        item.strip(" \t-•")
        for item in re.split(r"(?<=[.!?])\s+|\n+", chunk.text)
        if len(item.strip()) >= 8
    ]


def _atomic_observations(sentence: str) -> list[str]:
    """Split list-like passages without losing the exact source sentence used for tracing."""

    clauses = [
        item.strip(" \t-•")
        for item in re.split(r"\s*;\s*|\s+[•]\s+", sentence)
        if len(item.strip(" \t-•")) >= 3
    ]
    atomic: list[str] = []
    for clause in clauses:
        # A long comma-separated clinical list is a set of observations, not one
        # favorable block. Avoid splitting ordinary prose and laboratory sentences.
        if clause.count(",") >= 4:
            parts = [item.strip() for item in clause.split(",") if len(item.strip()) >= 3]
            atomic.extend(parts if len(parts) >= 4 else [clause])
        else:
            atomic.append(clause)
    return atomic or [sentence]


def extract_raw_evidence_from_chunks(chunks: list[DocumentChunk]) -> list[RawClinicalEvidence]:
    """Preserve every source passage before any clinical interpretation."""
    raw: dict[str, RawClinicalEvidence] = {}
    for chunk in chunks:
        document_date, _ = parse_date(chunk.text[:250])
        for sentence in _sentences(chunk):
            identity = f"{chunk.patient_id}:{chunk.chunk_id}:{sentence}"
            raw_id = f"raw_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
            raw[raw_id] = RawClinicalEvidence(
                raw_evidence_id=raw_id,
                patient_id=chunk.patient_id,
                text=sentence,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                filename=chunk.filename,
                page=chunk.page,
                date=parse_date(sentence)[0] or document_date,
            )
    return list(raw.values())


def extract_evidence_from_chunks(chunks: list[DocumentChunk]) -> list[PatientEvidence]:
    """Structure every passage; unclassified information remains available as OTHER."""
    evidence: dict[str, PatientEvidence] = {}
    for chunk in chunks:
        document_date, _ = parse_date(chunk.text[:250])
        for sentence in _sentences(chunk):
            for observation in _atomic_observations(sentence):
                evidence_type = classify_evidence(observation)
                raw_identity = f"{chunk.patient_id}:{chunk.chunk_id}:{sentence}"
                raw_id = f"raw_{hashlib.sha256(raw_identity.encode()).hexdigest()[:16]}"
                identity = f"{chunk.patient_id}:{normalize(observation).strip()}"
                evidence_id = f"pev_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
                item_polarity = polarity(observation)
                if item_polarity == EvidencePolarity.NEGATIVE and evidence_type in {
                    EvidenceType.SYMPTOM, EvidenceType.OTHER,
                }:
                    evidence_type = EvidenceType.EXPLICIT_NEGATIVE
                occurrence = EvidenceSourceOccurrence(
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    filename=chunk.filename,
                    quote=sentence,
                    page=chunk.page,
                    date=parse_date(sentence)[0] or document_date,
                )
                if evidence_id not in evidence:
                    evidence[evidence_id] = PatientEvidence(
                        evidence_id=evidence_id,
                        raw_evidence_id=raw_id,
                        patient_id=chunk.patient_id,
                        type=evidence_type,
                        concept_type=concept_type_for_evidence(evidence_type),
                        description=observation,
                        quote=sentence,
                        document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                        filename=chunk.filename,
                        page=chunk.page,
                        date=parse_date(sentence)[0] or document_date,
                        polarity=item_polarity,
                        numeric_observations=extract_numeric_observations(observation),
                        source_occurrences=[occurrence],
                    )
                elif occurrence not in evidence[evidence_id].source_occurrences:
                    # A repeated observation remains one clinical fact while every source
                    # occurrence is retained for auditability.
                    evidence[evidence_id].source_occurrences.append(occurrence)
    return list(evidence.values())


def evidence_semantically_duplicate(
    candidate: PatientEvidence, existing: list[PatientEvidence]
) -> bool:
    candidate_terms = set(re.findall(r"[a-z0-9]+", normalize(candidate.description)))
    for item in existing:
        if item.evidence_id == candidate.evidence_id:
            return True
        terms = set(re.findall(r"[a-z0-9]+", normalize(item.description)))
        union = candidate_terms | terms
        if union and len(candidate_terms & terms) / len(union) >= 0.9:
            return True
    return False


def verify_patient_evidence(items: list[PatientEvidence], get_chunk) -> list[PatientEvidence]:
    verified: list[PatientEvidence] = []
    for item in items:
        occurrences = item.source_occurrences or [
            EvidenceSourceOccurrence(
                document_id=item.document_id,
                chunk_id=item.chunk_id,
                filename=item.filename,
                quote=item.quote,
                page=item.page,
                date=item.date,
            )
        ]
        verified_occurrences: list[EvidenceSourceOccurrence] = []
        for occurrence in occurrences:
            chunk = get_chunk(occurrence.chunk_id, item.patient_id)
            if (
                chunk is not None
                and chunk.patient_id == item.patient_id
                and chunk.document_id == occurrence.document_id
                and occurrence.quote in chunk.text
            ):
                verified_occurrences.append(occurrence)
        valid = bool(verified_occurrences)
        updated = item.model_copy(deep=True)
        updated.verification_status = (
            VerificationStatus.VERIFIED if valid else VerificationStatus.REJECTED
        )
        updated.source_occurrences = verified_occurrences
        if valid:
            verified.append(updated)
    return verified


def verify_raw_evidence(items: list[RawClinicalEvidence], get_chunk) -> list[RawClinicalEvidence]:
    verified: list[RawClinicalEvidence] = []
    for item in items:
        chunk = get_chunk(item.chunk_id, item.patient_id)
        valid = (
            chunk is not None and chunk.patient_id == item.patient_id
            and chunk.document_id == item.document_id and item.text in chunk.text
        )
        if valid:
            verified.append(
                item.model_copy(update={"verification_status": VerificationStatus.VERIFIED})
            )
    return verified
