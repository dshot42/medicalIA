<script setup>
import { computed, reactive, ref } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  busy: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

const symptomChoices = [
  'Douleur abdominale',
  'Diarrhée',
  'Constipation',
  'Nausées',
  'Vomissements',
  'Fatigue',
  'Perte de poids',
  'Fièvre',
  'Vertiges',
  'Céphalées',
  'Palpitations',
  'Essoufflement',
]

const form = reactive({
  symptoms: [],
  onset: '',
  duration: '',
  intensity: '',
  evolution: '',
  triggers: '',
  associated: '',
  absent: '',
  notes: '',
})
const customSymptom = ref('')
const expanded = ref(!props.compact)
const attempted = ref(false)

function toggleSymptom(symptom) {
  const index = form.symptoms.indexOf(symptom)
  if (index >= 0) form.symptoms.splice(index, 1)
  else form.symptoms.push(symptom)
}

function addCustomSymptom() {
  const value = customSymptom.value.trim()
  if (!value) return
  if (!form.symptoms.some((item) => item.toLocaleLowerCase() === value.toLocaleLowerCase())) {
    form.symptoms.push(value)
  }
  customSymptom.value = ''
}

const consultationText = computed(() => {
  const parts = []
  if (form.symptoms.length) parts.push(`Symptômes rapportés : ${form.symptoms.join(', ')}.`)
  if (form.onset) parts.push(`Début : ${form.onset}.`)
  if (form.duration) parts.push(`Durée ou fréquence : ${form.duration}.`)
  if (form.intensity) parts.push(`Intensité ressentie : ${form.intensity}.`)
  if (form.evolution) parts.push(`Évolution : ${form.evolution}.`)
  if (form.triggers) parts.push(`Facteurs déclenchants ou soulageants rapportés : ${form.triggers}.`)
  if (form.associated) parts.push(`Autres signes associés : ${form.associated}.`)
  if (form.absent) parts.push(`Signes explicitement absents : ${form.absent}.`)
  if (form.notes) parts.push(`Contexte complémentaire rapporté : ${form.notes}.`)
  return parts.join(' ')
})

const valid = computed(() => form.symptoms.length > 0 && consultationText.value.length >= 10)

function submit() {
  attempted.value = true
  if (!valid.value || props.busy) return
  emit('submit', consultationText.value)
}
</script>

<template>
  <section class="symptom-composer" :class="{ compact }">
    <div class="composer-heading">
      <div>
        <span class="eyebrow">Nouvelle investigation</span>
        <h2>Quels symptômes sont rapportés&nbsp;?</h2>
        <p>
          Saisissez uniquement les signes observés et leur contexte. Le moteur confrontera ensuite
          ces éléments au dossier documentaire.
        </p>
      </div>
      <button v-if="compact" class="text-button" type="button" @click="expanded = !expanded">
        {{ expanded ? 'Réduire' : 'Préciser' }}
      </button>
    </div>

    <div class="symptom-grid" role="group" aria-label="Symptômes fréquents">
      <button
        v-for="symptom in symptomChoices"
        :key="symptom"
        type="button"
        class="symptom-chip"
        :class="{ selected: form.symptoms.includes(symptom) }"
        :aria-pressed="form.symptoms.includes(symptom)"
        @click="toggleSymptom(symptom)"
      >
        <AppIcon v-if="form.symptoms.includes(symptom)" name="check" :size="14" />
        {{ symptom }}
      </button>
    </div>

    <form class="custom-symptom" @submit.prevent="addCustomSymptom">
      <label class="sr-only" for="custom-symptom">Ajouter un autre symptôme</label>
      <input
        id="custom-symptom"
        v-model="customSymptom"
        type="text"
        placeholder="Autre symptôme…"
        autocomplete="off"
      />
      <button class="button button-secondary button-small" type="submit">
        <AppIcon name="plus" :size="16" /> Ajouter
      </button>
    </form>

    <div v-if="form.symptoms.length" class="selected-symptoms" aria-live="polite">
      <span v-for="symptom in form.symptoms" :key="symptom" class="selected-tag">
        {{ symptom }}
        <button type="button" :aria-label="`Retirer ${symptom}`" @click="toggleSymptom(symptom)">
          <AppIcon name="close" :size="12" />
        </button>
      </span>
    </div>

    <div v-show="expanded" class="clinical-fields">
      <label>
        <span>Depuis quand&nbsp;?</span>
        <input v-model="form.onset" type="text" placeholder="Ex. depuis 3 semaines, début brutal hier" />
      </label>
      <label>
        <span>Durée ou fréquence</span>
        <input v-model="form.duration" type="text" placeholder="Ex. épisodes de 2 h, 3 fois par semaine" />
      </label>
      <label>
        <span>Intensité ressentie</span>
        <select v-model="form.intensity">
          <option value="">Non précisée</option>
          <option>Faible</option>
          <option>Modérée</option>
          <option>Forte</option>
          <option>Très forte</option>
        </select>
      </label>
      <label>
        <span>Évolution</span>
        <select v-model="form.evolution">
          <option value="">Non précisée</option>
          <option>Stable</option>
          <option>S’aggrave</option>
          <option>S’améliore</option>
          <option>Intermittente / par épisodes</option>
        </select>
      </label>
      <label class="field-wide">
        <span>Déclencheurs ou facteurs soulageants</span>
        <input v-model="form.triggers" type="text" placeholder="Ex. après les repas, à l’effort, soulagé au repos" />
      </label>
      <label class="field-wide">
        <span>Autres signes associés</span>
        <input v-model="form.associated" type="text" placeholder="Signes survenant au même moment" />
      </label>
      <label class="field-wide">
        <span>Signes explicitement absents</span>
        <input
          v-model="form.absent"
          type="text"
          placeholder="Uniquement ce qui a été vérifié et nié, ex. pas de fièvre"
        />
        <small>Une absence de mention dans le dossier n’est jamais considérée comme un signe absent.</small>
      </label>
      <label class="field-wide">
        <span>Contexte libre</span>
        <textarea
          v-model="form.notes"
          rows="3"
          placeholder="Localisation, circonstances, répétition, traitements déjà essayés…"
        />
      </label>
    </div>

    <div v-if="consultationText" class="consultation-preview">
      <span>Aperçu transmis à l’investigation</span>
      <p>{{ consultationText }}</p>
    </div>
    <p v-if="attempted && !valid" class="field-error" role="alert">
      Sélectionnez ou ajoutez au moins un symptôme avant de lancer l’investigation.
    </p>

    <div class="composer-footer">
      <p><AppIcon name="shield" :size="16" /> Analyse locale, documentaire et non diagnostique.</p>
      <button class="button button-primary" type="button" :disabled="busy" @click="submit">
        <span v-if="busy" class="spinner" aria-hidden="true" />
        <AppIcon v-else name="spark" :size="18" />
        {{ busy ? 'Investigation en cours…' : 'Lancer l’investigation' }}
      </button>
    </div>
  </section>
</template>
