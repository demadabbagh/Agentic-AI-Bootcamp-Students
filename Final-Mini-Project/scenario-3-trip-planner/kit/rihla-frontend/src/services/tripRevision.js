import { validateTripResult } from './tripResult.js'

const MAX_CHANGES_SHOWN = 12
const MAX_UNDERSTOOD_SHOWN = 6

// What the assistant took the request to mean (Python's words for the operations it applied or tried).
function understoodOf(revision) {
  return (Array.isArray(revision.understood) ? revision.understood : []).map(String).filter(Boolean)
    .slice(0, MAX_UNDERSTOOD_SHOWN)
}

// Why the "Ask for a change" box cannot change this trip, or null when it can. A change is applied by the
// backend to the plan it keeps for this session (app/revision.py), named by plan_version: without one there
// is nothing the backend could revise, and an approved trip is already saved as it is.
export function revisionBlockedReason(result, { sample = false, approved = false, saved = false } = {}) {
  if (sample) return 'Sample mode: the assistant is not connected. Plan a trip to ask for changes.'
  if (saved || ['saved_limited', 'saved_draft'].includes(result?.status)) { // accepted with its limitations
    return 'This trip is saved in My Trips as it is. To change it, plan the trip again from the home page.'
  }
  if (approved || result?.status === 'approved') {
    return 'This trip is approved and saved as it is. To change it, plan the trip again from the home page.'
  }
  if (!result?.plan_version || !result?.session_id) {
    return 'This itinerary was opened from your saved trips, so the planner no longer has it in memory. '
      + 'Plan the trip again to ask for changes.'
  }
  return null
}

// The dashboard's next state after POST /api/chat answered a change request. Only a revised, displayable
// itinerary replaces the one on screen; a question, a refusal or a stale plan keep it (result: null).
export function applyRevision(current, response) {
  const revision = response?.revision && typeof response.revision === 'object' ? response.revision : {}
  const message = String(response?.message || '')

  if (revision.outcome !== 'revised') {
    return {
      result: null,
      outcome: revision.outcome || response?.status || 'unknown',
      reply: message || 'The itinerary was not changed.',
      changes: [],
      understood: understoodOf(revision),
    }
  }

  validateTripResult(response) // throws, and the caller keeps the previous itinerary
  const result = { ...response, session_id: response.session_id || current?.session_id }
  delete result.approval // a new version is never the approved one
  const changes = (Array.isArray(revision.changes) ? revision.changes : []).map(String).filter(Boolean)
  return {
    result,
    outcome: 'revised',
    reply: message || 'Your itinerary has been updated.',
    changes: changes.slice(0, MAX_CHANGES_SHOWN),
    moreChanges: Math.max(0, changes.length - MAX_CHANGES_SHOWN),
    understood: understoodOf(revision),
  }
}
