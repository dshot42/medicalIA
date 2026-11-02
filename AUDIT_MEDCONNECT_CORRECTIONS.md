# Audit et corrections du moteur de raisonnement médical MedConnect AI

Date : 26 octobre 2026

## Résumé exécutif

Le défaut de comparaison avait deux causes distinctes. Dans le run le plus récent avant
correction, Qwen avait généré sept hypothèses avec un budget de huit appels : un appel pour
l'extraction, un pour la génération, puis cinq revues. Deux hypothèses restaient `pending`.
Le comparateur exigeait pourtant que toutes les hypothèses aient été revues; il n'était donc
jamais appelé et l'état final restait `not_attempted` sans erreur. Dans des runs antérieurs,
le comparateur était appelé, mais une sortie JSON incomplète (`{}` ou une liste de noms
reformulés) échouait au parsing ou au contrôle d'exhaustivité.

La revue par hypothèse conservait aussi le texte narratif libre du LLM après validation des
identifiants de preuve. Une assertion rejetée par le validateur pouvait donc rester visible
dans le rapport. C'est le mécanisme qui autorisait notamment l'inférence d'une endoscopie
normale non documentée. La sérologie cœliaque négative, quant à elle, avait bien été extraite,
mais l'hypothèse cœliaque faisait partie des hypothèses jamais revues faute de budget.

Le chemin `qwen_grounded` valide maintenant toutes les relations de manière déterministe à
partir du corpus complet et du référentiel avant la comparaison. La narration est rendue
uniquement depuis ces relations. Le LLM n'est plus appelé pour chaque hypothèse : il sert à
générer le différentiel et à effectuer une comparaison qualitative des relations validées.
Une exécution réelle isolée avec Qwen a examiné 8/8 documents, structuré 34 faits, évalué
6/6 hypothèses et terminé la comparaison avec 3 appels LLM. Qwen n'a pas jugé les éléments
suffisants pour privilégier une hypothèse : les rangs sont donc restés à zéro, explicitement.

## Architecture et parcours reconstitués

### Entrée, persistance et RAG

1. `DocumentService` charge les fichiers, les découpe et les persiste dans SQLite.
2. Les chunks patient sont indexés dans Qdrant dans `patient_medical_records`.
3. Le référentiel séparé `medical_knowledge.json` est validé par Pydantic et indexé dans
   `medical_knowledge_base`.
4. `PatientHistoryRetriever.inventory` constitue l'inventaire autoritatif complet depuis le
   dépôt patient. Qdrant reste utilisé pour les recherches ciblées, filtrées par patient.
5. `ClinicalInvestigationService` initialise et persiste l'état LangGraph et le rapport final.

Le graphe d'analyse documentaire général (`app/agents/graph.py`) et le graphe d'investigation
clinique (`app/investigation/graph.py`) sont distincts. La présente correction porte sur le
second.

### Chemin LangGraph actif `qwen_grounded`

```text
START
  -> parse_consultation
  -> inventory_patient_documents
  -> extract_clinical_evidence
  -> control_document_coverage
       -> inventaire à nouveau, au plus trois fois, si incomplet
       -> rapport partiel sans diagnostic si l'incomplétude persiste
  -> generate_qwen_grounded_differential
  -> select_qwen_hypothesis
  -> retrieve_qwen_targeted_evidence
  -> review_qwen_hypothesis         (validation déterministe, aucun appel LLM)
  -> qwen_should_continue
       -> hypothèse suivante, ou nouvelle génération bornée, ou classement
  -> rank_qwen_differential
       -> validation finale de toutes les hypothèses
       -> comparaison Qwen bornée à deux tentatives
  -> END
```

Les opérations de fusion, vérification de provenance, validation des relations et génération
du texte ne sont pas toutes des nœuds LangGraph séparés; elles sont effectuées dans les nœuds
ci-dessus et dans `evidence.py` / `validation.py`.

### Contrats de données

Les objets Pydantic distinguent désormais :

- le passage brut et son statut de vérification;
- le fait patient structuré et toutes ses occurrences documentaires;
- la relation preuve-hypothèse, avec identifiant, hypothèse, observation, document, chunk,
  critère médical, polarité, justification, relations associées et statut de validation;
- les relations favorables, défavorables, non discriminantes, manquantes, contradictoires
  entre documents et non vérifiables;
- les assertions de type fait patient, connaissance médicale, inférence documentaire et
  hypothèse non vérifiée;
- les dimensions documentaires séparées du rapport final.

## Causes racines vérifiées

1. **Budget incompatible avec le nombre d'hypothèses.** Le nombre maximal d'appels était fixe,
   tandis que le générateur pouvait produire jusqu'à sept hypothèses et que chaque revue
   consommait un appel.
2. **Précondition tout-ou-rien du comparateur.** Si une seule hypothèse restait `pending`, la
   comparaison n'était pas tentée.
3. **État ambigu.** Une comparaison non tentée pour cause de budget aboutissait à une phrase
   clinique générique, sans `comparison_error`.
4. **Relances identiques.** Les deux tentatives utilisaient le même prompt et le même seed;
   une sortie invalide était donc souvent reproduite.
5. **Contrat fragile fondé sur les noms.** Accents, parenthèses et reformulations des noms de
   maladies pouvaient invalider une sortie par ailleurs exploitable.
6. **Narration indépendante des preuves.** Le texte libre de la revue LLM était concaténé au
   rapport, même si une assertion n'avait aucune source patient ou aucun critère référentiel.
7. **Validation asymétrique.** Les contradictions n'étaient structurées que pour les
   hypothèses effectivement revues; la sérologie cœliaque négative pouvait ainsi être ignorée.
8. **Absence et négativité insuffisamment séparées.** Un examen non retrouvé n'avait pas de
   représentation explicite et le narrateur pouvait le transformer en résultat normal.
9. **Score composite non calibré.** Le pourcentage combinait proportion de relations favorables
   et couverture, sans validation empirique, tout en étant présenté comme « certitude ».
10. **Déduplication sans provenance multiple.** Une phrase identique reprise dans plusieurs
    documents était fusionnée, mais les sources secondaires étaient perdues.
11. **Journalisation incomplète.** Le logger recevait `attempt`, le détail d'erreur et le nombre
    d'hypothèses, mais son formatter JSON supprimait ces champs.
12. **Fallbacks insuffisamment exposés.** L'échec d'une extraction structurée était mentionné
    dans l'historique, sans champ technique dédié dans le résultat final.

## Corrections effectivement vérifiées

### Comparaison et robustesse LangGraph

- Les revues relationnelles sont déterministes et ne consomment plus un appel LLM par
  hypothèse.
- Toutes les hypothèses sont validées une dernière fois depuis le corpus complet avant la
  comparaison, y compris si le budget d'itérations a interrompu les recherches ciblées.
- Le comparateur échange des `hypothesis_id` stables et vérifie l'ensemble exact des IDs.
- La seconde tentative utilise un seed différent et reçoit le motif technique de la première.
- Les erreurs `ComparisonUnavailable`, `ComparisonBudgetExceeded`, `ComparisonTimeout` et les
  erreurs JSON/Pydantic sont exposées dans `comparison_error` et `technical_warnings`.
- Un échec conserve tous les rangs à zéro. Une comparaison réussie avec
  `can_prioritize_one=false` conserve également les rangs à zéro, mais avec le statut
  `completed` plutôt que `failed`.
- La persistance utilise `completed_with_error` lorsqu'un rapport existe mais que la
  comparaison a techniquement échoué.
- Les boucles restent bornées par les itérations, deux générations au plus, deux tentatives de
  comparaison et le budget d'appels.

### Preuves, contradictions et assertions

- La sérologie cœliaque négative est reliée au marqueur contradictoire de la fiche cœliaque.
- Les examens explicitement non réalisés et les examens utiles non retrouvés dans un corpus
  complètement couvert sont classés `missing`, jamais `contradicts`.
- Un examen non documenté porte une source documentaire nulle et une justification explicite;
  il n'est pas transformé en fait patient.
- Les relations proposées par le LLM mais non confirmées par le référentiel deviennent
  `unverifiable`.
- Les polarités opposées sur le même critère dans des documents différents sont conservées
  comme `document_conflict`; elles ne sont pas résolues arbitrairement.
- Le texte `clinical_reasoning` est rendu exclusivement depuis les relations structurées.
  Le raisonnement initial du générateur reste séparé dans `generation_rationale` et ne sert pas
  de fait.
- Une assertion médicale non sourcée, y compris une association génétique erronée, ne peut
  plus apparaître dans le raisonnement final. Aucune correction propre à une maladie n'a été
  codée dans le validateur.
- L'API de détail d'une hypothèse restitue maintenant relations, assertions et toutes les
  preuves patient/connaissance associées.

### Déduplication et longitudinalité

- Une observation textuellement identique n'est comptée qu'une fois, mais toutes ses
  occurrences (document, chunk, date, page et citation) sont conservées.
- Les dates des relations, les faits de durée et les déclencheurs alimentent un indicateur
  explicite d'analyse chronologique.
- Les faits génériques restent `neutral` lorsqu'ils sont pertinents mais non discriminants;
  leur nombre brut ne produit aucun score supérieur.

### Scoring et restitution

- `certainty_index` reste conservé à zéro pour compatibilité de schéma. `reliability_rate`
  et `analysis_reliability_rate` exposent un taux d'étayage documentaire traçable, fondé sur
  les relations favorables/défavorables/incertaines/manquantes et pondéré par la couverture
  des passages ; ce taux n'est pas une probabilité diagnostique.
- Le rapport expose séparément couverture, sources vérifiées, éléments favorables,
  contradictions, éléments non discriminants, conflits documentaires, relations non
  vérifiables, informations manquantes, cohérence et statut de validation.
- Le mode `llm_only` ne transforme plus l'ordre de génération en classement : tous ses rangs
  sont zéro et son absence de comparaison documentaire est explicite.
- L'interface ne présente plus d'« indice de certitude documentaire ».

## Tests de non-régression ajoutés

Le fichier `tests/test_medical_reasoning_regressions.py` couvre des dossiers et observations
fictifs indépendants :

1. sérologie négative classée comme argument défavorable tracé;
2. endoscopie non documentée classée comme information manquante;
3. assertion médicale non sourcée absente du rapport final;
4. comparaison de deux hypothèses à partir des relations validées;
5. budget de comparaison épuisé signalé comme échec technique;
6. observation répétée comptée une fois avec deux sources conservées;
7. hypothèse sans relation validée classée non évaluable;
8. narration cohérente avec la contradiction structurée;
9. fallback d'extraction structurée exposé dans `technical_warnings`.

Les tests existants ont été adaptés pour vérifier l'absence de score composite, la baisse du
nombre d'appels LLM et la revue déterministe.

## Fichiers modifiés

- `app/models/investigation.py` : catégories de relation, provenance des assertions, sources
  multiples, dimensions documentaires et avertissements techniques.
- `app/models/__init__.py` : exports des nouveaux modèles.
- `app/investigation/evidence.py` : déduplication avec conservation de toutes les occurrences.
- `app/investigation/validation.py` : validation relationnelle complète, informations
  manquantes, conflits inter-documents, assertions et narration déterministe.
- `app/investigation/graph.py` : revue sans appel LLM, validation exhaustive, comparaison par
  IDs, retry contrôlé, statuts d'erreur et suppression des pourcentages actifs.
- `app/investigation/state.py` : avertissements techniques dans l'état LangGraph.
- `app/agents/local_gguf.py` : contrat minimal de comparaison par IDs, seed de relance et
  compatibilité avec les anciennes réponses nommées.
- `app/core/logging.py` : conservation des tentatives, détails techniques et cardinalité.
- `app/services/investigation_service.py` : statut persistant d'erreur, exposition des
  avertissements, relations et assertions.
- `frontend/app.py` : dimensions séparées, incidents techniques visibles, suppression du
  pourcentage de certitude.
- `tests/test_clinical_investigation.py`, `tests/test_clinical_non_regression.py`,
  `tests/test_investigation_refactor.py` : attentes adaptées aux garanties corrigées.
- `tests/test_medical_reasoning_regressions.py` : nouveaux scénarios obligatoires.

## Vérifications exécutées

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
.\.venv\Scripts\python.exe -m py_compile <modules modifiés>
.\.venv\Scripts\python.exe -m ruff check <fichiers modifiés>
```

Résultats : **50 tests passés**, compilation passée, Ruff passé sur tous les fichiers
modifiés. Un avertissement de dépréciation Starlette/AnyIO provenant d'une dépendance reste
présent. Le dépôt fourni ne contient pas de métadonnées Git accessibles; aucun diff Git fiable
n'a donc pu être produit.

Un Ruff global signale encore des dépassements de longueur préexistants dans des fichiers non
modifiés par cette mission (`loader.py`, `discrimination.py`, `knowledge.py`, `repository.py`
et `test_ingestion.py`). Ils n'affectent pas l'exécution ni les tests.

## Investigation réelle avant / après

Baseline persistée `inv_dcd2acce752140d992f7425c5de66b4b` :

| Mesure | Avant | Après (`inv_55a35f91015c469ca84213e01eae6930`) |
|---|---:|---:|
| Documents / passages couverts | 8 / 8 | 8 / 8 |
| Faits structurés vérifiés | 34 | 34 |
| Hypothèses générées | 7 | 6 |
| Hypothèses évaluées | 5 / 7 | 6 / 6 |
| Appels LLM | 7 | 3 |
| Appels de comparaison | 0 | 1 |
| Statut de comparaison | `not_attempted` | `completed` |
| Erreur de comparaison | absente malgré la non-exécution | aucune |
| Rang en cas de non-départage | ambigu dans l'ancien rapport | 0 pour toutes |
| Score composite | pourcentage documentaire | non calculé |

Dans le run après correction :

- maladie cœliaque : 4 relations favorables génériques, 1 contradiction validée (sérologie
  négative), 1 information discriminante manquante;
- ulcère gastroduodénal : 2 contradictions validées et endoscopie utile non documentée classée
  manquante; aucune endoscopie normale inventée;
- porphyrie aiguë intermittente : aucune assertion ALAD dans le raisonnement final;
- Qwen a renvoyé `can_prioritize_one=false`; la comparaison est techniquement réussie mais ne
  crée donc aucune hiérarchie clinique.

La première sortie structurée de l'extraction Qwen a été rejetée pendant ce run. L'extraction
déterministe, strictement fondée sur le texte, a pris le relais et les 34 faits ont été vérifiés.
Le code expose désormais ce type d'incident dans `technical_warnings`; cette dernière exposition
a été vérifiée par test automatisé après le run réel.

## Améliorations probables mais non validées cliniquement

- Le contrat par IDs et la réduction de contexte rendent les comparaisons plus robustes sur
  d'autres modèles locaux, mais cela ne valide pas leur jugement médical.
- Les catégories qualitatives réduisent le biais des symptômes génériques, sans constituer une
  mesure de sensibilité, spécificité ou valeur prédictive.
- La conservation multi-source améliore la traçabilité des répétitions exactes; elle ne résout
  pas toute la coréférence sémantique entre formulations très différentes.

## Points restant à valider ou à corriger

1. Le référentiel local ne contient que sept fiches et utilise un rapprochement lexical. Une
   hypothèse sans correspondance exacte reste volontairement non évaluable.
2. Les sources du référentiel sont déclaratives. Leur contenu, leur version et leur couverture
   doivent faire l'objet d'une gouvernance médicale indépendante avant tout usage clinique.
3. La chronologie structurée détecte dates, durées et déclencheurs, mais ne modélise pas encore
   formellement les fenêtres « pendant/hors épisode » ni la causalité.
4. Un appel llama.cpp en cours ne peut pas être interrompu proprement par le délai global actuel.
   Le graphe contrôle le temps avant les nœuds et expose le dépassement, mais un watchdog de
   processus serait nécessaire pour un timeout strict intra-appel.
5. La comparaison qualitative reste une sortie de Qwen, contrôlée sur sa structure et ses
   entrées, mais non validée empiriquement comme règle de classement clinique.
6. Les anciennes investigations persistées ne sont pas réécrites. Elles doivent être relancées
   pour bénéficier des nouveaux contrats et statuts.
7. Le système reste un prototype documentaire nécessitant une revue professionnelle; il n'est
   ni un dispositif médical validé ni un moteur de diagnostic autonome.

## Conclusion

Les anomalies rapportées n'ont pas été corrigées par des exceptions propres au patient, à la
maladie cœliaque, à la porphyrie ou à l'ulcère. Les corrections portent sur le budget et les
transitions LangGraph, la provenance, la validation relationnelle, la séparation absence /
négativité, le contrat de comparaison, la génération narrative et la présentation des scores.
Le moteur produit désormais des hypothèses documentaires traçables, signale les contradictions
et les données manquantes, distingue non-évaluabilité et faible étayage, et expose explicitement
les échecs techniques.
