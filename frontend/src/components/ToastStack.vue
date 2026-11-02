<script setup>
import AppIcon from './AppIcon.vue'

defineProps({ toasts: { type: Array, default: () => [] } })
defineEmits(['dismiss'])
</script>

<template>
  <div class="toast-stack" aria-live="polite" aria-atomic="true">
    <div v-for="toast in toasts" :key="toast.id" class="toast" :class="`toast-${toast.type}`">
      <AppIcon :name="toast.type === 'error' ? 'alert' : 'check'" :size="18" />
      <div>
        <strong>{{ toast.title }}</strong>
        <p v-if="toast.message">{{ toast.message }}</p>
      </div>
      <button type="button" aria-label="Fermer" @click="$emit('dismiss', toast.id)">
        <AppIcon name="close" :size="16" />
      </button>
    </div>
  </div>
</template>
