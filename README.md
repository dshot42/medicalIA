# MedConnect AI

MedConnect AI est un MVP local qui importe des documents médicaux, en extrait des événements,
reconstruit une chronologie et signale des points documentaires à clarifier. Chaque événement et
chaque constat renvoient à un passage source exact.

La version actuelle ajoute un moteur d'investigation clinique agentique : un médecin saisit les
symptômes actuels sans indiquer de maladie, puis un graphe itératif explore exclusivement le dossier
du patient, génère plusieurs hypothèses, recherche des arguments favorables et défavorables,
et demande au Qwen local de produire un classement contradictoire sourcé uniquement dans le dossier.

> **Important** — Ce prototype n'est pas un dispositif médical. Il ne fournit aucun diagnostic,
> ne prouve aucune causalité et ne remplace pas un professionnel de santé. Il n'est pas présenté
> comme conforme au RGPD, certifié HDS ou médicalement certifié. Une évaluation juridique,
> sécurité et clinique serait indispensable avant tout usage réel.

## Fonctionnalités

- import multiple de PDF texte, TXT et Markdown, limite de taille configurable ;
- identifiants stables SHA-256, détection des doublons et suppression SQLite/Qdrant/fichier ;
- extraction de texte paginée, chunks traçables et recherche sémantique Qdrant ;
- embeddings locaux sentence-transformers (aucun contenu envoyé à un service d'embeddings) ;
- stockage métier SQLite séparé de l'index vectoriel ;
- workflow LangGraph réel avec état, branche conditionnelle et checkpoints SQLite ;
- quatre détections : contradictions potentielles, proximité temporelle sans causalité,
  suivi non retrouvé et information référencée mais absente ;
- validation Pydantic et vérification mot à mot des citations dans les chunks ;
- API FastAPI, tableau de bord Vue 3/Vite responsive et dix outils MCP stdio ;
- intégration directe du GGUF local Qwen3.5-9B-Q6_K avec `llama-cpp-python`, plus modes Ollama
  et compatible OpenAI pour la synthèse documentaire ;
- 22 documents de démonstration explicitement fictifs répartis entre trois identifiants patients ;
- tests hors ligne utilisant un embedder déterministe injecté, sans LLM externe.
- isolation stricte par `patient_id` pour SQLite, Qdrant, API, MCP et investigations ;
- investigation différentielle en 12 nœuds LangGraph avec plusieurs itérations ;
- citations patient revalidées après chaque sortie structurée de Qwen ;
- historique immuable et relance d'une investigation après ajout de documents ;
- budget d'itérations, budget LLM et délai maximal configurables.

## Architecture

```text
Vue 3/Vite ──HTTP──> FastAPI ──> services partagés ──> SQLite (métier)
                                      │             └> Qdrant local
                                      │                 └> patient_medical_records
MCP stdio ────────────────────────────┘
                                      │
                                      └> LangGraph
                                          LOAD_DOCUMENTS → EXTRACT_EVENTS
                                          → VALIDATE_EVENTS → BUILD_TIMELINE
                                          → DETECT_FINDINGS → VERIFY_SOURCES
                                          → [FLAG_UNCERTAIN] → GENERATE_SUMMARY
```

### Graphe d'investigation clinique

```text
START
  → parse_consultation
  → inventory_patient_documents
  → extract_clinical_evidence
  → control_document_coverage
  → generate_qwen_grounded_differential
  → select_qwen_hypothesis
  → retrieve_qwen_targeted_evidence
  → review_qwen_hypothesis
  → qwen_should_continue
       ├─ continuer → select_qwen_hypothesis
       └─ arrêter   → rank_qwen_differential → END
```

La boucle s'arrête lorsque toutes les alternatives sont examinées, que les recherches restantes
sont redondantes, que le nombre maximal d'itérations ou d'appels LLM est atteint, ou que le délai
maximal est dépassé. Une hypothèse mieux classée ne suffit pas à interrompre les alternatives à ne
pas manquer.

Le mode par défaut `qwen_grounded` traite d'abord chaque document du patient, puis utilise Qdrant
uniquement pour focaliser les recherches dans ce même dossier. Qwen est l'unique source de savoir
médical : aucune base externe ou fiche médicale locale n'intervient dans la génération, la revue ou
le classement. Les sorties sont rendues reproductibles dans une configuration identique par une
génération gloutonne, une seed fixe, des prompts versionnés et une validation JSON stricte.

## Installation Windows (PowerShell)

Python 3.11 est recommandé. Toutes les commandes utilisent explicitement `.venv`.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Ne lancez jamais `pip install` en dehors de `.venv` pour ce projet. Au premier démarrage,
sentence-transformers télécharge le modèle d'embeddings public ; ensuite l'encodage est local.

## Installation Linux

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

## Démarrage rapide

### Lanceur Windows

Double-cliquez sur `launcher.bat`, ou lancez-le depuis PowerShell :

```powershell
.\launcher.bat
```

Le lanceur active explicitement `.venv`, puis vérifie ses dépendances, le GGUF et les ports. Il charge les dossiers fictifs de
façon idempotente, démarre FastAPI et l'interface Vue dans la même console visible, puis ouvre le tableau de bord.
`Ctrl+C` ou la fermeture de cette console arrête FastAPI, Vue et leurs sous-processus. Le launcher affiche en direct
leurs sorties techniques ; le journal structuré
`data/logs/medconnect.log` conserve les étapes et compteurs de progression, sans texte de symptômes,
citations ni raisonnement interne du modèle.

Options utiles :

```powershell
.\launcher.bat -CheckOnly
.\launcher.bat -SkipDemo -NoBrowser
.\launcher.bat -ApiPort 8080 -FrontendPort 5174
```

### Démarrage manuel

Chargez les 12 documents fictifs et lancez l'analyse :

```powershell
.\.venv\Scripts\python.exe -m scripts.load_demo
```

Chargez également les dossiers d'investigation fictifs `client_test-1` et
`client_control-2` :

```powershell
.\.venv\Scripts\python.exe -m scripts.load_clinical_demo
```

Terminal 1 — API :

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Terminal 2 — interface :

```powershell
cd frontend
npm install
npm run dev
```

Ouvrez `http://localhost:5173`. La documentation API est disponible sur
`http://127.0.0.1:8000/docs`. Sous Linux, remplacez le chemin de l'interpréteur par
`.venv/bin/python`.

Dans le tableau de bord, sélectionnez `client_test-1`, indiquez les symptômes rapportés, leur
chronologie, leur intensité et les signes explicitement absents, puis lancez l'investigation. Le nom de la maladie attendue n'est présent ni
dans le dossier patient, ni dans les métadonnées, ni dans les règles de classement. Le corrigé est
isolé dans `tests/evaluation/ground_truth.json` et n'est utilisé que par :

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_scenario
```

Ce script ne doit être lancé qu'après une investigation terminée.

## Source du raisonnement clinique

Le savoir clinique provient exclusivement des paramètres du modèle Qwen 3.5 9B local. Les seules
sources documentaires consultées sont les pièces du patient. Les identifiants de preuve proposés
par le modèle sont filtrés contre la liste autorisée puis leurs citations sont revérifiées dans les
chunks SQLite. Cette configuration améliore la reproductibilité technique, mais ne transforme pas
le modèle en référentiel médical validé et ne garantit pas la justesse clinique.

## Configuration LLM

### GGUF local configuré

Le projet est configuré par défaut avec le modèle fourni, chargé paresseusement au premier appel :

```dotenv
MEDCONNECT_LLM_PROVIDER=gguf
MEDCONNECT_GGUF_MODEL_PATH=C:/Users/come/Desktop/ia-llm/phi3/ia_llm/ressources/models/Qwen3.5-9B-Q6_K.gguf
MEDCONNECT_GGUF_CONTEXT_SIZE=65536
MEDCONNECT_GGUF_THREADS=8
MEDCONNECT_GGUF_GPU_LAYERS=-1
MEDCONNECT_MAX_INVESTIGATION_LLM_CALLS=8
MEDCONNECT_INVESTIGATION_TIMEOUT_SECONDS=600
MEDCONNECT_INVESTIGATION_MODE=qwen_grounded
```

L'inférence passe directement par `llama-cpp-python` et n'effectue aucun appel HTTP. Le modèle de
6,95 Gio n'est ni copié dans le dépôt ni chargé au démarrage de l'API. Sur la RTX 5070, toutes les
couches sont déportées avec `MEDCONNECT_GGUF_GPU_LAYERS=-1`. Le lanceur refuse de démarrer si le
paquet actif ne contient pas le backend CUDA. L'installation GPU complète (dépendances puis wheel
CUDA 13.2) se fait avec :

```powershell
.\install-gpu.bat
```

Le script termine par une validation de `ggml-cuda.dll` et de
`llama_supports_gpu_offload()`. Pour seulement contrôler une installation existante :

```powershell
.\install-gpu.ps1 -CheckOnly
```

Si le wheel officiel n'est pas accessible, la reconstruction native utilisée sur cette machine
(RTX 5070, architecture CUDA 120) est automatisée par :

```powershell
.\install-gpu.ps1 -SourceBuild
```

Pour exécuter le moteur entièrement déterministe sans charger le modèle :

```powershell
$env:MEDCONNECT_LLM_PROVIDER="disabled"
```

### Ollama local

Installez Ollama séparément, puis :

```powershell
ollama pull llama3.1:8b
$env:MEDCONNECT_LLM_PROVIDER="ollama"
$env:MEDCONNECT_LLM_MODEL="llama3.1:8b"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

La valeur `MEDCONNECT_OLLAMA_BASE_URL` permet de changer l'adresse locale.

### Fournisseur compatible OpenAI (opt-in explicite)

```powershell
$env:MEDCONNECT_LLM_PROVIDER="openai_compatible"
$env:MEDCONNECT_OPENAI_BASE_URL="https://fournisseur.example/v1"
$env:MEDCONNECT_OPENAI_API_KEY="votre-cle"
$env:MEDCONNECT_LLM_MODEL="modele-json"
```

Cette option peut sortir les données de la machine. Elle est désactivée par défaut et ne doit pas
être utilisée avec des données sensibles sans analyse contractuelle et consentement appropriés.

## Serveur MCP

Démarrage stdio :

```powershell
.\.venv\Scripts\python.exe -m app.mcp.server
```

Exemple de configuration d'un client MCP (remplacez les chemins absolus) :

```json
{
  "mcpServers": {
    "medconnect": {
      "command": "C:\\chemin\\medconnect-ai\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp.server"],
      "cwd": "C:\\chemin\\medconnect-ai"
    }
  }
}
```

Outils exposés :

- `search_medical_documents(query, limit)` ;
- `get_document(document_id)` ;
- `get_medical_timeline()` ;
- `get_analysis_findings()` ;
- `get_event_sources(event_id)`.
- `search_patient_evidence(patient_id, query, limit)` ;
- `search_medical_knowledge(query, limit)` ;
- `get_patient_timeline(patient_id)` ;
- `get_clinical_investigation(patient_id, investigation_id)` ;
- `get_hypothesis_evidence(patient_id, investigation_id, hypothesis_id)`.

Qdrant embarqué verrouille son répertoire pour garantir l'intégrité. Dans ce MVP, n'exécutez pas
l'API et le serveur MCP dans deux processus simultanés sur le même `data/qdrant`. Arrêtez l'API
avant MCP, ou configurez des répertoires distincts. Une instance Qdrant serveur est l'évolution
recommandée pour la concurrence multi-processus.

## Tests et qualité

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

Les tests n'utilisent ni réseau, ni Ollama, ni API LLM. Un double local injecté vérifie les deux
nœuds LLM et le rejet d'une extraction sans citation exacte. Ils couvrent l'ingestion, les références,
les dates inconnues, la chronologie, les quatre constats, la non-confirmation quand une source est
insuffisante, la déduplication, la suppression, Qdrant, FastAPI, LangGraph et l'inventaire MCP.
Ils couvrent aussi les itérations multiples, les recherches ciblées, les contradictions,
l'absence de contamination inter-patients, l'arrêt par budget ou
redondance, les preuves insuffisantes et le déclassement d'une hypothèse contredite.

## Données et confidentialité

- `data/samples/` contient uniquement un dossier créé pour cette démonstration ;
- `.env`, SQLite, l'index Qdrant et les fichiers importés sont ignorés par Git ;
- les logs structurés contiennent des événements techniques, jamais le contenu médical ;
- l'interface Vue n'utilise aucun service de télémétrie ni ressource distante ;
- les noms sont réduits à leur basename, les formats sont en liste blanche, les fichiers texte
  binaires/chiffrés sont refusés et la taille maximale est contrôlée ;
- supprimer un document retire son fichier, ses chunks Qdrant et ses données métier associées.

## Limites connues

- pas d'OCR : un PDF image est refusé s'il ne contient pas de texte extractible ;
- extraction déterministe adaptée au français et au jeu MVP, moins riche qu'un modèle clinique ;
- savoir clinique interne de Qwen non validé ni calibré cliniquement ;
- le taux d'étayage documentaire combine les relations de preuve et la couverture des
  passages ; il ne constitue jamais une probabilité diagnostique ;
- rapprochements par fenêtres temporelles simples, toujours présentés comme incertains ;
- pas d'authentification, chiffrement applicatif, gestion multi-utilisateur ni audit de conformité ;
- Qdrant local ne convient pas à plusieurs processus concurrents ;
- le premier chargement du modèle d'embeddings nécessite un accès à son dépôt public.
- le Qwen GGUF local est volumineux et les deux premières inférences sont lentes en mode CPU ;
  l'annulation dure d'une génération native en cours n'est pas encore disponible ;

## Améliorations recommandées

1. authentification, chiffrement, politiques de rétention et audit de sécurité/confidentialité ;
2. Qdrant serveur et tâches d'analyse asynchrones pour la production ;
3. évaluation clinique annotée de l'extraction/priorisation GGUF et exécution asynchrone annulable ;
4. OCR isolé avec indicateurs de qualité pour les scans ;
5. terminologies médicales contrôlées, tests multilingues et mesures précision/rappel ;
6. export d'un rapport sourcé et interface de correction manuelle.

