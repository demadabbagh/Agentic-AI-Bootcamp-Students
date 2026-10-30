import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { buildTripView, describePrice, describeTravel, mapSearchUrl } from './itineraryAdapter.js'

describe('activity details', () => {
  it('distinguishes free, paid, unknown and conflicting prices', () => {
    assert.equal(describePrice({ estimated_cost: 0, cost_status: 'free' }, 'EUR'), 'Free')
    assert.equal(describePrice({ estimated_cost: 12 }, 'EUR'), '€12.00')
    assert.equal(describePrice({ estimated_cost: null }, 'EUR'), 'Price unknown')
    assert.equal(describePrice({}, 'EUR'), 'Price unknown')
    assert.match(
      describePrice({ estimated_cost: 20, cost_status: 'range', cost_min: 10, cost_max: 20 }, 'USD'),
      /\$10\.00–\$20\.00 \(sources disagree\)/
    )
  })

  it('links to a map search, never a made-up route', () => {
    const url = mapSearchUrl('Sagrada Família', 'Barcelona')
    assert.equal(url, 'https://www.openstreetmap.org/search?query=Sagrada%20Fam%C3%ADlia%2C%20Barcelona')
  })

  it('shows travel time only when the backend sent one', () => {
    assert.equal(describeTravel(undefined), null)
    assert.equal(describeTravel(null), null)
    assert.equal(describeTravel(15), 'About 15 min from the previous stop')
  })

  it('maps a partial Tokyo itinerary with one activity and an empty day', () => {
    const view = buildTripView({
      destination: 'Tokyo',
      currency: 'JPY',
      travellers: 1,
      days: [
        { date: '2026-11-10', activities: [{ name: 'Senso-ji', kind: 'see', estimated_cost: null }] },
        { date: '2026-11-11', activities: [] },
      ],
    })
    assert.equal(view.destination, 'Tokyo')
    assert.equal(view.budgetTotal, null)
    assert.equal(view.unknownCostCount, 1)
    const [activity] = view.days[0].activities
    assert.equal(activity.type, 'Sight')
    assert.equal(activity.priceUnknown, true)
    assert.equal(activity.time, 'Flexible time')
    assert.equal(activity.travel, null)
    assert.equal(view.days[1].activities.length, 0)
  })
})
