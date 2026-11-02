<script setup>
import { ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import SymptomComposer from '../components/SymptomComposer.vue'

defineProps({ patientId: { type: String, required: true }, investigations: { type: Array, default: () => [] }, busy: { type: Boolean, default: false }, actionBusy: { type: String, default: '' } })
const emit = defineEmits(['run', 'open', 'rerun'])
const showComposer = ref(true)

function formatDate(value) {
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'long', timeStyle: 'short' }).format(new Date(value))
}

function statusLabel(status) {
  return { completed: 'Terminée', completed_with_error: 'Terminée avec incident', incomplete: 'Incomplète' }[status] || status
}
</script>

<template>
  <header class="page-heading">
    <div><span class="eyebrow">Raisonnement documentaire</span><h1>Investigations</h1><p>Décrivez les symptômes, puis consultez chaque hypothèse et sa traçabilité.</p></div>
    <button class="button button-secondary" @click="showComposer = !showComposer"><AppIcon :name="showComposer ? 'close' : 'plus'" :size="17" />{{ showComposer ? 'Masquer le formulaire' : 'Nouvelle investigation' }}</button>
  </header>

  <SymptomComposer v-if="showComposer" :busy="busy" @submit="emit('run', $event)" />

  <section class="content-card investigation-history">
    <div class="panel-heading"><div><span class="eyebrow">Historique</span><h2>{{ investigations.length }} investigation(s)</h2></div></div>
    <div v-if="investigations.length" class="history-list">
      <article v-for="item in investigations" :key="item.investigation_id" class="history-row">
        <span class="history-icon"><AppIcon name="spark" /></span>
        <div class="history-main"><div><time>{{ formatDate(item.created_at) }}</time><span class="status-pill" :class="item.status">{{ statusLabel(item.status) }}</span></div><p>{{ item.consultation_symptoms }}</p><small>{{ item.model_version }} · {{ item.investigation_id }}</small></div>
        <div class="history-actions">
          <button class="button button-ghost button-small" :disabled="actionBusy === item.investigation_id" @click="emit('rerun', item.investigation_id)"><span v-if="actionBusy === item.investigation_id" class="spinner" /><AppIcon v-else name="refresh" :size="15" />Relancer</button>
          <button class="button button-secondary button-small" @click="emit('open', item.investigation_id)">Voir le rapport<AppIcon name="arrow" :size="15" /></button>
        </div>
      </article>
    </div>
    <div v-else class="empty-state small"><AppIcon name="spark" :size="30" /><h3>Aucune investigation</h3><p>Renseignez les symptômes rapportés pour commencer.</p></div>
  </section>
</template>
