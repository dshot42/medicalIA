<script setup>
import { ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'

defineProps({
  patientId: { type: String, default: '' },
  documents: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  detailBusy: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  selectedDocument: { type: Object, default: null },
})
const emit = defineEmits(['upload', 'open', 'close', 'delete'])
const dragging = ref(false)

function sendFiles(files) {
  const accepted = [...files]
  if (accepted.length) emit('upload', accepted)
}

function formatBytes(bytes) {
  if (!bytes) return '0 o'
  const units = ['o', 'Ko', 'Mo', 'Go']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`
}

function formatDate(value) {
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium' }).format(new Date(value))
}
</script>

<template>
  <header class="page-heading">
    <div><span class="eyebrow">Sources patient</span><h1>Documents</h1><p>Importez, consultez et gérez les pièces indexées du dossier.</p></div>
  </header>

  <label
    class="dropzone"
    :class="{ dragging, disabled: busy || !patientId }"
    @dragenter.prevent="dragging = true"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="dragging = false; sendFiles($event.dataTransfer.files)"
  >
    <input type="file" multiple accept=".pdf,.txt,.md,.markdown,.zip" :disabled="busy || !patientId" @change="sendFiles($event.target.files); $event.target.value = ''" />
    <span class="dropzone-icon"><span v-if="busy" class="spinner" /><AppIcon v-else name="upload" :size="24" /></span>
    <span><strong>{{ busy ? 'Import et indexation en cours…' : 'Déposez vos documents ici' }}</strong><small>ou cliquez pour parcourir · PDF, TXT, Markdown, ZIP · 10 Mo par fichier</small></span>
  </label>

  <section class="content-card document-section">
    <div class="panel-heading">
      <div><span class="eyebrow">Bibliothèque</span><h2>{{ loading && !documents.length ? 'Chargement…' : `${documents.length} document(s)` }}</h2></div>
    </div>
    <div v-if="documents.length" class="document-list">
      <article v-for="document in documents" :key="document.id" class="document-row">
        <span class="file-avatar"><AppIcon name="file" /></span>
        <button class="document-main" @click="emit('open', document.id)">
          <strong>{{ document.filename }}</strong>
          <small>{{ formatBytes(document.size_bytes) }} · {{ document.chunk_count }} passage(s) · {{ formatDate(document.created_at) }}</small>
        </button>
        <button class="icon-button danger" type="button" :aria-label="`Supprimer ${document.filename}`" @click="emit('delete', document)">
          <AppIcon name="trash" :size="18" />
        </button>
      </article>
    </div>
    <div v-else-if="loading" class="empty-state small"><span class="spinner" /><h3>Chargement du dossier</h3><p>Récupération des documents indexés…</p></div>
    <div v-else class="empty-state small"><AppIcon name="file" :size="30" /><h3>Aucun document</h3><p>Importez les premières pièces médicales de ce dossier.</p></div>
  </section>

  <div v-if="selectedDocument || detailBusy" class="drawer-backdrop" @click.self="emit('close')">
    <aside class="detail-drawer">
      <header><div><span class="eyebrow">Contenu extrait</span><h2>{{ selectedDocument?.filename || 'Chargement…' }}</h2></div><button class="icon-button" @click="emit('close')"><AppIcon name="close" /></button></header>
      <div v-if="detailBusy" class="drawer-loading"><span class="spinner" />Chargement du document…</div>
      <template v-else-if="selectedDocument">
        <div class="document-metadata"><span>{{ formatBytes(selectedDocument.size_bytes) }}</span><span>{{ selectedDocument.chunk_count }} passages</span><span>{{ selectedDocument.media_type }}</span></div>
        <pre class="document-text">{{ selectedDocument.text }}</pre>
      </template>
    </aside>
  </div>
</template>
