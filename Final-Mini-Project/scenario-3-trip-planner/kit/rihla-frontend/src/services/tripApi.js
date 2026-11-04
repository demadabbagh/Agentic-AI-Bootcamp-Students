import { requireSession } from '../auth/authApi.js'
import { sendApproval } from './approval.js'
import { apiPlace } from '../features/trip-travel/tripTravelState.js'

function buildTripMessage(trip) {
  const details = [
    `Plan a trip to ${trip.destination}.`,
    `Departure date: ${trip.departureDate}.`,
    `Return date: ${trip.returnDate}.`,
    `Number of travellers: ${trip.travellers}.`,
    `Total budget: ${trip.budget} USD.`,
  ]

  if (trip.interests?.length) {
    details.push(`Travel interests: ${trip.interests.join(', ')}.`)
  }

  if (trip.customPreferences?.trim()) {
    details.push(`Additional preferences and requests: ${trip.customPreferences.trim()}`)
  }

  return details.join('\n')
}

// The form's own fields, sent as they are: the backend uses them exactly (the
// selected interests are never re-read from the message by an LLM).
export function buildTripForm(trip) {
  // The city chosen in the destination box (GeoNames id, country code, coordinates), when there is one: the server
  // checks it against the text and researches that very place, not a namesake (app/destinations/identity.py).
  const place = apiPlace(trip.destinationPlace)
  return {
    destination: trip.destination.trim(),
    start_date: trip.departureDate,
    end_date: trip.returnDate,
    travellers: Number(trip.travellers),
    budget: Number(trip.budget),
    currency: 'USD',
    interests: [...(trip.interests || [])],
    preferences: trip.customPreferences?.trim() || null,
    ...(place ? { place } : {}),
  }
}

// FastAPI's 422 detail is a list of {loc, msg}: show the messages, not "Request failed".
function validationMessage(detail) {
  if (!Array.isArray(detail)) return null
  const messages = detail
    .map((item) => String(item?.msg || '').replace(/^Value error, /, ''))
    .filter(Boolean)
  return messages.length ? `Please check the trip details: ${messages.join(' ')}` : null
}

// What did not happen, said in every error: a new trip was not generated, or the current one was not changed.
const NEW_TRIP = { outcome: 'No trip was generated.', timedOut: 'so no trip is shown', login: 'Please log in to plan your trip.' }
const REVISION = {
  outcome: 'Your itinerary was not changed.',
  timedOut: 'so your itinerary was not changed',
  login: 'Please log in again to change your trip.',
}

// Measured full runs take 75-85 s (sequential LLM stages); stop waiting well
// after that so a hung backend or provider never leaves the page spinning forever.
export const TRIP_REQUEST_TIMEOUT_MS = 5 * 60 * 1000
// Longer trips take longer (more places to research, plan and verify): the limit grows with the trip's days.
export const LONG_TRIP_DAYS = 7

// The traveller picked a destination: the backend starts reading its guide while the form is filled in. Only a head
// start: the answer is not used and a failure changes nothing, so it never reaches the page.
export function prepareDestination(destination, fetchImpl = globalThis.fetch) {
  return Promise.resolve()
    .then(() => fetchImpl('/api/plan/prepare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destination: String(destination || '').slice(0, 100) }),
    }))
    .then(() => undefined, () => undefined)
}
const EXTRA_MS_PER_DAY = 20 * 1000
const MAX_TRIP_REQUEST_TIMEOUT_MS = 20 * 60 * 1000

export function tripDays(trip) {
  const start = Date.parse(`${trip?.departureDate}T00:00:00Z`)
  const end = Date.parse(`${trip?.returnDate}T00:00:00Z`)
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? Math.round((end - start) / 86400000) + 1 : 1
}

export function tripRequestTimeoutMs(trip) {
  const extra = Math.max(0, tripDays(trip) - LONG_TRIP_DAYS) * EXTRA_MS_PER_DAY
  return Math.min(MAX_TRIP_REQUEST_TIMEOUT_MS, TRIP_REQUEST_TIMEOUT_MS + extra)
}

// POST /api/chat with a time limit. Throws AuthRequiredError for a 401 (the page asks to log in and retries),
// otherwise an Error whose message is safe to show and says what did not happen.
async function postChat(body, timeoutMs, { outcome, timedOut, login }) {
  let response
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    response = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
  } catch {
    if (controller.signal.aborted) {
      throw new Error(
        `The planner did not answer within ${Math.round(timeoutMs / 60000)} minutes, ${timedOut}. `
        + 'The server may still be working on it; please try again in a moment.'
      )
    }
    throw new Error('Could not reach the Rihla backend. Check that it is running, then try again.')
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    await requireSession(response, login)
    let detail = `Request failed (${response.status}).`

    // The backend's own diagnosis (app/diagnostics.py: which failure, and the error id in its log) is more
    // useful than a generic message for its status.
    let diagnosed = null

    try {
      const error = await response.json()
      if (typeof error.detail === 'string') detail = error.detail
      if (typeof error.error?.message === 'string' && error.error.message && typeof error.detail === 'string') {
        diagnosed = error.detail
      }
      const invalid = response.status === 422 ? validationMessage(error.detail) : null
      if (invalid) throw new Error(invalid)
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('Please check the trip details')) throw err
      // Keep the HTTP error message.
    }

    if (diagnosed) throw new Error(`${diagnosed} ${outcome}`)
    if (response.status === 402) throw new Error(`The AI provider declined the request (payment required). ${outcome}`)
    if (response.status === 503) {
      throw new Error(`The trip planning service is temporarily unavailable. ${outcome} Please try again later.`)
    }
    throw new Error(`${detail} ${outcome}`)
  }

  try {
    return await response.json()
  } catch {
    throw new Error(`The planner returned an unreadable response. ${outcome}`)
  }
}

export async function createTrip(trip, sessionId, timeoutMs = tripRequestTimeoutMs(trip)) {
  return postChat(
    { session_id: sessionId, message: buildTripMessage(trip), trip: buildTripForm(trip) },
    timeoutMs,
    NEW_TRIP,
  )
}

// POST /api/chat with a change to the itinerary on screen, in the same session: the backend revises the session's
// plan (app/revision.py), it never plans an unrelated trip. `baseVersion` is the plan_version on screen: the backend
// refuses a change made on an older version. Resolves with the backend's answer whatever it is (a revised
// itinerary, a question, a refusal); throws only when there is no answer, and then nothing was changed.
export async function reviseTrip(sessionId, message, baseVersion = null, timeoutMs = TRIP_REQUEST_TIMEOUT_MS) {
  const text = String(message || '').trim()
  if (!sessionId) {
    throw new Error('This trip has no planning session on the server, so it cannot be changed. Plan it again from the home page.')
  }
  if (!text) throw new Error('Type the change you would like to make.')
  const body = await postChat(
    { session_id: sessionId, message: text, revision: true, base_version: baseVersion || null },
    timeoutMs,
    REVISION,
  )
  if (!body || typeof body !== 'object') {
    throw new Error('The planner returned an unreadable response. Your itinerary was not changed.')
  }
  return body
}

async function readError(response, fallback) {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string' && body.detail) return body.detail
  } catch {
    // keep the fallback
  }
  return fallback
}

// POST /api/approve for the save_trip action of a plan the approval policy made eligible
// (app/approval_policy.py). Resolves only after the backend confirmed the save with a trip ID; a repeat of the
// same approval returns the trip already saved, never a second one. The call itself is services/approval.js.
export async function approveTrip(sessionId, actionId) {
  const out = await sendApproval({ sessionId, actionId, approved: true })
  if (out.status !== 'approved' || !out.tripId) {
    throw new Error('The backend did not confirm the save. The trip was not marked as approved.')
  }
  return { trip: { ...(out.trip || {}), trip_id: out.tripId }, alreadyApproved: out.alreadyApproved, message: out.message }
}

// POST /api/approve with approved: false. Nothing is written; resolves with the backend's reply.
export async function rejectTrip(sessionId, actionId) {
  const out = await sendApproval({ sessionId, actionId, approved: false })
  if (out.status !== 'rejected') throw new Error('The backend did not confirm the rejection.')
  return { message: out.message }
}

async function getJson(url, what) {
  let response
  try {
    response = await fetch(url)
  } catch {
    throw new Error(`Could not reach the Rihla backend to load ${what}.`)
  }
  await requireSession(response, 'Your session ended. Please log in again to see your trips.')
  if (!response.ok) {
    throw new Error(await readError(response, `Loading ${what} failed (${response.status}).`))
  }
  try {
    return await response.json()
  } catch {
    throw new Error(`The backend returned unreadable ${what}.`)
  }
}

// GET /api/trips: the approved trips saved in the Rihla Trip Store for the signed-in user (summaries).
export async function listApprovedTrips() {
  const body = await getJson('/api/trips', 'your saved trips')
  return Array.isArray(body?.trips) ? body.trips : []
}

// GET /api/trips/{id}: one approved trip with its full itinerary (404 when it is not the signed-in user's).
export async function getApprovedTrip(tripId) {
  return getJson(`/api/trips/${encodeURIComponent(tripId)}`, 'the saved trip')
}
