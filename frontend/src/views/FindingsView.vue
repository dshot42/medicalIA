<script setup>
import AppIcon from '../components/AppIcon.vue'

defineProps({ findings: { type: Array, default: () => [] }, busy: { type: Boolean, default: false } })
defineEmits(['run-analysis'])

function categoryTone(category) {
  if (category?.toLocaleLowerCase().includes('contradiction')) return 'danger'
  if (category?.toLocaleLowerCase().includes('manqu')) return 'warning'
  return 'info'
}
</script>

<template>
  <header class="page-heading">
    <div><span class="eyebrow">Contrôle documentaire</span><h1>Points à clarifier</h1><p>Incohérences, suivis manquants et rapprochements à vérifier humainement.</p></div>
    <button class="button button-secondary" :disabled="busy" @click="$emit('run-analysis')"><span v-if="busy" class="spinner" /><AppIcon v-else name="refresh" :size="17" />{{ busy ? 'Analyse en cours…' : 'Relancer l’analyse' }}</button>
  </header>

  <section v-if="findings.length" class="finding-grid">
    <article v-for="finding in findings" :key="finding.id" class="finding-card" :class="`tone-${categoryTone(finding.category)}`">
      <header><span class="finding-icon"><AppIcon :name="categoryTone(finding.category) === 'danger' ? 'alert' : 'activity'" /></span><span class="status-pill" :class="finding.sources_verified ? 'verified' : 'uncertain'">{{ finding.sources_verified ? 'Sources vérifiées' : 'À vérifier' }}</span></header>
      <span class="eyebrow">{{ finding.category }}</span>
      <h2>{{ finding.description }}</h2>
      <p class="verification-message">{{ finding.verification_message }}</p>
      <div v-if="finding.question_for_professional" class="professional-question"><strong>Question à discuter</strong><p>{{ finding.question_for_professional }}</p></div>
      <details><summary>{{ finding.sources?.length || 0 }} source(s) documentaire(s)</summary><blockquote v-for="source in finding.sources" :key="source.chunk_id"><p>« {{ source.quote }} »</p><cite>{{ source.filename }}<template v-if="source.page"> · page {{ source.page }}</template></cite></blockquote></details>
    </article>
  </section>
  <div v-else class="empty-state"><AppIcon name="shield" :size="38" /><h2>Aucun point documentaire ouvert</h2><p>Relancez l’analyse après l’import de nouveaux documents.</p></div>
</template>
