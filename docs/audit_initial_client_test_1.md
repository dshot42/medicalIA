# Audit initial — investigation clinique de `client_test-1`

Date de l'audit : 2026-10-26  
Périmètre principal : investigation `inv_815a3148f34e47ac81ee3be9f02308c7`, celle qui a classé « Insuffisance surrénalienne chronique » en première position.  
Méthode : lecture des états persistés dans `data/medconnect.db`, des journaux, des documents patient, du référentiel et du graphe. Aucun composant du moteur n'avait été modifié au moment de cet audit.

## Résumé exécutif

Le classement observé n'est pas le résultat d'une investigation documentaire incomplète : il provient d'un chemin d'exécution qui a **volontairement court-circuité toute l'investigation**. Le paramètre par défaut `investigation_mode` valait `llm_only`. Dans ce mode, le graphe a exécuté seulement :

```text
parse_consultation
  -> generate_llm_only_differential
  -> rank_llm_only_differential
  -> END
```

Qwen a proposé trois hypothèses à partir du seul texte de consultation. L'insuffisance surrénalienne chronique était le premier élément de cette liste et a reçu mécaniquement le meilleur `comparison_score` (3, puis 2, puis 1). Aucun document, chunk, résultat biologique, référentiel médical ou élément contradictoire n'a participé à ce classement.

La sortie elle-même indiquait correctement que sa solidité était « non évaluable avec les données disponibles ». Elle a néanmoins été présentée sous forme de classement, ce qui créait une apparence de priorité clinique injustifiée.

## Investigation auditée

- Identifiant : `inv_815a3148f34e47ac81ee3be9f02308c7`
- Création : `2026-10-26T16:37:54.618643+00:00`
- Modèle : `agentic-clinical-engine-v2+Qwen3.5-9B-Q6_K.gguf`
- Mode : `llm_only`
- Budget : 12 itérations, 8 appels LLM, délai 600 s
- Consommation réelle : 0 itération d'investigation, 2 appels LLM
- Motif : douleurs abdominales intenses intermittentes, nausées, fatigue et faiblesse musculaire depuis plusieurs mois
- Arrêt explicite : `Mode LLM seul : aucun dossier ni référentiel consulté.`

Une investigation encore plus récente (`inv_ed2400bd89d0440a9de9718b921937d4`) concernait un autre motif (« diarrhée et maux de ventre »). Elle n'est donc pas celle visée par le problème décrit.

## Documents et chunks réellement consultés

Pour l'investigation auditée :

- Documents consultés : **0**
- Chunks récupérés : **0**
- Collection `patient_medical_records` interrogée : **non**
- Collection `medical_knowledge_base` interrogée : **non**
- Requêtes ciblées : **0**

Le dossier persistant contient actuellement **8 documents**, chacun composé d'un chunk :

1. `01_consultation_initiale.txt`
2. `02_passage_urgences.txt`
3. `03_bilan_biologique.txt`
4. `04_evaluation_neurologique.md`
5. `05_facteurs_temporels.txt`
6. `06_evaluation_digestive.txt`
7. `07_bilan_endocrinien.txt`
8. `08_suivi_2025.md`

Il existe donc un écart vérifiable avec l'attente de dix documents : ni le répertoire patient ni la base SQLite n'en contiennent dix au moment de l'audit. Cet écart ne doit pas être masqué par un taux de couverture artificiellement calculé sur huit documents.

## Symptômes extraits

L'extraction de la consultation a identifié quatre symptômes présents :

- douleurs abdominales, intenses, depuis plusieurs mois ;
- nausées ;
- fatigue importante ;
- faiblesse musculaire intermittente.

Les informations signalées comme manquantes étaient : durée de chaque épisode, facteurs déclenchants, relation avec les repas et le transit, médicaments/expositions récents et cause identifiée.

Point important : plusieurs de ces informations existaient dans le dossier, mais le mode `llm_only` n'a pas tenté de les retrouver.

## Résultats biologiques identifiés

Pour l'investigation auditée : **aucun**.

Les données pourtant disponibles dans les documents incluaient notamment :

- sodium 126 mmol/L pendant un épisode, explicitement qualifié d'hyponatrémie ;
- potassium 4,1 mmol/L, normal ;
- CRP 2 mg/L, normale ;
- TSH 1,8 mUI/L, normale ;
- sérologie cœliaque négative ;
- recherche de *Helicobacter pylori* négative ;
- cortisol matinal non mesuré ;
- porphobilinogène et acide delta-aminolévulinique urinaires non recherchés pendant les symptômes.

La différence entre résultat normal, résultat anormal et examen non réalisé n'a donc joué aucun rôle dans le classement observé.

## Hypothèses générées et classement

1. Insuffisance surrénalienne chronique
   - justification LLM : faiblesse musculaire et fatigue pouvant évoquer un déficit hormonal surrénalien ;
   - information manquante : dosages hormonaux ;
   - score : 3, correspondant uniquement à sa position initiale dans la liste.
2. Pathologie digestive inflammatoire
   - justification LLM : douleurs abdominales récurrentes à corréler aux marqueurs inflammatoires ;
   - score : 2.
3. Trouble métabolique ou nutritionnel
   - justification LLM : nausées et fatigue compatibles de façon générale ;
   - score : 1.

Aucune hypothèse n'était reliée à une fiche médicale indépendante. Les deux dernières étaient en outre des catégories larges, non des diagnostics suffisamment précis.

## Arguments favorables, défavorables et contradictions

Pour l'insuffisance surrénalienne chronique :

- arguments favorables utilisés : une seule citation du motif, « sensations de faiblesse musculaire », complétée par la fatigue dans le résumé ;
- résultats biologiques favorables utilisés : aucun ;
- chronologie confrontée : aucune ;
- facteurs déclenchants confrontés : aucun ;
- arguments défavorables recherchés : aucun ;
- manifestations explicitement absentes recherchées : aucune ;
- alternative mieux étayée recherchée : aucune ;
- examens de confirmation évalués : aucun ;
- contradictions enregistrées : liste vide.

L'absence d'information n'a pas été utilisée comme preuve négative, ce qui est correct, mais aucune recherche n'a été faite pour distinguer absence de documentation et négativité explicite.

## Pourquoi LangGraph s'est arrêté

Le graphe n'a pas arrêté son investigation sur un critère clinique, une saturation des recherches ou un budget. Le routage conditionnel après `parse_consultation` a sélectionné la branche `llm_only`, laquelle possède une arête directe vers `END` après le classement. Le compteur d'itérations est resté à zéro.

La cause de routage est le défaut de configuration :

```python
investigation_mode: Literal["grounded", "llm_only"] = "llm_only"
```

L'API ne permettait pas au requérant de choisir explicitement le mode. Le service réutilisait simplement le mode global configuré au démarrage.

## Comparaison avec un run grounded antérieur

Le run `inv_0a893284488e4158abf00e85a6509e00`, effectué avec le même motif en mode historique grounded, démontre que les données étaient accessibles :

- 8 passages récupérés ;
- 19 faits cliniques vérifiés ;
- 7 hypothèses générées depuis le référentiel séparé ;
- 7 itérations ;
- 2 appels LLM ;
- arrêt : toutes les hypothèses prioritaires examinées ;
- première hypothèse finale : porphyrie aiguë intermittente.

Ce changement de première hypothèse ne prouve pas à lui seul une amélioration clinique. Il prouve seulement que le chemin grounded exploite des faits discriminants ignorés par le run `llm_only`.

Un autre run grounded (`inv_bbdd37c484114dfea457875bc364d306`) a classé l'insuffisance surrénalienne primaire première après une seule itération, car son budget avait été limité à 1. Il avait bien trouvé une contradiction explicite (pression normale et absence d'hyperpigmentation), mais les autres hypothèses n'avaient pas été soumises au même cycle contradictoire. Cela révèle un deuxième biais : un classement final pouvait comparer une hypothèse investiguée à six hypothèses encore non évaluées.

## Causes probables identifiées

1. **Mode non grounded par défaut** : cause directe du résultat observé.
2. **Classement par ordre de génération en mode LLM seul** : le premier élément reçoit mécaniquement le rang 1.
3. **Aucune barrière de sécurité de présentation** : des hypothèses non vérifiées sont néanmoins ordonnées comme un différentiel clinique.
4. **Couverture documentaire assimilée à la récupération** dans le chemin grounded : la récupération initiale pouvait atteindre tous les documents dans ce petit dossier, mais aucune étape ne le garantissait ni ne le mesurait.
5. **Extraction opportuniste par chunks** : pas d'inventaire systématique par document ni de contrôle spécifique des bilans disponibles.
6. **Discrimination rudimentaire** : le moteur comptait des correspondances textuelles de manifestations et des preuves favorables, ce qui favorisait les tableaux généraux.
7. **Comparaison asymétrique avec petit budget** : une hypothèse investiguée pouvait dominer des alternatives non encore challengées.
8. **Contradiction limitée à des marqueurs textuels** : pas de représentation explicite des associations, de la chronologie ou du statut « examen non réalisé ».
9. **Référentiel indépendant réel mais limité** : sept fiches seulement ; son insuffisance n'était pas exposée au niveau de chaque hypothèse.
10. **Écart de corpus** : huit documents présents, contre dix attendus dans la mission.

## Conclusion de l'audit initial

L'insuffisance surrénalienne chronique n'est ni confirmée ni réfutée par l'investigation observée. Elle a été privilégiée parce que Qwen l'a émise en premier sur la base de symptômes généraux et parce que le mode par défaut empêchait toute vérification. La priorité corrective est donc de rendre obligatoire le chemin grounded, d'inventorier et traiter tout le dossier, puis d'appliquer le même examen favorable/contradictoire à chaque hypothèse avant tout classement.

Un pourcentage de « fiabilité diagnostique » ne peut pas être déduit de ce run : il n'existe ni validation clinique ni calibration statistique. Toute valeur numérique future devra être clairement nommée **indice de fiabilité de l'analyse**, fondée sur des dimensions auditables (couverture, vérification, référentiel, contradictions, données manquantes), et explicitement distinguée d'une probabilité de diagnostic.
