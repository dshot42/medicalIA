from __future__ import annotations

import hashlib

from app.investigation.knowledge import contradiction_match, text_match
from app.models.investigation import (
    DiscriminativeCategory,
    DiscriminativeEvidence,
    EvidencePolarity,
    EvidenceType,
    MedicalKnowledgeProfile,
    PatientEvidence,
)


def _finding_id(category: DiscriminativeCategory, evidence_ids: list[str], label: str) -> str:
    identity = f"{category}:{label}:{'|'.join(sorted(evidence_ids))}"
    return f"dev_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"


def identify_discriminative_findings(
    facts: list[PatientEvidence], profiles: list[MedicalKnowledgeProfile]
) -> list[DiscriminativeEvidence]:
    """Classify evidence only through statements present in the curated reference profiles.

    No epidemiologic measure or numerical symptom weight is inferred here. Categories express
    how selectively a verified patient statement matches the independent knowledge base.
    """
    findings: dict[str, DiscriminativeEvidence] = {}
    positive = [item for item in facts if item.polarity == EvidencePolarity.SUPPORTING]

    for fact in facts:
        typical_matches = [
            profile
            for profile in profiles
            if any(text_match(pattern, fact.description) for pattern in profile.typical_manifestations)
        ]
        discriminating_matches = [
            profile
            for profile in profiles
            if any(text_match(pattern, fact.description) for pattern in profile.discriminating_features)
        ]
        contradiction_matches = [
            profile
            for profile in profiles
            if fact.polarity == EvidencePolarity.NEGATIVE
            and any(
                contradiction_match(marker, fact.description)
                for marker in profile.contradiction_markers
            )
        ]

        category: DiscriminativeCategory | None = None
        related = discriminating_matches or typical_matches
        rationale = ""
        if contradiction_matches:
            category = DiscriminativeCategory.EXPLICITLY_INCOMPATIBLE
            related = contradiction_matches
            rationale = "Résultat explicitement négatif ou normal relié à un marqueur contraire du référentiel."
        elif fact.type == EvidenceType.LABORATORY and fact.polarity != EvidencePolarity.NEUTRAL and related:
            category = DiscriminativeCategory.DISCRIMINATING_LAB
            rationale = "Résultat biologique vérifié relié à une manifestation ou un critère documenté."
        elif discriminating_matches:
            category = DiscriminativeCategory.RELATIVELY_CHARACTERISTIC
            rationale = "Indice relié à un caractère discriminant documenté dans le référentiel."
        elif len(typical_matches) == 1:
            category = DiscriminativeCategory.RELATIVELY_CHARACTERISTIC
            rationale = "Indice compatible avec une seule fiche parmi le référentiel actuellement disponible."
        elif len(typical_matches) > 1:
            category = DiscriminativeCategory.NON_SPECIFIC
            rationale = "Indice compatible avec plusieurs fiches du référentiel et donc peu différenciant isolément."

        if category is None:
            continue
        relevant = sorted({profile.disease_name for profile in related})
        knowledge_ids = sorted({profile.knowledge_id for profile in related})
        finding = DiscriminativeEvidence(
            finding_id=_finding_id(category, [fact.evidence_id], fact.description),
            category=category,
            description=fact.description,
            patient_evidence_ids=[fact.evidence_id],
            relevant_diagnoses=relevant,
            knowledge_ids=knowledge_ids,
            rationale=rationale,
        )
        findings[finding.finding_id] = finding

    # An association is emitted only when a reference profile documents the matched components
    # and at least two distinct verified facts support them. This does not create a new criterion.
    for profile in profiles:
        matched: dict[str, PatientEvidence] = {}
        for fact in positive:
            if any(
                text_match(pattern, fact.description)
                for pattern in [
                    *profile.typical_manifestations,
                    *profile.discriminating_features,
                ]
            ):
                matched[fact.evidence_id] = fact
        if len(matched) < 2:
            continue
        has_characteristic_component = any(
            any(text_match(feature, fact.description) for feature in profile.discriminating_features)
            or fact.type in {EvidenceType.LABORATORY, EvidenceType.TRIGGER}
            for fact in matched.values()
        )
        if not has_characteristic_component:
            continue
        ids = sorted(matched)
        finding = DiscriminativeEvidence(
            finding_id=_finding_id(
                DiscriminativeCategory.INFORMATIVE_ASSOCIATION, ids, profile.disease_name
            ),
            category=DiscriminativeCategory.INFORMATIVE_ASSOCIATION,
            description=(
                f"Association documentée de {len(ids)} indices compatibles avec la fiche "
                f"« {profile.disease_name} » ; l'association n'est pas quantifiée."
            ),
            patient_evidence_ids=ids,
            relevant_diagnoses=[profile.disease_name],
            knowledge_ids=[profile.knowledge_id],
            rationale=(
                "Plusieurs composantes de la même fiche médicale sont présentes, dont un "
                "résultat biologique, un facteur déclenchant ou un caractère discriminant."
            ),
        )
        findings[finding.finding_id] = finding

    return list(findings.values())
