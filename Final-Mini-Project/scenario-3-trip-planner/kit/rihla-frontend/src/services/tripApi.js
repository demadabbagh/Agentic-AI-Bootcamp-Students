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

export async function createTrip(trip, sessionId) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      session_id: sessionId,
      message: buildTripMessage(trip),
    }),
  })

  if (!response.ok) {
    let detail = `Request failed (${response.status})`

    try {
      const error = await response.json()
      detail = typeof error.detail === 'string'
        ? error.detail
        : JSON.stringify(error.detail ?? error)
    } catch {
      // Keep the HTTP error message.
    }

    throw new Error(detail)
  }

  return response.json()
}
