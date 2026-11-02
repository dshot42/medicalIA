from __future__ import annotations

import hashlib

from app.investigation.knowledge import (
    contradiction_match,
    discriminating_text_match,
    text_match,
)
from app.models.investigation import (
    AssertionOrigin,
    EvidenceSpecificity,
    EvidenceTier,
    EvidencePolarity,
    EvidenceRelation,
    EvidenceRelationAssessment,
    EvidenceType,
    MedicalKnowledgeProfile,
    PatientEvidence,
    RelationValidationStatus,
    ValidatedAssertion,
    VerificationStatus,
)


def _collapse_repeated_relations(
    relations: list[EvidenceRelationAssessment],
) -> list[EvidenceRelationAssessment]:
    """Count one clinical criterion once while retaining every source evidence id."""

    grouped: dict[tuple[object, ...], EvidenceRelationAssessment] = {}
    passthrough: list[EvidenceRelationAssessment] = []
    for relation in relations:
        if relation.relation not in {
            EvidenceRelation.SUPPORTS,
            EvidenceRelation.CONTRADICTS,
            EvidenceRelation.NEUTRAL,
        } or not relation.criterion:
            passthrough.append(relation)
            continue
        key = (
            relation.hypothesis_id,
            relation.relation,
            relation.criterion,
            relation.evidence_tier,
        )
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = relation.model_copy(deep=True)
            continue
        existing.related_evidence_ids = list(dict.fromkeys([
            *existing.related_evidence_ids,
            relation.evidence_id,
            *relation.related_evidence_ids,
        ]))
    return [*grouped.values(), *passthrough]


def _matching_patterns(patterns: list[str], text: str) -> list[str]:
    return [pattern for pattern in patterns if text_match(pattern, text)]


def _matching_specific_patterns(patterns: list[str], text: str) -> list[str]:
    return [pattern for pattern in patterns if discriminating_text_match(pattern, text)]


def _relation_id(
    hypothesis_id: str,
    evidence_id: str,
    relation: EvidenceRelation,
    criterion: str | None,
) -> str:
    identity = f"{hypothesis_id}:{evidence_id}:{relation.value}:{criterion or ''}"
    return f"rel_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"


def validate_evidence_relations(
    facts: list[PatientEvidence],
    profile: MedicalKnowledgeProfile | None,
    *,
    hypothesis_id: str = "",
    proposed_supporting_ids: list[str] | None = None,
    proposed_contradicting_ids: list[str] | None = None,
    proposed_discriminative_ids: list[str] | None = None,
    include_undocumented_tests: bool = False,
) -> list[EvidenceRelationAssessment]:
    """Build source-backed relations independently from free-form LLM prose.

    A relation is validated only when both the patient observation and a criterion from
    the independently loaded profile are present. Unknown hypotheses deliberately remain
    unverifiable. An unperformed test is always missing, never a negative result.
    """

    proposed = set(proposed_supporting_ids or [])
    proposed.update(proposed_contradicting_ids or [])
    proposed.update(proposed_discriminative_ids or [])
    relations: dict[str, EvidenceRelationAssessment] = {}

    for fact in facts:
        if fact.verification_status != VerificationStatus.VERIFIED:
            continue

        relation = EvidenceRelation.NEUTRAL
        relevant = False
        criterion: str | None = None
        knowledge_ids: list[str] = []
        validation_status = RelationValidationStatus.UNVERIFIABLE
        evidence_tier = EvidenceTier.UNVERIFIABLE
        specificity = EvidenceSpecificity.UNKNOWN
        classification_rationale = (
            "Aucun critère indépendant ne permet de classer cette observation."
        )
        rationale = "Observation patient vérifiée, sans relation médicale indépendante établie."

        if profile is not None:
            knowledge_ids = [profile.knowledge_id]
            manifestations = [
                *profile.typical_manifestations,
                *profile.atypical_manifestations,
            ]
            confirmatory_matches = _matching_specific_patterns(
                profile.confirmatory_features, fact.description
            )
            discriminating_matches = _matching_specific_patterns(
                profile.discriminating_features, fact.description
            )
            useful_test_matches = _matching_specific_patterns(
                profile.useful_tests, fact.description
            )
            manifestation_matches = _matching_patterns(manifestations, fact.description)
            criterion_matches = [
                *confirmatory_matches,
                *discriminating_matches,
            ]
            contradiction_matches = [
                marker
                for marker in profile.contradiction_markers
                if contradiction_match(marker, fact.description)
            ]
            missing_test = (
                fact.type == EvidenceType.UNPERFORMED_TEST
                and bool([*criterion_matches, *useful_test_matches])
            )

            if missing_test:
                relation = EvidenceRelation.MISSING
                relevant = True
                criterion = (*criterion_matches, *useful_test_matches)[0]
                validation_status = RelationValidationStatus.VALIDATED
                evidence_tier = EvidenceTier.MISSING
                specificity = EvidenceSpecificity.UNKNOWN
                classification_rationale = (
                    "Examen utile ou discriminant absent du dossier; aucune négativité déduite."
                )
                rationale = (
                    "Examen discriminant du référentiel explicitement non réalisé; "
                    "il reste une information manquante et non un résultat négatif."
                )
            elif fact.polarity == EvidencePolarity.NEGATIVE and contradiction_matches:
                relation = EvidenceRelation.CONTRADICTS
                relevant = True
                criterion = contradiction_matches[0]
                validation_status = RelationValidationStatus.VALIDATED
                evidence_tier = EvidenceTier.CONTRADICTORY
                specificity = EvidenceSpecificity.HIGH
                classification_rationale = (
                    "Résultat négatif explicite correspondant à un marqueur contradictoire sourcé."
                )
                rationale = (
                    "Résultat explicitement négatif ou normal relié à un marqueur "
                    "défavorable du référentiel."
                )
            elif (
                fact.polarity == EvidencePolarity.SUPPORTING
                and (
                    bool(confirmatory_matches)
                    or fact.type not in {EvidenceType.MEDICAL_CONCLUSION, EvidenceType.OTHER}
                )
                and (manifestation_matches or criterion_matches)
            ):
                relation = EvidenceRelation.SUPPORTS
                relevant = True
                criterion = (
                    confirmatory_matches
                    or discriminating_matches
                    or criterion_matches
                    or manifestation_matches
                )[0]
                validation_status = RelationValidationStatus.VALIDATED
                if confirmatory_matches:
                    evidence_tier = EvidenceTier.CONFIRMATORY
                    specificity = EvidenceSpecificity.HIGH
                    classification_rationale = (
                        "Observation vérifiée correspondant à un critère confirmatoire du référentiel."
                    )
                elif discriminating_matches:
                    evidence_tier = EvidenceTier.HIGHLY_DISCRIMINATING
                    specificity = EvidenceSpecificity.HIGH
                    classification_rationale = (
                        "Observation vérifiée correspondant à un critère fortement discriminant."
                    )
                else:
                    evidence_tier = EvidenceTier.COMPATIBLE_NON_SPECIFIC
                    specificity = EvidenceSpecificity.LOW
                    classification_rationale = (
                        "Manifestation compatible mais partagée avec plusieurs diagnostics."
                    )
                rationale = (
                    "Observation positive reliée à une manifestation ou un critère "
                    "du référentiel."
                )
            elif manifestation_matches or criterion_matches:
                relation = EvidenceRelation.NEUTRAL
                relevant = True
                criterion = (criterion_matches or manifestation_matches)[0]
                validation_status = RelationValidationStatus.VALIDATED
                evidence_tier = EvidenceTier.NON_DISCRIMINATING
                specificity = EvidenceSpecificity.LOW
                classification_rationale = (
                    "Observation reliée au profil mais sans polarité ou spécificité suffisante."
                )
                rationale = (
                    "Observation pertinente mais non discriminante, ou polarité insuffisante "
                    "pour la classer comme favorable ou défavorable."
                )

        if fact.evidence_id in proposed and not relevant:
            relation = EvidenceRelation.UNVERIFIABLE
            validation_status = RelationValidationStatus.UNVERIFIABLE
            evidence_tier = EvidenceTier.UNVERIFIABLE
            specificity = EvidenceSpecificity.UNKNOWN
            classification_rationale = (
                "Classification proposée sans critère indépendant correspondant."
            )
            rationale = (
                "Relation proposée par le LLM mais non confirmée par un critère du "
                "référentiel indépendant."
            )

        if relevant or fact.evidence_id in proposed:
            relation_id = _relation_id(
                hypothesis_id, fact.evidence_id, relation, criterion
            )
            relations[relation_id] = EvidenceRelationAssessment(
                relation_id=relation_id,
                hypothesis_id=hypothesis_id,
                evidence_id=fact.evidence_id,
                relation=relation,
                relevant=relevant,
                # An absent date is unknown, not evidence of temporal inconsistency.
                temporal_consistent=True,
                knowledge_ids=knowledge_ids if relevant else [],
                patient_observation=fact.description,
                document_id=fact.document_id,
                chunk_id=fact.chunk_id,
                criterion=criterion,
                polarity=fact.polarity,
                validation_status=validation_status,
                evidence_tier=evidence_tier,
                specificity=specificity,
                classification_rationale=classification_rationale,
                rationale=rationale,
            )

    if profile is not None and include_undocumented_tests:
        verified_facts = [
            fact
            for fact in facts
            if fact.verification_status == VerificationStatus.VERIFIED
        ]
        for useful_test in profile.useful_tests:
            if any(
                discriminating_text_match(useful_test, fact.description)
                for fact in verified_facts
            ):
                continue
            missing_id = (
                "missing_"
                + hashlib.sha256(
                    f"{hypothesis_id}:{profile.knowledge_id}:{useful_test}".encode()
                ).hexdigest()[:16]
            )
            relation_id = _relation_id(
                hypothesis_id, missing_id, EvidenceRelation.MISSING, useful_test
            )
            relations[relation_id] = EvidenceRelationAssessment(
                relation_id=relation_id,
                hypothesis_id=hypothesis_id,
                evidence_id=missing_id,
                relation=EvidenceRelation.MISSING,
                relevant=True,
                temporal_consistent=True,
                knowledge_ids=[profile.knowledge_id],
                patient_observation=(
                    f"Résultat non documenté dans le dossier examiné : {useful_test}."
                ),
                criterion=useful_test,
                polarity=EvidencePolarity.NEUTRAL,
                validation_status=RelationValidationStatus.VALIDATED,
                evidence_tier=EvidenceTier.MISSING,
                specificity=EvidenceSpecificity.UNKNOWN,
                classification_rationale=(
                    "Examen du référentiel non retrouvé dans le corpus couvert."
                ),
                rationale=(
                    "Information discriminante non retrouvée dans le corpus couvert; "
                    "elle est classée manquante, jamais négative."
                ),
            )

    # Preserve explicit conflicts instead of silently choosing one source. A common
    # profile criterion is required, which avoids comparing unrelated positive/negative facts.
    validated = list(relations.values())
    positive = [
        item
        for item in validated
        if item.relation == EvidenceRelation.SUPPORTS and item.criterion
    ]
    negative = [
        item
        for item in validated
        if item.polarity == EvidencePolarity.NEGATIVE and item.criterion
    ]
    for favorable in positive:
        for unfavorable in negative:
            if (
                favorable.criterion != unfavorable.criterion
                or favorable.document_id == unfavorable.document_id
                or favorable.evidence_id == unfavorable.evidence_id
            ):
                continue
            evidence_ids = sorted([favorable.evidence_id, unfavorable.evidence_id])
            synthetic_evidence_id = "|".join(evidence_ids)
            relation_id = _relation_id(
                hypothesis_id,
                synthetic_evidence_id,
                EvidenceRelation.DOCUMENT_CONFLICT,
                favorable.criterion,
            )
            relations[relation_id] = EvidenceRelationAssessment(
                relation_id=relation_id,
                hypothesis_id=hypothesis_id,
                evidence_id=favorable.evidence_id,
                relation=EvidenceRelation.DOCUMENT_CONFLICT,
                relevant=True,
                temporal_consistent=False,
                knowledge_ids=favorable.knowledge_ids,
                patient_observation=(
                    f"{favorable.patient_observation} / {unfavorable.patient_observation}"
                ),
                document_id=favorable.document_id,
                chunk_id=favorable.chunk_id,
                criterion=favorable.criterion,
                polarity=EvidencePolarity.NEUTRAL,
                validation_status=RelationValidationStatus.VALIDATED,
                evidence_tier=EvidenceTier.CONTRADICTORY,
                specificity=EvidenceSpecificity.HIGH,
                classification_rationale=(
                    "Sources vérifiées de polarités opposées pour le même critère."
                ),
                related_evidence_ids=evidence_ids,
                rationale=(
                    "Deux documents portent des polarités opposées sur le même critère; "
                    "l'évolution temporelle doit être vérifiée avant interprétation."
                ),
            )

    return _collapse_repeated_relations(list(relations.values()))


def relation_ids(
    relations: list[EvidenceRelationAssessment], relation: EvidenceRelation
) -> list[str]:
    return sorted(
        {
            item.evidence_id
            for item in relations
            if item.relation == relation
            and item.validation_status == RelationValidationStatus.VALIDATED
        }
    )


def build_validated_assertions(
    hypothesis_id: str,
    disease_name: str,
    relations: list[EvidenceRelationAssessment],
) -> list[ValidatedAssertion]:
    assertions = [
        ValidatedAssertion(
            assertion_id=f"assert_hyp_{hypothesis_id}",
            origin=AssertionOrigin.UNVERIFIED_HYPOTHESIS,
            text=f"{disease_name} est une hypothèse documentaire non confirmée.",
            verification_status=VerificationStatus.PENDING,
        )
    ]
    seen_patient: set[str] = set()
    seen_knowledge: set[tuple[str, str]] = set()
    for relation in relations:
        if (
            relation.document_id is not None
            and relation.evidence_id not in seen_patient
            and relation.patient_observation
        ):
            seen_patient.add(relation.evidence_id)
            assertions.append(
                ValidatedAssertion(
                    assertion_id=f"assert_patient_{relation.evidence_id}",
                    origin=AssertionOrigin.PATIENT_FACT,
                    text=relation.patient_observation,
                    evidence_ids=[relation.evidence_id],
                    verification_status=VerificationStatus.VERIFIED,
                )
            )
        if relation.criterion:
            for knowledge_id in relation.knowledge_ids:
                key = (knowledge_id, relation.criterion)
                if key in seen_knowledge:
                    continue
                seen_knowledge.add(key)
                assertions.append(
                    ValidatedAssertion(
                        assertion_id=(
                            "assert_knowledge_"
                            + hashlib.sha256(f"{knowledge_id}:{relation.criterion}".encode())
                            .hexdigest()[:16]
                        ),
                        origin=AssertionOrigin.MEDICAL_KNOWLEDGE,
                        text=relation.criterion,
                        knowledge_ids=[knowledge_id],
                        verification_status=VerificationStatus.VERIFIED,
                    )
                )
        assertions.append(
            ValidatedAssertion(
                assertion_id=f"assert_inference_{relation.relation_id}",
                origin=AssertionOrigin.CLINICAL_INFERENCE,
                text=relation.rationale,
                evidence_ids=[relation.evidence_id, *relation.related_evidence_ids],
                knowledge_ids=relation.knowledge_ids,
                verification_status=(
                    VerificationStatus.VERIFIED
                    if relation.validation_status == RelationValidationStatus.VALIDATED
                    else VerificationStatus.REJECTED
                ),
            )
        )
    return assertions


def render_validated_narrative(
    disease_name: str,
    relations: list[EvidenceRelationAssessment],
) -> str:
    """Render the report from validated relations only, never from LLM narrative text."""

    groups = {
        relation: [item for item in relations if item.relation == relation]
        for relation in EvidenceRelation
    }
    if not relations or not any(item.relevant for item in relations):
        return (
            f"{disease_name} est non évaluable : aucune relation entre une observation "
            "patient vérifiée et un critère médical sourcé n'a été validée."
        )

    parts = [f"Évaluation documentaire de {disease_name}."]

    def describe(relation: EvidenceRelation, label: str) -> None:
        items = groups[relation]
        if not items:
            return
        details = "; ".join(
            f"[{item.evidence_id}] {item.patient_observation} "
            f"(critère : {item.criterion or 'non vérifiable'})"
            for item in items
        )
        parts.append(f"{label} : {details}.")

    describe(EvidenceRelation.SUPPORTS, "Éléments favorables validés")
    describe(EvidenceRelation.CONTRADICTS, "Éléments défavorables validés")
    describe(EvidenceRelation.NEUTRAL, "Éléments pertinents non discriminants")
    describe(EvidenceRelation.MISSING, "Informations discriminantes manquantes")
    describe(EvidenceRelation.DOCUMENT_CONFLICT, "Contradictions entre documents")
    describe(EvidenceRelation.UNVERIFIABLE, "Relations proposées mais non vérifiables")
    describe(EvidenceRelation.UNCERTAIN, "Relations incertaines")
    parts.append(
        "Cette synthèse décrit la traçabilité documentaire et ne confirme ni n'exclut "
        "un diagnostic clinique."
    )
    return " ".join(parts)
