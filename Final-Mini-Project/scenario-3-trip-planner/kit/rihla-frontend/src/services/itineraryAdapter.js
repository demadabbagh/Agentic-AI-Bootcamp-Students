const FALLBACK_IMAGE =
  'https://images.unsplash.com/photo-1488646953014-85cb44e25828?w=500&q=85'

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
  if (value === null || value === undefined) return 'Price unknown'
  if (value === 0) return 'Free'

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency || 'USD',
    maximumFractionDigits: 2,
  }).format(value)
}

export function normalizeItinerary(itinerary) {
  if (!itinerary || !Array.isArray(itinerary.days)) return []

  const currency = itinerary.currency || 'USD'

  return itinerary.days.map((day, index) => {
    const date = new Date(`${day.date}T12:00:00`)

    const formattedDate = Number.isNaN(date.getTime())
      ? day.date
      : date.toLocaleDateString('en-US', {
          month: 'long',
          day: 'numeric',
        })

    return {
      label: `Day ${String(index + 1).padStart(2, '0')}`,
      date: formattedDate,
      title: `Explore ${itinerary.destination || 'your destination'}`,
      activities: (day.activities || []).map((activity) => ({
        name: activity.name,
        time: formatTime(activity.start_time),
        duration: formatDuration(activity.start_time, activity.end_time),
        price: formatPrice(activity.estimated_cost, currency),
        type: 'Travel experience',
        image: FALLBACK_IMAGE,
        notes: activity.notes || '',
        sourceUrl: activity.source_url || null,
      })),
    }
  })
}
