# Audit du classement diagnostique

Date : 27 octobre 2026

## Origine des anciens pourcentages

Le score n'était pas produit par Qwen. Le backend calculait `reliability_rate` à partir du nombre de relations documentaires, et l'interface Vue avait aussi un calcul de secours dans `supportRate()`. L'API sérialisait ensuite cette valeur. Le score ne représentait donc pas une probabilité clinique.

Pour l'investigation Laura `inv_afd1b02ae10f4d938106a2a17c6bc157`, les données sauvegardées montrent :

| Hypothèse | Relations favorables | Non discriminantes | Conflits documentaires | Informations manquantes | Ancien score |
|---|---:|---:|---:|---:|---:|
| Infection par le virus Nipah | 9 | 10 | 5 | 0 | 38 % |
| Encéphalite herpétique | 5 | 1 | 0 | 2 | 62 % |

La formule effective était `round(100 × favorables / (favorables + défavorables + neutres + conflits + non vérifiables + manquants) × min(passages examinés / passages disponibles, 1))`. Le dossier était couvert à 100 %, donnant `9/24 ≈ 38 %` contre `5/8 ≈ 62 %`. L'algorithme récompensait la proportion de relations favorables et pénalisait les conflits et les examens absents. Il ne mesurait ni la spécificité, ni la valeur discriminante, ni une probabilité calibrée. Le frontend pouvait recalculer un score similaire en l'absence de la valeur backend.

## Modifications du pipeline

- Qwen reste le moteur local de comparaison. Il propose un statut documentaire, une force des preuves, les preuves citées, les éléments discriminants, les alternatives, une justification, l'incertitude et l'ordre comparatif. Il ne fournit plus de probabilité numérique.
- Les relations validées sont classées en preuves confirmatoires, fortement discriminantes, compatibles non spécifiques, contradictoires, non discriminantes, manquantes ou non vérifiables. Chaque relation conserve sa source et son motif de classification.
- La validation indépendante vérifie les identifiants et le contenu des preuves avant d'accepter le statut `DOCUMENTED_CONFIRMED`. Une preuve confirmatoire vérifiée prend le pas sur des symptômes génériques.
- Les passages composites sont séparés en observations atomiques tout en gardant la citation source. Les mentions répétées d'une même relation sont regroupées et leurs sources conservées.
- Le classement relatif de Qwen est préservé même quand aucune hypothèse unique ne peut être privilégiée ; des rangs égaux représentent une égalité, zéro l'absence d'ordre comparatif défendable. Une confirmation documentaire validée peut être remontée par le validateur indépendant.
- La couverture du corpus est séparée du statut diagnostique. Les anciens champs de fiabilité sont conservés comme alias de compatibilité et signalés comme couverture, non comme probabilité.
- L'interface n'affiche plus le pourcentage d'étayage ; elle montre le statut, la force des preuves, la preuve principale et sa source. La couverture documentaire reste distincte.

## Schéma de sortie

Le contrat `RankedDiagnosis` demeure sérialisable pour les anciennes investigations et ajoute les champs structurés suivants :

```json
{
  "diagnostic_status": "DOCUMENTED_CONFIRMED",
  "evidence_strength_class": "DECISIVE",
  "supporting_evidence_details": [
    {
      "type": "CONFIRMATORY",
      "observation": "Résultat spécifique vérifié dans le dossier",
      "source_id": "identifiant de preuve",
      "specificity": "HIGH",
      "validation": "VALIDATED"
    }
  ],
  "contradicting_evidence_details": [],
  "missing_information": [],
  "comparative_assessment": {
    "relative_position": 1,
    "reason": "Justification comparative issue des preuves validées",
    "limitations": [],
    "alternative_diagnoses": []
  },
  "uncertainty": "LOW_FOR_DOCUMENTED_CONFIRMATION"
}
```

Les anciens `rank`, `evidence_strength`, relations, résumés et références restent présents. Les statuts sont `DOCUMENTED_CONFIRMED`, `STRONGLY_SUPPORTED`, `SUPPORTED`, `POSSIBLE`, `CONTRADICTED` et `INSUFFICIENT_EVIDENCE`.

## Corpus de test

Le manifeste `data/test_corpus_exclusions.json` et le chargeur de démonstration excluent exactement `99_CORRIGE_NE_PAS_IMPORTER.md` du corpus de test. Le script `scripts/prepare_clean_test_corpus.py` enlève de manière reproductible les entrées correspondantes seulement pour le patient demandé, en SQLite et dans la collection Qdrant ciblée. Pour Laura (`b.laura`), le document et ses vecteurs ont été supprimés puis l'absence vérifiée dans les deux index.

## Tests et scénario Laura

Les tests de non-régression couvrent la confirmation spécifique, les symptômes non spécifiques, les hypothèses compatibles sans examen confirmatoire, le regroupement des doublons, les informations manquantes, la validation du schéma, la sérialisation, les en-têtes d'examens sans résultat et l'affichage sans pourcentage. `python -m pytest -q` : **59 tests réussis**. `npm run build` : **réussi**. La vérification `python -m scripts.prepare_clean_test_corpus --patient-id b.laura --dry-run` ne trouve plus de document exclu.

L'investigation antérieure `inv_afd1b02ae10f4d938106a2a17c6bc157` plaçait l'encéphalite herpétique devant Nipah sur le taux documentaire (62 contre 38), malgré 9 relations favorables à Nipah dont une PCR spécifique rapportée. Après nettoyage du corpus (12 documents, fichier de correction absent) et durcissement du validateur, l'investigation `inv_9a8aab24570548e9aa5964588d6174dd` a extrait 109 observations, comparé six hypothèses et consommé quatre appels LLM. Le résultat est :

| Rang | Hypothèse | Statut documentaire | Force des preuves | Preuve principale |
|---:|---|---|---|---|
| 1 | Encéphalite à virus Nipah | `DOCUMENTED_CONFIRMED` | Décisive | Détection d'ARN Nipah par RT-PCR spécifique sur prélèvement respiratoire |
| 2 | Encéphalite herpétique | `POSSIBLE` | Non spécifique | Symptômes neurologiques compatibles ; PCR HSV non documentée |
| 3 | Paludisme grave | `POSSIBLE` | Non spécifique | Symptômes compatibles, avec une contradiction documentaire |
| 4 | Méningite bactérienne | `POSSIBLE` | Non spécifique | Symptômes compatibles |
| 5 | Encéphalite japonaise | `INSUFFICIENT_EVIDENCE` | Insuffisante | Aucune preuve validée |
| 6 | Encéphalite auto-immune | `INSUFFICIENT_EVIDENCE` | Insuffisante | Aucune preuve validée |

L'en-tête d'imagerie seul ne valide désormais aucune preuve discriminante pour HSV ni pour l'encéphalite auto-immune. Nipah passe en tête grâce à la preuve moléculaire validée ; les autres diagnostics restent à leur niveau documentaire. Le LLM a émis des sorties structurées invalides pendant cette exécution (`local_gguf_validation_failed`, `qwen_comparison_failed`), puis le graphe a terminé avec `comparison_status: completed` et le statut Nipah confirmatoire issu du validateur indépendant. Ces erreurs techniques restent visibles dans les journaux et indiquent que la comparaison LLM ne doit pas être présentée comme validée pour cette exécution ; l'ordre constaté est robuste sur le statut confirmatoire, mais l'ordre relatif des hypothèses non confirmées est moins fiable.

## Limites

Le rang est un ordre qualitatif comparatif, pas une probabilité calibrée. Un résultat source doit satisfaire le validateur documentaire avant d'être présenté comme confirmé. La validation documentaire ne remplace pas la décision clinique.
