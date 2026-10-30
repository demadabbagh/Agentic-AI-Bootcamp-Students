export const FALLBACK_IMAGE =
  'https://images.unsplash.com/photo-1488646953014-85cb44e25828?w=500&q=85'

export function formatDate(value) {
  if (!value) return ''

  const date = new Date(`${value}T12:00:00`)
  if (Number.isNaN(date.getTime())) return value

  return date.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
  })
}

export function formatMoney(value, currency) {
  if (!Number.isFinite(value)) return 'Not specified'

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency || 'USD',
    maximumFractionDigits: 2,
  }).format(value)
}

function formatTime(value) {
  if (!value) return 'Flexible time'

  const [hour, minute] = value.split(':').map(Number)
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return value

  const period = hour >= 12 ? 'PM' : 'AM'
  const displayHour = hour % 12 || 12

  return `${String(displayHour).padStart(2, '0')}:${String(minute).padStart(2, '0')} ${period}`
}

function formatDuration(start, end) {
  if (!start || !end) return 'Flexible duration'

  const [startHour, startMinute] = start.split(':').map(Number)
  const [endHour, endMinute] = end.split(':').map(Number)

  const minutes = (endHour * 60 + endMinute) - (startHour * 60 + startMinute)

  if (!Number.isFinite(minutes) || minutes <= 0) return 'Flexible duration'

  const hours = Math.floor(minutes / 60)
  const remaining = minutes % 60

  if (!hours) return `${remaining} min`
  if (!remaining) return `${hours} ${hours === 1 ? 'hour' : 'hours'}`

  return `${hours}h ${remaining}m`
}

function formatPrice(value, currency) {
  if (!Number.isFinite(value)) return 'Price unknown'
  if (value === 0) return 'Free'

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency || 'USD',
    maximumFractionDigits: 2,
  }).format(value)
}

// Wikivoyage listing kinds (planning/models.py Activity.kind), when the backend sends them.
const KIND_LABELS = {
  see: 'Sight',
  do: 'Activity',
  buy: 'Shopping',
  eat: 'Restaurant',
  drink: 'Café / bar',
}

// The price label, with "free" only when the source says so: a null cost is unknown, never free.
export function describePrice(activity, currency) {
  const { estimated_cost: cost, cost_status: status, cost_min: min, cost_max: max } = activity
  if (status === 'range' && Number.isFinite(min) && Number.isFinite(max)) {
    return `${formatPrice(min, currency)}–${formatPrice(max, currency)} (sources disagree)`
  }
  if (status === 'free') return 'Free'
  return formatPrice(cost, currency)
}

// A search link, not a route: the backend sends no coordinates for activities,
// so the map shows what OpenStreetMap finds for the name in the destination.
export function mapSearchUrl(name, destination) {
  const query = [name, destination].filter(Boolean).join(', ')
  return `https://www.openstreetmap.org/search?query=${encodeURIComponent(query)}`
}

export function describeTravel(minutes) {
  if (!Number.isInteger(minutes)) return null
  if (minutes === 0) return 'Same place as the previous stop'
  return `About ${minutes} min from the previous stop`
}

export function normalizeItinerary(itinerary) {
  if (!itinerary || !Array.isArray(itinerary.days)) return []

  const currency = itinerary.currency || 'USD'

  return itinerary.days.map((day, index) => {
    return {
      label: `Day ${String(index + 1).padStart(2, '0')}`,
      isoDate: day.date,
      date: formatDate(day.date),
      title: `Explore ${itinerary.destination || 'your destination'}`,
      activities: (day.activities || []).map((activity) => ({
        name: activity.name,
        time: formatTime(activity.start_time),
        duration: formatDuration(activity.start_time, activity.end_time),
        durationIsEstimate: activity.duration_is_estimate !== false,
        price: describePrice(activity, currency),
        priceUnknown: !Number.isFinite(activity.estimated_cost) && activity.cost_status !== 'free',
        type: KIND_LABELS[activity.kind] || 'Travel experience',
        image: FALLBACK_IMAGE,
        notes: activity.notes || '',
        sourceUrl: activity.source_url || null,
        mapUrl: mapSearchUrl(activity.name, itinerary.destination),
        travel: describeTravel(activity.travel_minutes_from_previous),
      })),
    }
  })
}

// Everything the Dashboard shows about a real trip, taken from the
// backend itinerary (Trip Store shape) rather than from form input.
export function buildTripView(itinerary) {
  const days = itinerary.days

  return {
    destination: itinerary.destination,
    startDate: days[0]?.date || null,
    endDate: days[days.length - 1]?.date || null,
    travellers: itinerary.travellers ?? null,
    currency: itinerary.currency || 'USD',
    budgetTotal: itinerary.budget_total ?? null,
    estimatedTotal: itinerary.estimated_total ?? null,
    unknownCostCount: days.reduce(
      (count, day) => count + (day.activities || []).filter((a) => !Number.isFinite(a.estimated_cost)).length,
      0
    ),
    days: normalizeItinerary(itinerary),
  }
}
