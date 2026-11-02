const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function api(path, options = {}) {
  const { query, body, headers, ...requestOptions } = options
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, String(value))
    }
  })

  const finalHeaders = new Headers(headers || {})
  let finalBody = body
  if (body && !(body instanceof FormData) && typeof body !== 'string') {
    finalHeaders.set('Content-Type', 'application/json')
    finalBody = JSON.stringify(body)
  }

  let response
  try {
    response = await fetch(url, {
      cache: 'no-store',
      ...requestOptions,
      headers: finalHeaders,
      body: finalBody,
    })
  } catch (error) {
    throw new ApiError(
      "L’API MedConnect est inaccessible. Vérifiez que le serveur local est démarré.",
    )
  }

  if (!response.ok) {
    let detail = `Erreur API ${response.status}`
    try {
      const payload = await response.json()
      detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)
    } catch {
      detail = (await response.text()) || detail
    }
    throw new ApiError(detail, response.status)
  }

  if (response.status === 204) return null
  return response.json()
}

export const medconnectApi = {
  health: () => api('/health'),
  patients: (q = '') => api('/api/patients', { query: { q } }),
  createPatient: (patientId) =>
    api('/api/patients', { method: 'POST', body: { patient_id: patientId } }),
  dashboard: (patientId) => api('/api/dashboard', { query: { patient_id: patientId } }),
  documents: (patientId) => api('/api/documents', { query: { patient_id: patientId } }),
  document: (documentId, patientId) =>
    api(`/api/documents/${documentId}`, { query: { patient_id: patientId } }),
  uploadDocuments: (patientId, files) => {
    const form = new FormData()
    form.set('patient_id', patientId)
    files.forEach((file) => form.append('files', file))
    return api('/api/documents', { method: 'POST', body: form })
  },
  deleteDocument: (documentId) => api(`/api/documents/${documentId}`, { method: 'DELETE' }),
  timeline: (patientId) => api('/api/timeline', { query: { patient_id: patientId } }),
  eventSources: (eventId, patientId) =>
    api(`/api/timeline/events/${eventId}/sources`, { query: { patient_id: patientId } }),
  findings: (patientId) =>
    api('/api/analysis/findings', { query: { patient_id: patientId } }),
  runAnalysis: (patientId) =>
    api('/api/analysis/run', { method: 'POST', body: { patient_id: patientId } }),
  investigations: (patientId) =>
    api('/api/investigations', { query: { patient_id: patientId } }),
  investigation: (investigationId, patientId) =>
    api(`/api/investigations/${investigationId}`, { query: { patient_id: patientId } }),
  runInvestigation: (patientId, consultationSymptoms) =>
    api('/api/investigations', {
      method: 'POST',
      body: { patient_id: patientId, consultation_symptoms: consultationSymptoms },
    }),
  rerunInvestigation: (investigationId, patientId) =>
    api(`/api/investigations/${investigationId}/rerun`, {
      method: 'POST',
      query: { patient_id: patientId },
    }),
  hypothesisEvidence: (investigationId, hypothesisId, patientId) =>
    api(`/api/investigations/${investigationId}/hypotheses/${hypothesisId}/evidence`, {
      query: { patient_id: patientId },
    }),
}
