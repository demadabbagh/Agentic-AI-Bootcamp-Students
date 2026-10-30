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
  return {
    destination: trip.destination.trim(),
    start_date: trip.departureDate,
    end_date: trip.returnDate,
    travellers: Number(trip.travellers),
    budget: Number(trip.budget),
    currency: 'USD',
    interests: [...(trip.interests || [])],
    preferences: trip.customPreferences?.trim() || null,
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

const STATUS_MESSAGES = {
  402: 'The AI provider declined the request (payment required). No trip was generated.',
  503: 'The trip planning service is temporarily unavailable. No trip was generated, please try again later.',
}

// Measured full runs take 75-85 s (sequential LLM stages); stop waiting well
// after that so a hung backend or provider never leaves the page spinning forever.
export const TRIP_REQUEST_TIMEOUT_MS = 5 * 60 * 1000

export async function createTrip(trip, sessionId, timeoutMs = TRIP_REQUEST_TIMEOUT_MS) {
  let response
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    response = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        session_id: sessionId,
        message: buildTripMessage(trip),
        trip: buildTripForm(trip),
      }),
      signal: controller.signal,
    })
  } catch {
    if (controller.signal.aborted) {
      throw new Error(
        `The planner did not answer within ${Math.round(timeoutMs / 60000)} minutes, so no trip is shown. `
        + 'The server may still be working on it; please try again in a moment.'
      )
    }
    throw new Error('Could not reach the Rihla backend. Check that it is running, then try again.')
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status}). No trip was generated.`

    try {
      const error = await response.json()
      if (typeof error.detail === 'string') detail = error.detail
      const invalid = response.status === 422 ? validationMessage(error.detail) : null
      if (invalid) throw new Error(invalid)
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('Please check the trip details')) throw err
      // Keep the HTTP error message.
    }

    throw new Error(STATUS_MESSAGES[response.status] || detail)
  }

  try {
    return await response.json()
  } catch {
    throw new Error('The planner returned an unreadable response. No trip was generated.')
  }
}
