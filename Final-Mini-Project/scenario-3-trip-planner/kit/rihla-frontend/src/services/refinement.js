import { requireSession } from '../auth/authApi.js'

// The AI review of a fast plan (app/refinement.py). The plan on screen is complete and verified; meanwhile the agents
// review it in the background. What they find is an offer: the plan changes only if the traveller applies it, and the
// result then goes through the normal approval policy like any new plan.

export const REVIEW_POLL_MS = 5000
export const REVIEW_MAX_MS = 10 * 60 * 1000 // stop asking after this: the plan on screen stands

// running: keep asking. Every other status is final for this plan.
export const reviewIsRunning = (status) => status === 'running'

// What the panel shows for a status; null shows nothing (no review, or one that no longer concerns this plan).
export function reviewView(review) {
  const status = review?.status
  if (status === 'running') {
    return { kind: 'running', title: 'AI review in progress',
      text: 'This plan is complete and already checked. Rihla\'s AI agents are reviewing it and may suggest a better version. You can approve or save it now.' }
  }
  if (status === 'ready') {
    return { kind: 'ready', title: 'The AI review suggests a refined plan', text: 'Your plan stays as it is unless you choose this version.',
      changes: Array.isArray(review.changes) ? review.changes.map(String).slice(0, 6) : [] }
  }
  if (status === 'no_change') {
    return { kind: 'done', title: 'AI review finished', text: 'The agents found nothing better: your plan stands.' }
  }
  if (status === 'failed') {
    return { kind: 'done', title: 'AI review unavailable', text: 'The review could not finish. Your plan is not affected.' }
  }
  return null
}

async function post(path, body, fetchImpl) {
  let response
  try {
    response = await fetchImpl(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new Error('Could not reach the Rihla backend. Your itinerary was not changed.')
  }
  await requireSession(response, 'Your session ended. Please log in again.')
  let data = null
  try {
    data = await response.json()
  } catch {
    // handled below
  }
  if (!response.ok || !data || typeof data !== 'object') {
    throw new Error('The AI review could not be read. Your itinerary was not changed.')
  }
  return data
}

export function getReview(sessionId, baseVersion, fetchImpl = globalThis.fetch) {
  return post('/api/plan/refinement', { session_id: sessionId, base_version: baseVersion || null }, fetchImpl)
}

export function applyReview(sessionId, baseVersion, fetchImpl = globalThis.fetch) {
  return post('/api/plan/refinement/apply', { session_id: sessionId, base_version: baseVersion || null }, fetchImpl)
}
