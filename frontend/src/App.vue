<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import AppIcon from './components/AppIcon.vue'
import InvestigationReport from './components/InvestigationReport.vue'
import ToastStack from './components/ToastStack.vue'
import DashboardView from './views/DashboardView.vue'
import DocumentsView from './views/DocumentsView.vue'
import FindingsView from './views/FindingsView.vue'
import InvestigationsView from './views/InvestigationsView.vue'
import TimelineView from './views/TimelineView.vue'
import { medconnectApi } from './lib/api.js'

const navigation = [
  { id: 'dashboard', label: 'Tableau de bord', icon: 'grid' },
  { id: 'documents', label: 'Documents', icon: 'file' },
  { id: 'timeline', label: 'Chronologie', icon: 'clock' },
  { id: 'findings', label: 'Points à clarifier', icon: 'alert' },
  { id: 'investigations', label: 'Investigations', icon: 'spark' },
]

const activeView = ref('dashboard')
const sidebarOpen = ref(false)
const patientPickerOpen = ref(false)
const createPatientOpen = ref(false)
const patientSearch = ref('')
const newPatientId = ref('')
const patients = ref([])
const selectedPatient = ref(localStorage.getItem('medconnect.patient') || '')
const apiOnline = ref(false)
const dashboard = ref({})
const documents = ref([])
const events = ref([])
const findings = ref([])
const investigations = ref([])
const selectedDocument = ref(null)
const currentReport = ref(null)
const eventSources = ref({})
const busy = ref({ refresh: false, analysis: false, upload: false, investigation: false, document: false, rerun: '', source: '' })
const toasts = ref([])
let toastSequence = 0
let refreshSequence = 0
let initializing = true

const filteredPatients = computed(() => {
  const query = patientSearch.value.trim().toLocaleLowerCase()
  return query ? patients.value.filter((item) => item.toLocaleLowerCase().includes(query)) : patients.value
})
const currentViewLabel = computed(() => navigation.find((item) => item.id === activeView.value)?.label || '')

function toast(type, title, message = '') {
  const id = ++toastSequence
  toasts.value.push({ id, type, title, message })
  window.setTimeout(() => dismissToast(id), 5200)
}
function dismissToast(id) {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

async function loadPatients() {
  patients.value = await medconnectApi.patients()
  if (!selectedPatient.value && patients.value.length) selectedPatient.value = patients.value[0]
  if (selectedPatient.value && !patients.value.includes(selectedPatient.value)) selectedPatient.value = patients.value[0] || ''
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function initializeApplication() {
  const maximumAttempts = 60
  let lastError = null
  busy.value.refresh = true
  for (let attempt = 1; attempt <= maximumAttempts; attempt += 1) {
    try {
      await medconnectApi.health()
      await loadPatients()
      apiOnline.value = true
      initializing = false
      resetPatientContext()
      await refreshPatientData({ quiet: true })
      return
    } catch (error) {
      lastError = error
      apiOnline.value = false
      if (attempt < maximumAttempts) {
        await wait(Math.min(250 * attempt, 2000))
      }
    }
  }
  initializing = false
  busy.value.refresh = false
  toast(
    'error',
    'Initialisation impossible',
    lastError?.message || 'Le backend MedConnect ne répond pas.',
  )
}

async function refreshPatientData({ quiet = false } = {}) {
  const patientId = selectedPatient.value
  const requestSequence = ++refreshSequence
  if (!patientId) {
    busy.value.refresh = false
    dashboard.value = {}
    documents.value = []
    events.value = []
    findings.value = []
    investigations.value = []
    return
  }
  busy.value.refresh = true
  try {
    const [nextDashboard, nextDocuments, nextEvents, nextFindings, nextInvestigations] = await Promise.all([
      medconnectApi.dashboard(patientId),
      medconnectApi.documents(patientId),
      medconnectApi.timeline(patientId),
      medconnectApi.findings(patientId),
      medconnectApi.investigations(patientId),
    ])
    if (requestSequence !== refreshSequence || selectedPatient.value !== patientId) return
    dashboard.value = nextDashboard
    documents.value = nextDocuments
    events.value = nextEvents
    findings.value = nextFindings
    investigations.value = nextInvestigations.filter((item) => item.patient_id === patientId)
    apiOnline.value = true
    if (!quiet) toast('success', 'Dossier actualisé')
  } catch (error) {
    if (requestSequence !== refreshSequence || selectedPatient.value !== patientId) return
    apiOnline.value = false
    toast('error', 'Actualisation impossible', error.message)
  } finally {
    if (requestSequence === refreshSequence) busy.value.refresh = false
  }
}

function resetPatientContext() {
  dashboard.value = {}
  documents.value = []
  events.value = []
  findings.value = []
  investigations.value = []
  selectedDocument.value = null
  currentReport.value = null
  eventSources.value = {}
}

async function createPatient() {
  const value = newPatientId.value.trim()
  if (value.length < 2) return toast('error', 'Identifiant incomplet', 'Saisissez au moins deux caractères.')
  try {
    await medconnectApi.createPatient(value)
    await loadPatients()
    selectedPatient.value = value
    newPatientId.value = ''
    createPatientOpen.value = false
    patientPickerOpen.value = false
    toast('success', 'Dossier créé', `Le dossier ${value} est prêt.`)
  } catch (error) {
    toast('error', 'Création impossible', error.message)
  }
}

function selectPatient(patientId) {
  selectedPatient.value = patientId
  patientPickerOpen.value = false
  patientSearch.value = ''
  sidebarOpen.value = false
}

function navigate(view) {
  activeView.value = view
  sidebarOpen.value = false
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function uploadFiles(files) {
  const patientId = selectedPatient.value
  if (!patientId || !files.length) return
  busy.value.upload = true
  try {
    const result = await medconnectApi.uploadDocuments(patientId, files)
    if (selectedPatient.value !== patientId) return
    const uploadedDocuments = result
      .map((item) => item.document)
      .filter((document) => document?.patient_id === patientId)
    const documentsById = new Map(
      [...documents.value, ...uploadedDocuments].map((document) => [document.id, document]),
    )
    documents.value = [...documentsById.values()].sort(
      (left, right) => new Date(right.created_at) - new Date(left.created_at),
    )
    dashboard.value = {
      ...dashboard.value,
      document_count: documents.value.length,
      recent_documents: documents.value.slice(0, 5),
    }
    const duplicates = result.filter((item) => item.duplicate).length
    toast('success', 'Documents traités', `${result.length - duplicates} importé(s), ${duplicates} doublon(s).`)
    await refreshPatientData({ quiet: true })
  } catch (error) {
    toast('error', 'Import impossible', error.message)
  } finally {
    busy.value.upload = false
  }
}

async function openDocument(documentId) {
  busy.value.document = true
  selectedDocument.value = null
  try {
    selectedDocument.value = await medconnectApi.document(documentId, selectedPatient.value)
  } catch (error) {
    toast('error', 'Document inaccessible', error.message)
  } finally {
    busy.value.document = false
  }
}

async function deleteDocument(document) {
  if (!window.confirm(`Supprimer « ${document.filename} » et son index associé ?`)) return
  try {
    await medconnectApi.deleteDocument(document.id)
    toast('success', 'Document supprimé', document.filename)
    await refreshPatientData({ quiet: true })
  } catch (error) {
    toast('error', 'Suppression impossible', error.message)
  }
}

async function runAnalysis() {
  if (!selectedPatient.value) return
  busy.value.analysis = true
  try {
    const result = await medconnectApi.runAnalysis(selectedPatient.value)
    toast('success', 'Analyse documentaire terminée', `${result.events?.length || 0} événement(s) et ${result.findings?.length || 0} point(s) à clarifier.`)
    await refreshPatientData({ quiet: true })
  } catch (error) {
    toast('error', 'Analyse interrompue', error.message)
  } finally {
    busy.value.analysis = false
  }
}

function normalizeStoredInvestigation(payload) {
  if (!payload?.state) return payload
  return {
    ...payload.state,
    investigation_id: payload.investigation_id,
    patient_id: payload.patient_id,
    consultation_symptoms:
      payload.consultation_symptoms || payload.state.consultation_symptoms || '',
    model_version: payload.model_version,
    final_assessment: payload.final_assessment || payload.state.final_assessment,
    clinical_evidence: payload.state.extracted_clinical_facts || [],
  }
}

async function runInvestigation(symptoms) {
  const patientId = selectedPatient.value
  if (!patientId) return
  busy.value.investigation = true
  try {
    const result = await medconnectApi.runInvestigation(patientId, symptoms)
    if (selectedPatient.value !== patientId) return
    currentReport.value = result
    toast('success', 'Investigation terminée', 'Le rapport documentaire est disponible.')
    await refreshPatientData({ quiet: true })
  } catch (error) {
    toast('error', 'Investigation interrompue', error.message)
  } finally {
    busy.value.investigation = false
  }
}

async function openInvestigation(investigationId) {
  const patientId = selectedPatient.value
  if (!patientId) return
  try {
    const result = await medconnectApi.investigation(investigationId, patientId)
    if (selectedPatient.value !== patientId) return
    currentReport.value = normalizeStoredInvestigation(result)
  } catch (error) {
    if (selectedPatient.value !== patientId) return
    toast('error', 'Rapport inaccessible', error.message)
  }
}

async function rerunInvestigation(investigationId) {
  const patientId = selectedPatient.value
  if (!patientId) return
  busy.value.rerun = investigationId
  try {
    const result = await medconnectApi.rerunInvestigation(investigationId, patientId)
    if (selectedPatient.value !== patientId) return
    currentReport.value = result
    toast('success', 'Nouvelle investigation terminée')
    await refreshPatientData({ quiet: true })
  } catch (error) {
    if (selectedPatient.value !== patientId) return
    toast('error', 'Relance impossible', error.message)
  } finally {
    busy.value.rerun = ''
  }
}

async function toggleEventSources(eventId) {
  if (eventSources.value[eventId]) {
    const next = { ...eventSources.value }
    delete next[eventId]
    eventSources.value = next
    return
  }
  busy.value.source = eventId
  try {
    eventSources.value = { ...eventSources.value, [eventId]: await medconnectApi.eventSources(eventId, selectedPatient.value) }
  } catch (error) {
    toast('error', 'Source inaccessible', error.message)
  } finally {
    busy.value.source = ''
  }
}

watch(selectedPatient, async (value) => {
  if (value) localStorage.setItem('medconnect.patient', value)
  else localStorage.removeItem('medconnect.patient')
  resetPatientContext()
  if (initializing) return
  await refreshPatientData({ quiet: true })
})

onMounted(initializeApplication)
</script>

<template>
  <div class="app-shell">
    <div v-if="sidebarOpen" class="mobile-overlay" @click="sidebarOpen = false" />
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand">
        <span class="brand-mark"><AppIcon name="activity" :size="25" /></span>
        <span><strong>MedConnect</strong><small>AI workspace</small></span>
      </div>

      <div class="patient-switcher">
        <span class="sidebar-label">Dossier actif</span>
        <button class="patient-button" type="button" @click="patientPickerOpen = !patientPickerOpen">
          <span class="patient-avatar">{{ selectedPatient ? selectedPatient.slice(0, 2).toUpperCase() : '—' }}</span>
          <span><strong>{{ selectedPatient || 'Aucun patient' }}</strong><small>{{ selectedPatient ? 'Dossier local' : 'Sélection requise' }}</small></span>
          <AppIcon name="chevron" :size="15" />
        </button>
        <div v-if="patientPickerOpen" class="patient-popover">
          <label class="search-field"><AppIcon name="search" :size="16" /><input v-model="patientSearch" autofocus placeholder="Rechercher un dossier" /></label>
          <div class="patient-options">
            <button v-for="patient in filteredPatients" :key="patient" :class="{ active: patient === selectedPatient }" @click="selectPatient(patient)">
              <span>{{ patient.slice(0, 2).toUpperCase() }}</span>{{ patient }}<AppIcon v-if="patient === selectedPatient" name="check" :size="15" />
            </button>
            <p v-if="!filteredPatients.length">Aucun dossier trouvé.</p>
          </div>
          <button class="create-patient-link" @click="createPatientOpen = true; patientPickerOpen = false"><AppIcon name="plus" :size="16" />Créer un dossier</button>
        </div>
      </div>

      <nav class="main-nav" aria-label="Navigation principale">
        <span class="sidebar-label">Espace de travail</span>
        <button v-for="item in navigation" :key="item.id" :class="{ active: activeView === item.id }" @click="navigate(item.id)">
          <AppIcon :name="item.icon" :size="19" />{{ item.label }}
          <span v-if="item.id === 'findings' && dashboard.finding_count" class="nav-badge">{{ dashboard.finding_count }}</span>
        </button>
      </nav>

      <div class="sidebar-footer">
        <div class="local-status"><span :class="{ online: apiOnline }" /><div><strong>{{ apiOnline ? 'Services disponibles' : 'Services hors ligne' }}</strong><small>Traitement local</small></div></div>
        <p><AppIcon name="shield" :size="14" /> Prototype d’aide documentaire<br />Non destiné au diagnostic autonome.</p>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <button class="mobile-menu" aria-label="Ouvrir le menu" @click="sidebarOpen = true"><AppIcon name="menu" /></button>
        <div><span>{{ currentViewLabel }}</span><small v-if="selectedPatient">{{ selectedPatient }}</small></div>
        <div class="topbar-actions">
          <span class="privacy-chip"><AppIcon name="shield" :size="14" />Données locales</span>
          <button class="icon-button" :disabled="busy.refresh" aria-label="Actualiser" @click="refreshPatientData()"><AppIcon name="refresh" :class="{ spinning: busy.refresh }" :size="18" /></button>
        </div>
      </header>

      <main class="main-content">
        <DashboardView
          v-if="activeView === 'dashboard'"
          :key="`dashboard:${selectedPatient}`"
          :patient-id="selectedPatient"
          :dashboard="dashboard"
          :documents="documents"
          :investigations="investigations"
          :analysis-busy="busy.analysis"
          :investigation-busy="busy.investigation"
          :upload-busy="busy.upload"
          :loading="busy.refresh"
          @navigate="navigate"
          @run-analysis="runAnalysis"
          @run-investigation="runInvestigation"
          @upload="uploadFiles"
          @open-investigation="openInvestigation"
        />
        <DocumentsView
          v-else-if="activeView === 'documents'"
          :patient-id="selectedPatient"
          :documents="documents"
          :busy="busy.upload"
          :detail-busy="busy.document"
          :loading="busy.refresh"
          :selected-document="selectedDocument"
          @upload="uploadFiles"
          @open="openDocument"
          @close="selectedDocument = null"
          @delete="deleteDocument"
        />
        <TimelineView v-else-if="activeView === 'timeline'" :events="events" :sources="eventSources" :source-busy="busy.source" @sources="toggleEventSources" />
        <FindingsView v-else-if="activeView === 'findings'" :findings="findings" :busy="busy.analysis" @run-analysis="runAnalysis" />
        <InvestigationsView
          v-else
          :key="`investigations:${selectedPatient}`"
          :patient-id="selectedPatient"
          :investigations="investigations"
          :busy="busy.investigation"
          :action-busy="busy.rerun"
          @run="runInvestigation"
          @open="openInvestigation"
          @rerun="rerunInvestigation"
        />
      </main>
    </div>

    <div v-if="createPatientOpen" class="modal-backdrop" @click.self="createPatientOpen = false">
      <form class="modal-card patient-modal" @submit.prevent="createPatient">
        <header><div><span class="eyebrow">Nouveau dossier</span><h2>Créer un patient</h2></div><button type="button" class="icon-button" @click="createPatientOpen = false"><AppIcon name="close" /></button></header>
        <label><span>Identifiant du dossier</span><input v-model="newPatientId" autofocus required minlength="2" maxlength="100" placeholder="Ex. patient-104" /></label>
        <p>Utilisez un identifiant pseudonymisé. N’inscrivez pas de nom complet dans cet écran.</p>
        <footer><button type="button" class="button button-ghost" @click="createPatientOpen = false">Annuler</button><button class="button button-primary" type="submit"><AppIcon name="plus" :size="17" />Créer le dossier</button></footer>
      </form>
    </div>

    <div v-if="currentReport" class="report-backdrop" @click.self="currentReport = null">
      <InvestigationReport :report="currentReport" @close="currentReport = null" />
    </div>

    <ToastStack :toasts="toasts" @dismiss="dismissToast" />
  </div>
</template>
