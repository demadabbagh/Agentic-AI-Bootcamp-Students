import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { buildTripView } from './itineraryAdapter.js'
import { getVerificationState, isValidTripResult, summarizeBudget, summarizeDensity, validateTripResult } from './tripResult.js'

// The shape of a POST /api/chat response (app/schemas.py ChatResponse).
const AMMAN = {
  destination: 'Amman',
  currency: 'USD',
  travellers: 2,
  budget_total: 2000,
  estimated_total: 22.56,
  days: [
    {
      date: '2026-11-02',
      activities: [
        { name: 'Amman Citadel', start_time: '09:00', end_time: '10:30', estimated_cost: 8.46 },
        { name: 'Hashem', start_time: '13:00', end_time: '14:00', estimated_cost: 8.46 },
      ],
    },
    {
      date: '2026-11-03',
      activities: [
        { name: 'Roman Theater', start_time: '10:00', end_time: '11:00', estimated_cost: 5.64 },
        { name: 'Rainbow Street', start_time: '16:00', end_time: '18:00', estimated_cost: null },
      ],
    },
    { date: '2026-11-04', activities: [] },
  ],
}

const COVERAGE = {
  trip_days: 3, required_days: 2, sufficient: true, food_focused: false,
  covered_dates: ['2026-11-02', '2026-11-03'], meal_only_dates: [], empty_dates: ['2026-11-04'],
  warning: '1 of 3 trip days have no sightseeing or other non-meal activities planned: 2026-11-04.',
}

const verified = {
  session_id: 's',
  status: 'pending_approval',
  itinerary: AMMAN,
  pending_action: { action_id: 'a', type: 'save_trip', verification_status: 'verified' },
  message: 'The itinerary passed real verification and is awaiting user approval.',
  coverage: COVERAGE,
}

describe('validateTripResult', () => {
  it('accepts a verified Amman itinerary with several activities', () => {
    assert.equal(validateTripResult(verified), verified)
  })

  it('accepts an unverified itinerary (shown, never as verified)', () => {
    const result = { ...verified, status: 'verification_required', pending_action: null }
    assert.equal(isValidTripResult(result), true)
  })

  it('rejects needs_revision for low coverage, with the backend message', () => {
    const result = {
      status: 'needs_revision',
      itinerary: null,
      message: 'Only 0 of 8 trip days have sightseeing or other non-meal activities.',
      coverage: { ...COVERAGE, sufficient: false },
    }
    assert.throws(() => validateTripResult(result), /Only 0 of 8 trip days/)
  })

  it('rejects an itinerary whose coverage is insufficient even with an itinerary status', () => {
    const result = { ...verified, status: 'verification_required', coverage: { sufficient: false } }
    assert.equal(isValidTripResult(result), false)
  })

  it('rejects a status with no itinerary, using the backend message', () => {
    assert.throws(
      () => validateTripResult({ status: 'research_unavailable', itinerary: null, message: 'Research is down.' }),
      /Research is down/
    )
  })

  it('rejects incomplete itineraries', () => {
    const cases = [
      { ...AMMAN, destination: '' },
      { ...AMMAN, days: [] },
      { ...AMMAN, days: [{ activities: [] }] },
      { ...AMMAN, days: [{ date: '2026-11-02' }] },
    ]
    for (const itinerary of cases) {
      assert.throws(() => validateTripResult({ ...verified, itinerary }), /incomplete itinerary/)
    }
    assert.throws(() => validateTripResult(null), /empty response/)
  })

  it('rejects an unexpected status even with an itinerary', () => {
    assert.equal(isValidTripResult({ ...verified, status: 'verified' }), false)
  })
})

describe('getVerificationState', () => {
  it('is verified only with the backend save_trip pending action', () => {
    assert.equal(getVerificationState(verified).level, 'verified')
  })

  it('is not verified merely because the itinerary has days', () => {
    assert.equal(getVerificationState({ ...verified, pending_action: null }).level, 'unverified')
    assert.equal(
      getVerificationState({ ...verified, pending_action: { type: 'save_trip', verification_status: 'unverified' } }).level,
      'unverified'
    )
    assert.equal(getVerificationState({ ...verified, status: 'verification_required' }).level, 'unverified')
  })

  it('shows the backend message, including coverage warnings', () => {
    const result = { ...verified, status: 'verification_required', message: 'Not verified. 1 of 3 trip days...' }
    assert.match(getVerificationState(result).detail, /1 of 3 trip days/)
  })
})

describe('budget with unknown costs', () => {
  it('counts unknown prices and never treats them as zero', () => {
    const view = buildTripView(AMMAN)
    assert.equal(view.unknownCostCount, 1)
    assert.equal(view.days[1].activities[1].price, 'Price unknown')
  })

  it('gives only an upper bound for the remaining budget while prices are unknown', () => {
    const view = buildTripView(AMMAN)
    const budget = summarizeBudget(view.budgetTotal, view.estimatedTotal, view.unknownCostCount)
    assert.equal(budget.costComplete, false)
    assert.equal(budget.remaining, null)
    assert.equal(budget.maxRemaining, 2000 - 22.56)
  })

  it('gives the remaining budget when every price is known', () => {
    const budget = summarizeBudget(2000, 22.56, 0)
    assert.equal(budget.remaining, 2000 - 22.56)
    assert.equal(budget.maxRemaining, null)
  })

  it('reports over budget from known costs alone, whatever is unknown', () => {
    const budget = summarizeBudget(100, 150, 2)
    assert.equal(budget.overBudget, true)
    assert.equal(budget.overrun, 50)
    assert.equal(budget.remaining, null)
    assert.equal(budget.maxRemaining, null)
  })

  it('cannot confirm anything without a budget or an estimate', () => {
    const budget = summarizeBudget(null, null, 0)
    assert.equal(budget.remaining, null)
    assert.equal(budget.maxRemaining, null)
    assert.equal(budget.percentUsed, null)
  })
})

describe('summarizeDensity', () => {
  it('lists the days below their target for a light plan', () => {
    const density = summarizeDensity({
      level: 'light', total_non_meal: 5, target_min: 8, target_max: 13,
      days: [
        { date: '2026-11-02', non_meal: 1, target: 1 },
        { date: '2026-11-03', non_meal: 1, target: 2 },
        { date: '2026-11-04', non_meal: 1, target: 2 },
      ],
    })
    assert.equal(density.light, true)
    assert.equal(density.total, 5)
    assert.deepEqual(density.lightDays.map((d) => d.date), ['2026-11-03', '2026-11-04'])
  })

  it('is not light when every day meets its target', () => {
    const density = summarizeDensity({ level: 'full', total_non_meal: 2, days: [{ date: '2026-11-02', non_meal: 2, target: 2 }] })
    assert.equal(density.light, false)
    assert.deepEqual(density.lightDays, [])
  })

  it('is absent when the backend sent no plan quality', () => {
    assert.equal(summarizeDensity(null), null)
    assert.equal(summarizeDensity({ level: 'light' }), null)
  })
})

describe('buildTripView', () => {
  it('takes every field from the backend itinerary', () => {
    const view = buildTripView(AMMAN)
    assert.equal(view.destination, 'Amman')
    assert.equal(view.startDate, '2026-11-02')
    assert.equal(view.endDate, '2026-11-04')
    assert.equal(view.travellers, 2)
    assert.equal(view.budgetTotal, 2000)
    assert.equal(view.estimatedTotal, 22.56)
    assert.deepEqual(view.days.map((d) => d.activities.length), [2, 2, 0])
    assert.equal(view.days[0].activities[0].name, 'Amman Citadel')
    assert.equal(view.days[0].date, 'November 2')
  })
})
