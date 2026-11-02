<script setup>
import AppIcon from '../components/AppIcon.vue'
import SymptomComposer from '../components/SymptomComposer.vue'

defineProps({
  patientId: { type: String, default: '' },
  dashboard: { type: Object, default: () => ({}) },
  documents: { type: Array, default: () => [] },
  investigations: { type: Array, default: () => [] },
  analysisBusy: { type: Boolean, default: false },
  investigationBusy: { type: Boolean, default: false },
  uploadBusy: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['navigate', 'run-analysis', 'run-investigation', 'upload', 'open-investigation'])

function formatDate(value) {
  if (!value) return 'Date inconnue'
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium' }).format(new Date(value))
}

function handleFiles(event) {
  const files = [...event.target.files]
  if (files.length) emit('upload', files)
  event.target.value = ''
}
</script>

<template>
  <div v-if="!patientId" class="empty-page">
    <div class="empty-illustration"><AppIcon name="user" :size="38" /></div>
    <span class="eyebrow">Bienvenue</span>
    <h1>Sélectionnez un dossier patient</h1>
    <p>Choisissez un dossier dans la barre latérale ou créez-en un pour commencer.</p>
  </div>

  <template v-else>
    <header class="page-heading dashboard-heading">
      <div>
        <span class="eyebrow">Vue d’ensemble</span>
        <h1>Dossier {{ patientId }}</h1>
        <p>Une vue claire du dossier, des analyses et des actions en attente.</p>
      </div>
      <button class="button button-secondary" :disabled="analysisBusy" @click="emit('run-analysis')">
        <span v-if="analysisBusy" class="spinner" />
        <AppIcon v-else name="refresh" :size="17" />
        {{ analysisBusy ? 'Analyse en cours…' : 'Actualiser l’analyse' }}
      </button>
    </header>

    <section class="metric-grid">
      <button class="metric-card" @click="emit('navigate', 'documents')">
        <span class="metric-icon teal"><AppIcon name="file" /></span>
        <span><strong>{{ loading && dashboard.document_count == null ? '…' : (dashboard.document_count ?? 0) }}</strong><small>Documents</small></span>
        <AppIcon name="chevron" :size="16" />
      </button>
      <button class="metric-card" @click="emit('navigate', 'timeline')">
        <span class="metric-icon blue"><AppIcon name="clock" /></span>
        <span><strong>{{ dashboard.event_count || 0 }}</strong><small>Événements</small></span>
        <AppIcon name="chevron" :size="16" />
      </button>
      <button class="metric-card" @click="emit('navigate', 'findings')">
        <span class="metric-icon amber"><AppIcon name="alert" /></span>
        <span><strong>{{ dashboard.finding_count || 0 }}</strong><small>Points à clarifier</small></span>
        <AppIcon name="chevron" :size="16" />
      </button>
      <button class="metric-card" @click="emit('navigate', 'investigations')">
        <span class="metric-icon violet"><AppIcon name="spark" /></span>
        <span><strong>{{ dashboard.investigation_count || 0 }}</strong><small>Investigations</small></span>
        <AppIcon name="chevron" :size="16" />
      </button>
    </section>

    <section class="dashboard-layout">
      <SymptomComposer
        class="dashboard-composer"
        compact
        :busy="investigationBusy"
        @submit="emit('run-investigation', $event)"
      />

      <aside class="quick-panel">
        <div class="panel-heading">
          <div><span class="eyebrow">Actions rapides</span><h2>Gérer le dossier</h2></div>
        </div>
        <label class="quick-action upload-action" :class="{ disabled: uploadBusy }">
          <input
            type="file"
            multiple
            accept=".pdf,.txt,.md,.markdown,.zip"
            :disabled="uploadBusy"
            @change="handleFiles"
          />
          <span class="quick-icon"><AppIcon name="upload" /></span>
          <span><strong>{{ uploadBusy ? 'Import en cours…' : 'Importer des documents' }}</strong><small>PDF, texte, Markdown ou ZIP</small></span>
          <AppIcon name="chevron" :size="16" />
        </label>
        <button class="quick-action" :disabled="analysisBusy" @click="emit('run-analysis')">
          <span class="quick-icon"><AppIcon name="activity" /></span>
          <span><strong>Reconstruire la chronologie</strong><small>Extraire événements et constats</small></span>
          <AppIcon name="chevron" :size="16" />
        </button>
        <button class="quick-action" @click="emit('navigate', 'documents')">
          <span class="quick-icon"><AppIcon name="file" /></span>
          <span><strong>Gérer les documents</strong><small>{{ loading && !documents.length ? 'Chargement…' : `${documents.length} document(s) indexé(s)` }}</small></span>
          <AppIcon name="chevron" :size="16" />
        </button>
      </aside>
    </section>

    <section class="dashboard-bottom">
      <article class="content-card">
        <div class="panel-heading">
          <div><span class="eyebrow">Derniers éléments</span><h2>Activité du dossier</h2></div>
          <button class="text-button" @click="emit('navigate', 'timeline')">Toute la chronologie</button>
        </div>
        <div v-if="dashboard.recent_events?.length" class="activity-list">
          <div v-for="event in dashboard.recent_events" :key="event.id" class="activity-row">
            <span class="activity-date">{{ formatDate(event.event_date) }}</span>
            <span class="activity-dot" />
            <div><strong>{{ event.event_type }}</strong><p>{{ event.description }}</p></div>
          </div>
        </div>
        <div v-else class="inline-empty">
          <AppIcon name="clock" /><p>Aucun événement extrait. Lancez l’analyse documentaire.</p>
        </div>
      </article>

      <article class="content-card">
        <div class="panel-heading">
          <div><span class="eyebrow">Suivi</span><h2>Investigations récentes</h2></div>
          <button class="text-button" @click="emit('navigate', 'investigations')">Tout afficher</button>
        </div>
        <div v-if="investigations.length" class="investigation-mini-list">
          <button
            v-for="item in investigations.slice(0, 3)"
            :key="item.investigation_id"
            @click="emit('open-investigation', item.investigation_id)"
          >
            <span class="status-dot" :class="item.status" />
            <span><strong>{{ formatDate(item.created_at) }}</strong><small>{{ item.consultation_symptoms }}</small></span>
            <AppIcon name="chevron" :size="16" />
          </button>
        </div>
        <div v-else class="inline-empty">
          <AppIcon name="spark" /><p>Aucune investigation enregistrée pour ce dossier.</p>
        </div>
      </article>
    </section>
  </template>
</template>
