<script setup>
import { computed, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'

const props = defineProps({ events: { type: Array, default: () => [] }, sources: { type: Object, default: () => ({}) }, sourceBusy: { type: String, default: '' } })
const emit = defineEmits(['sources'])
const selectedTypes = ref([])
const search = ref('')

const types = computed(() => [...new Set(props.events.map((item) => item.event_type))])
const filtered = computed(() => props.events.filter((event) => {
  const typeMatch = !selectedTypes.value.length || selectedTypes.value.includes(event.event_type)
  const text = `${event.description} ${event.event_type}`.toLocaleLowerCase()
  return typeMatch && text.includes(search.value.toLocaleLowerCase())
}))

function toggleType(type) {
  const index = selectedTypes.value.indexOf(type)
  if (index >= 0) selectedTypes.value.splice(index, 1)
  else selectedTypes.value.push(type)
}

function formatDate(value) {
  if (!value) return 'Date non renseignée'
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'long' }).format(new Date(value))
}
</script>

<template>
  <header class="page-heading"><div><span class="eyebrow">Parcours longitudinal</span><h1>Chronologie médicale</h1><p>{{ events.length }} événement(s) extrait(s), relié(s) à leurs sources.</p></div></header>
  <section class="filter-bar">
    <label class="search-field"><AppIcon name="search" :size="17" /><input v-model="search" placeholder="Rechercher dans la chronologie" /></label>
    <div class="filter-chips">
      <button v-for="type in types" :key="type" :class="{ active: selectedTypes.includes(type) }" @click="toggleType(type)">{{ type }}</button>
    </div>
  </section>

  <section v-if="filtered.length" class="timeline">
    <article v-for="event in filtered" :key="event.id" class="timeline-event">
      <div class="timeline-rail"><span /><i /></div>
      <div class="timeline-card">
        <div class="timeline-meta"><time>{{ formatDate(event.event_date) }}</time><span class="status-pill" :class="event.status === 'vérifiée' ? 'verified' : 'uncertain'">{{ event.status }}</span></div>
        <h2>{{ event.event_type }}</h2>
        <p>{{ event.description }}</p>
        <button class="text-button" :disabled="sourceBusy === event.id" @click="emit('sources', event.id)">
          <span v-if="sourceBusy === event.id" class="spinner" />
          <AppIcon v-else name="eye" :size="16" /> {{ sources[event.id] ? 'Masquer les sources' : 'Voir la source' }}
        </button>
        <div v-if="sources[event.id]" class="source-quotes">
          <blockquote v-for="source in sources[event.id]" :key="source.chunk_id">
            <p>« {{ source.quote }} »</p><cite>{{ source.filename }}<template v-if="source.page"> · page {{ source.page }}</template></cite>
          </blockquote>
        </div>
      </div>
    </article>
  </section>
  <div v-else class="empty-state"><AppIcon name="clock" :size="36" /><h2>Aucun événement à afficher</h2><p>Lancez l’analyse documentaire ou modifiez les filtres.</p></div>
</template>
