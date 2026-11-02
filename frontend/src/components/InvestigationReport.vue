<script setup>
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({ report: { type: Object, required: true } })
const emit = defineEmits(['close', 'load-evidence'])
const activeTab = ref('results')
const openDiagnoses = ref(new Set())

const assessment = computed(() => props.report.final_assessment || {})
const coverage = computed(() => props.report.coverage_report || assessment.value.coverage || {})
const patientEvidence = computed(() =>
  Object.fromEntries((props.report.clinical_evidence || []).map((item) => [item.evidence_id, item])),
)
const knowledgeEvidence = computed(() =>
  Object.fromEntries(
    (props.report.medical_knowledge_evidence || []).map((item) => [item.knowledge_id, item]),
  ),
)

function diagnosticStatusLabel(diagnosis) {
  return {
    DOCUMENTED_CONFIRMED: 'Confirmé dans les documents',
    STRONGLY_SUPPORTED: 'Fortement étayé',
    SUPPORTED: 'Étayé',
    POSSIBLE: 'Hypothèse compatible',
    CONTRADICTED: 'Contredit par le dossier',
    INSUFFICIENT_EVIDENCE: 'Données insuffisantes',
  }[diagnosis.diagnostic_status] || diagnosis.evidence_strength || 'Non évaluable'
}

function evidenceStrengthLabel(diagnosis) {
  return {
    DECISIVE: 'Confirmatoire',
    HIGHLY_DISCRIMINATING: 'Fortement discriminante',
    SPECIFIC: 'Spécifique',
    NON_SPECIFIC: 'Compatible non spécifique',
    CONTRADICTORY: 'Contradictoire',
    INSUFFICIENT: 'Insuffisante',
  }[diagnosis.evidence_strength_class] || diagnosis.evidence_strength || 'Non évaluée'
}

function toggleDiagnosis(id) {
  const next = new Set(openDiagnoses.value)
  next.has(id) ? next.delete(id) : next.add(id)
  openDiagnoses.value = next
}

function sourceLabel(item) {
  if (!item) return ''
  return `${item.filename || 'Source'}${item.page ? ` · page ${item.page}` : ''}`
}

function relationLabel(value) {
  return {
    supports: 'Favorable',
    contradicts: 'Défavorable',
    neutral: 'Non discriminante',
    missing: 'Information manquante',
    document_conflict: 'Conflit documentaire',
    unverifiable: 'Non vérifiable',
  }[value] || value
}
</script>

<template>
  <div class="report-shell">
    <header class="report-header">
      <div>
        <span class="eyebrow">Rapport documentaire</span>
        <h2>Investigation {{ report.investigation_id?.slice(-8) }}</h2>
        <p>
          {{ report.model_version || 'Modèle non renseigné' }} ·
          {{ report.llm_call_count || 0 }} appel(s) LLM local(aux)
        </p>
      </div>
      <button class="icon-button" type="button" aria-label="Fermer le rapport" @click="emit('close')">
        <AppIcon name="close" />
      </button>
    </header>

    <div class="report-status-grid">
      <div>
        <span>Couverture</span>
        <strong>{{ coverage.passages_examined || 0 }}/{{ coverage.passages_total || 0 }} passages</strong>
      </div>
      <div>
        <span>Comparaison</span>
        <strong :class="assessment.comparison_status === 'failed' ? 'text-danger' : 'text-success'">
          {{ assessment.comparison_status || report.comparison_status || 'non tentée' }}
        </strong>
      </div>
      <div>
        <span>État</span>
        <strong>{{ assessment.investigation_completeness || 'non concluant' }}</strong>
      </div>
      <div>
        <span>Itérations</span>
        <strong>{{ report.iteration_count || 0 }}</strong>
      </div>
    </div>

    <section v-if="report.consultation_symptoms" class="consultation-prompt">
      <span>Symptômes décrits dans le prompt</span>
      <p>{{ report.consultation_symptoms }}</p>
    </section>

    <div v-if="assessment.technical_warnings?.length || report.technical_warnings?.length" class="notice notice-danger">
      <AppIcon name="alert" />
      <div>
        <strong>Incident technique signalé</strong>
        <p v-for="warning in assessment.technical_warnings || report.technical_warnings" :key="warning">
          {{ warning }}
        </p>
      </div>
    </div>
    <div class="notice notice-info">
      <AppIcon name="shield" />
      <div>
        <strong>{{ assessment.safety_notice || 'Hypothèses documentaires à valider humainement.' }}</strong>
        <p>{{ assessment.conclusion || 'Évaluation incomplète.' }}</p>
      </div>
    </div>

    <nav class="report-tabs" aria-label="Sections du rapport">
      <button :class="{ active: activeTab === 'results' }" @click="activeTab = 'results'">Hypothèses</button>
      <button :class="{ active: activeTab === 'trace' }" @click="activeTab = 'trace'">Traçabilité</button>
      <button :class="{ active: activeTab === 'facts' }" @click="activeTab = 'facts'">Faits extraits</button>
    </nav>

    <section v-if="activeTab === 'results'" class="report-content">
      <div v-if="!assessment.differential_diagnoses?.length" class="empty-state small">
        <AppIcon name="activity" :size="28" />
        <h3>Aucune hypothèse évaluable</h3>
        <p>Le dossier ne contient pas assez de relations validées pour établir une comparaison.</p>
      </div>
      <article
        v-for="diagnosis in assessment.differential_diagnoses || []"
        :key="diagnosis.hypothesis_id"
        class="diagnosis-card"
      >
        <button class="diagnosis-summary" type="button" @click="toggleDiagnosis(diagnosis.hypothesis_id)">
          <span class="rank-badge" :class="{ unranked: !diagnosis.rank }">
            {{ diagnosis.rank || '—' }}
          </span>
          <span class="diagnosis-title">
            <strong>{{ diagnosis.disease_name }}</strong>
            <small>{{ diagnosticStatusLabel(diagnosis) }}</small>
          </span>
          <span class="support-rate">
            <strong>{{ evidenceStrengthLabel(diagnosis) }}</strong>
            <small>Force des preuves</small>
          </span>
          <span class="relation-counts">
            <span class="count-support">+{{ diagnosis.documentary_assessment?.validated_support_count || diagnosis.supporting_evidence?.length || 0 }}</span>
            <span class="count-against">−{{ diagnosis.documentary_assessment?.validated_contradiction_count || diagnosis.contradicting_evidence?.length || 0 }}</span>
            <span class="count-missing">?{{ diagnosis.missing_information?.length || 0 }}</span>
          </span>
          <AppIcon name="chevron" :class="{ rotated: openDiagnoses.has(diagnosis.hypothesis_id) }" />
        </button>

        <div v-if="openDiagnoses.has(diagnosis.hypothesis_id)" class="diagnosis-details">
          <p class="reasoning">{{ diagnosis.clinical_reasoning }}</p>
          <p class="dimension-note">
            {{ diagnosis.documentary_assessment?.qualitative_basis || assessment.certainty_index_notice }}
          </p>

          <section v-if="diagnosis.primary_evidence" class="missing-section">
            <h4>Preuve principale</h4>
            <p>{{ diagnosis.primary_evidence.observation }}</p>
            <small>
              {{ diagnosis.primary_evidence.source_id || 'Source vérifiée' }} ·
              {{ evidenceStrengthLabel(diagnosis) }}
            </small>
          </section>

          <div class="evidence-columns">
            <section>
              <h4><span class="dot dot-support" /> Arguments favorables</h4>
              <p v-if="!diagnosis.supporting_evidence?.length" class="muted">Aucune preuve favorable validée.</p>
              <div v-for="id in diagnosis.supporting_evidence || []" :key="id" class="evidence-row support">
                <p>{{ patientEvidence[id]?.description || id }}</p>
                <small>{{ sourceLabel(patientEvidence[id]) }}</small>
              </div>
            </section>
            <section>
              <h4><span class="dot dot-against" /> Arguments défavorables</h4>
              <p v-if="!diagnosis.contradicting_evidence?.length" class="muted">Aucune contradiction explicite validée.</p>
              <div v-for="id in diagnosis.contradicting_evidence || []" :key="id" class="evidence-row against">
                <p>{{ patientEvidence[id]?.description || id }}</p>
                <small>{{ sourceLabel(patientEvidence[id]) }}</small>
              </div>
            </section>
          </div>

          <section v-if="diagnosis.missing_information?.length" class="missing-section">
            <h4>Informations discriminantes manquantes</h4>
            <ul><li v-for="item in diagnosis.missing_information" :key="item">{{ item }}</li></ul>
          </section>

          <section v-if="diagnosis.evidence_relations?.length" class="relations-section">
            <h4>Relations validées</h4>
            <div class="relation-table-wrap">
              <table class="relation-table">
                <thead><tr><th>Polarité</th><th>Observation / critère</th><th>Validation</th></tr></thead>
                <tbody>
                  <tr v-for="relation in diagnosis.evidence_relations" :key="relation.relation_id">
                    <td><span class="status-pill" :class="`status-${relation.relation}`">{{ relationLabel(relation.relation) }}</span></td>
                    <td>
                      <strong>{{ relation.patient_observation || relation.rationale }}</strong>
                      <small>{{ relation.criterion || relation.medical_criterion }}</small>
                    </td>
                    <td>{{ relation.validation_status || 'non renseignée' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <details v-if="diagnosis.medical_knowledge_evidence?.length" class="knowledge-details">
            <summary>Référentiel médical utilisé</summary>
            <div v-for="id in diagnosis.medical_knowledge_evidence" :key="id" class="knowledge-row">
              <p>{{ knowledgeEvidence[id]?.statement || id }}</p>
              <a v-if="knowledgeEvidence[id]?.source_url" :href="knowledgeEvidence[id].source_url" target="_blank" rel="noreferrer">
                {{ knowledgeEvidence[id].source_title || 'Consulter la source' }}
              </a>
            </div>
          </details>
        </div>
      </article>
    </section>

    <section v-else-if="activeTab === 'trace'" class="report-content trace-list">
      <div v-for="(entry, index) in report.investigation_history || []" :key="`${entry.node}-${index}`" class="trace-row">
        <span class="trace-marker"><AppIcon name="check" :size="14" /></span>
        <div>
          <strong>{{ entry.node }}</strong>
          <p>{{ entry.details }}</p>
          <small v-if="entry.hypothesis_id">Hypothèse {{ entry.hypothesis_id }}</small>
        </div>
      </div>
    </section>

    <section v-else class="report-content facts-list">
      <div v-for="fact in report.clinical_evidence || []" :key="fact.evidence_id" class="fact-row">
        <span class="status-pill">{{ fact.concept_type || 'fait' }}</span>
        <div>
          <strong>{{ fact.description }}</strong>
          <p>{{ sourceLabel(fact) }} · {{ fact.validation_status || fact.status }}</p>
        </div>
      </div>
    </section>
  </div>
</template>
