// The backend refuses longer trips (app/schemas.py MAX_TRIP_DAYS = 21): stop them here, before the request,
// instead of after the traveller has waited for a 422.
export const MAX_TRIP_DAYS = 21
export const MAX_TRAVELLERS = 20

function isoToday(now) {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

function parseIsoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return null
  const date = new Date(`${value}T00:00:00Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

// Returns a list of user-facing problems; empty means the form can be sent.
// Checked before the request so the traveller is not kept waiting a minute
// for a planner error about something the form already knew.
export function validateTripForm(trip, now = new Date()) {
  const errors = []
  const destination = String(trip.destination || '').trim()

  if (destination.length < 2) errors.push('Enter a destination (city, and country if the name is common).')
  if (destination.length > 100) errors.push('The destination is too long (100 characters at most).')

  const start = parseIsoDate(trip.departureDate)
  const end = parseIsoDate(trip.returnDate)
  if (!start || !end) {
    errors.push('Choose both a departure and a return date.')
  } else {
    if (trip.departureDate < isoToday(now)) errors.push('The departure date is in the past.')
    if (end < start) errors.push('The return date must be on or after the departure date.')
    const days = Math.round((end - start) / 86400000) + 1
    if (days > MAX_TRIP_DAYS) errors.push(`Trips can be at most ${MAX_TRIP_DAYS} days long.`)
  }

  const travellers = Number(trip.travellers)
  if (!Number.isInteger(travellers) || travellers < 1 || travellers > MAX_TRAVELLERS) {
    errors.push(`Travellers must be a whole number from 1 to ${MAX_TRAVELLERS}.`)
  }

  const budget = Number(trip.budget)
  if (trip.budget === '' || trip.budget === null || trip.budget === undefined || !Number.isFinite(budget) || budget <= 0) {
    errors.push('Enter a budget greater than 0 USD.')
  }

  return errors
}
