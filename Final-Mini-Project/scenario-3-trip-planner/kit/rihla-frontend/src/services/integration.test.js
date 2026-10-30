import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { afterEach, describe, it } from 'node:test'

import { sendApproval } from './approval.js'
import { buildTripView, describeTravel, formatMoney } from './itineraryAdapter.js'
import { reviseTrip } from './tripApi.js'
import { pdfInputFromResult, resultWarnings } from './tripExport.js'
import { validateTripForm } from './tripForm.js'
import { buildTripPdf } from './tripPdf.js'
import { budgetNotes, getVerificationState, interpretRevision, summarizeVerification } from './tripResult.js'
import { RECORDED_AMMAN_RESULT } from '../data/recordedAmmanResult.js'

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

const pdfString = (doc) => Buffer.from(doc.output('arraybuffer')).toString('latin1')

// A /api/chat result in today's backend shape (app/schemas.py ChatResponse), with the summary,
// verification and budget blocks app/orchestrator.py adds.
const RESULT = {
  session_id: 'sess-9',
  status: 'verification_required',
  pending_action: null,
  message: 'The itinerary could not be fully verified.',
  itinerary: {
    destination: 'Istanbul',
    currency: 'USD',
    travellers: 1,
    budget_total: 700,
    estimated_total: 40,
    days: [{
      date: '2026-11-02',
      activities: [
        { name: 'Hagia Sophia', start_time: '09:00', end_time: '10:30', estimated_cost: 28 },
        { name: 'Basilica Cistern', start_time: '10:45', end_time: '11:45', estimated_cost: 12 },
        { name: 'Galata Tower', start_time: '13:00', end_time: '14:00', estimated_cost: null },
        { name: 'Pera Museum', start_time: '15:00', end_time: '16:00', estimated_cost: 0 },
      ],
    }],
  },
  summary: {
    limitations: ['No usable price in the sources for: Galata Tower. Their cost is unknown, not zero.'],
    days: [{
      date: '2026-11-02',
      activities: [
        { name: 'Hagia Sophia', kind: 'see', travel_minutes: null, travel_mode: null },
        { name: 'Basilica Cistern', kind: 'see', travel_minutes: 4, travel_mode: 'foot' },
        { name: 'Galata Tower', kind: 'see', travel_minutes: 12, travel_mode: 'driving' },
        { name: 'Pera Museum', kind: 'see', travel_minutes: 41, travel_mode: 'foot' },
      ],
    }],
  },
  verification: {
    status: 'UNKNOWN', verified: false, state: 'unverifiable', evidence_basis: 'recorded',
    findings: [
      { code: 'hours_unknown', state: 'unverifiable', check: 'opening_hours', date: '2026-11-02',
        explanation: 'Galata Tower: no opening hours in the listing.', recommended_action: 'Check before you go.' },
      { code: 'closed_day', state: 'confirmed_problem', check: 'opening_hours', date: '2026-11-02',
        explanation: 'Pera Museum is closed on Mondays.', recommended_action: null },
      { code: 'price_range', state: 'advice', check: 'budget', date: null, explanation: 'A price is a range.' },
    ],
    revision: { decided_by: 'python', attempts: 2 },
  },
  budget: {
    status: 'undetermined', confidence: 'medium',
    confidence_basis: '3 of 4 planned places have a price from the sources.',
    warnings: [
      'Galata Tower: sources disagree, 13.07-26.14 USD for the party; 26.14 USD budgeted.',
      'These totals cover only the entry fees and meals priced in this plan.',
    ],
    suggestions: ['Check the prices of Galata Tower before you go: the sources give none.'],
  },
}

describe('trip form limits match the backend', () => {
  const ok = { destination: 'Amman', departureDate: '2026-11-02', returnDate: '2026-11-22', travellers: '2', budget: '900' }
  const NOW = new Date('2026-10-30T12:00:00Z')

  it('accepts 21 days, the backend maximum (app/schemas.py MAX_TRIP_DAYS)', () => {
    assert.deepEqual(validateTripForm(ok, NOW), [])
  })

  it('refuses 22 days before any request is sent', () => {
    assert.match(validateTripForm({ ...ok, returnDate: '2026-11-23' }, NOW).join(' '), /at most 21 days/)
  })
})

describe('travel between stops comes from the backend summary', () => {
  it('labels walking and driving legs as route estimates and flags a long walk', () => {
    const [day] = buildTripView(RESULT.itinerary, RESULT.summary).days
    assert.equal(day.activities[0].travel, null) // the first stop has no previous leg
    assert.match(day.activities[1].travel, /About 4 min walk .*estimated walking route/)
    assert.match(day.activities[2].travel, /About 12 min by car .*traffic not included/)
    assert.equal(day.activities[2].longWalk, false)
    assert.equal(day.activities[3].longWalk, true) // 41 min on foot is over the 30-minute limit
    assert.equal(day.activities[1].type, 'Sight') // kind from the summary
  })

  it('shows the measured walks of the recorded real Amman result (older walk_minutes shape)', () => {
    const view = buildTripView(RECORDED_AMMAN_RESULT.itinerary, RECORDED_AMMAN_RESULT.summary)
    const legs = view.days.flatMap((d) => d.activities.map((a) => a.travel)).filter(Boolean)
    assert.ok(legs.length > 0, 'the recorded result has walking times and they must be shown')
    assert.ok(legs.some((t) => /About 140 min walk/.test(t)))
    assert.ok(view.days[0].activities.some((a) => a.longWalk))
  })

  it('ignores a summary entry that is about a different activity', () => {
    const summary = { days: [{ date: '2026-11-02', activities: [{ name: 'Somewhere else', travel_minutes: 9, travel_mode: 'foot' }] }] }
    const [day] = buildTripView(RESULT.itinerary, summary).days
    assert.equal(day.activities[0].travel, null)
  })

  it('works without a summary (saved trips and samples from before it existed)', () => {
    const [day] = buildTripView(RESULT.itinerary).days
    assert.ok(day.activities.every((a) => a.travel === null))
    assert.equal(describeTravel(15), 'About 15 min from the previous stop') // mode unknown: no claim about how
  })
})

describe('prices', () => {
  it('shows null as unknown and 0 as free, never the other way round', () => {
    const [day] = buildTripView(RESULT.itinerary, RESULT.summary).days
    assert.equal(day.activities[2].price, 'Price unknown')
    assert.equal(day.activities[2].priceUnknown, true)
    assert.equal(day.activities[3].price, 'Free')
    assert.equal(day.activities[3].priceUnknown, false)
  })

  it('does not crash on a currency code Intl does not know', () => {
    assert.equal(formatMoney(12.5, 'ZZ1'), '12.50 ZZ1')
    assert.equal(formatMoney(12.5, 'USD'), '$12.50')
  })

  it('reports the budget analyst’s confidence, price ranges and suggestions as sent', () => {
    const notes = budgetNotes(RESULT.budget)
    assert.equal(notes.confidence, 'medium')
    assert.match(notes.statusText, /cannot be confirmed/)
    assert.deepEqual(notes.priceDoubts, [RESULT.budget.warnings[0]])
    assert.equal(notes.suggestions.length, 1)
    assert.equal(budgetNotes(null), null)
  })
})

describe('verification is shown as the backend decided it', () => {
  it('groups Python’s findings and never upgrades them', () => {
    const v = summarizeVerification(RESULT.verification)
    assert.equal(v.problems.length, 1)
    assert.equal(v.unverifiable.length, 1)
    assert.equal(v.advice.length, 1)
    assert.equal(v.repaired, true)
    assert.match(v.headline, /could not be checked/)
  })

  it('a plan that passed only on recorded data is labelled, and is still not verified', () => {
    const recorded = { ...RESULT, verification: { ...RESULT.verification, state: 'supported_by_records', status: 'PASS' } }
    const state = getVerificationState(recorded)
    assert.equal(state.level, 'unverified')
    assert.match(state.label, /recorded data only/)
  })

  it('only a verified pending save_trip action shows as verified', () => {
    const live = { ...RESULT, verification: { ...RESULT.verification, state: 'confirmed_valid' } }
    assert.equal(getVerificationState(live).level, 'unverified') // no pending action: not approvable
  })
})

describe('warnings in the PDF and the dashboard', () => {
  it('include budget doubts and verification problems, once each', () => {
    const warnings = resultWarnings(RESULT)
    assert.ok(warnings.includes(RESULT.summary.limitations[0]))
    assert.ok(warnings.includes(RESULT.budget.warnings[0]))
    assert.ok(warnings.some((w) => w === 'Verification: Pera Museum is closed on Mondays.'))
    assert.ok(warnings.some((w) => /Galata Tower: no opening hours/.test(w)))
    assert.equal(warnings.some((w) => w.includes('A price is a range.')), false) // advice is not a warning
    assert.equal(new Set(warnings).size, warnings.length)
  })
})

describe('PDF export of a long trip', () => {
  const days = Array.from({ length: 21 }, (_, i) => ({
    date: `2026-11-${String(i + 2).padStart(2, '0')}`,
    activities: Array.from({ length: 5 }, (_, j) => ({
      name: `Place ${i}-${j} with a rather long name that wraps`,
      start_time: '09:00', end_time: '10:00',
      estimated_cost: j === 0 ? null : j === 1 ? 0 : 10,
      source_url: `https://en.wikivoyage.org/wiki/Istanbul/${'Very_Long_District_Name_'.repeat(6)}#See`,
      notes: 'Opening hours unknown: check before you go; price basis not stated in the source. '.repeat(6),
    })),
  }))
  const result = { ...RESULT, itinerary: { ...RESULT.itinerary, days }, summary: null }

  it('paginates 21 days with long notes and URLs, numbering every page', () => {
    const doc = buildTripPdf(pdfInputFromResult(result, { now: new Date('2026-10-30T12:00:00Z') }))
    const pages = doc.getNumberOfPages()
    assert.ok(pages >= 10, `pages: ${pages}`)
    const raw = pdfString(doc)
    assert.ok(raw.includes(`Page ${pages} of ${pages}`))
    assert.ok(raw.includes('Day 21'))
    assert.ok(raw.includes('Price unknown') && raw.includes('Free'))
    assert.ok(raw.includes('Warnings and limitations'))
  })

  it('prints the travel legs and flags the long walk', () => {
    const raw = pdfString(buildTripPdf(pdfInputFromResult(RESULT)))
    assert.ok(raw.includes('About 4 min walk from the previous stop'))
    assert.ok(raw.includes('a long walk; local transport may be faster'))
    assert.ok(raw.includes('Verification: Pera Museum is closed on Mondays.'))
  })
})

describe('changing a trip from the chat (reviseTrip + interpretRevision)', () => {
  it('sends only the session and the message: no trip form, so it is read as a change', async () => {
    let sent
    globalThis.fetch = async (url, init) => {
      sent = { url, body: JSON.parse(init.body) }
      return { ok: true, status: 200, json: async () => ({ status: 'revision_not_supported', message: 'Not connected yet.' }) }
    }
    const answer = interpretRevision(await reviseTrip('sess-9', '  make day 2 cheaper  '))
    assert.equal(sent.url, '/api/chat')
    assert.deepEqual(sent.body, { session_id: 'sess-9', message: 'make day 2 cheaper' })
    assert.equal(answer.kind, 'unchanged')
    assert.equal(answer.message, 'Not connected yet.')
    assert.equal(answer.approvalLost, true) // app/main.py clears the pending approval on every chat call
  })

  it('a complete new plan replaces the shown one; an incomplete one never does', () => {
    assert.equal(interpretRevision({ ...RESULT, message: 'Day 2 is cheaper.' }).kind, 'revised')
    assert.equal(interpretRevision({ status: 'verification_required', itinerary: { destination: 'X', days: [] } }).kind, 'unchanged')
    assert.equal(interpretRevision(null).kind, 'unchanged')
  })

  it('says the trip was not changed when the request fails', async () => {
    globalThis.fetch = async () => ({ ok: false, status: 500, json: async () => ({}) })
    await assert.rejects(reviseTrip('sess-9', 'cut the budget by 20%'), /Request failed \(500\)\. Your trip was not changed\./)
    globalThis.fetch = async () => {
      throw new TypeError('Failed to fetch')
    }
    await assert.rejects(reviseTrip('sess-9', 'cut it'), /Could not reach the Rihla backend/)
  })

  it('refuses an empty message or a missing session without calling the server', async () => {
    let called = false
    globalThis.fetch = async () => {
      called = true
    }
    await assert.rejects(reviseTrip('sess-9', '   '), /Type the change/)
    await assert.rejects(reviseTrip(null, 'cheaper'), /no planning session/)
    assert.equal(called, false)
  })
})

describe('approval answers in the bootcamp contract shape (common/CONTRACT.md)', () => {
  const fake = (status, body) => async () => ({ ok: status < 400, status, json: async () => body })

  it('approved only with a result.trip_id from the server', async () => {
    const answer = await sendApproval({ sessionId: 's', actionId: 'a', approved: true },
      fake(200, { session_id: 's', reply: 'Saved as TRIP-3001.', result: { trip_id: 'TRIP-3001' } }))
    assert.equal(answer.status, 'approved')
    assert.equal(answer.tripId, 'TRIP-3001')
    assert.equal(answer.message, 'Saved as TRIP-3001.')
  })

  it('a reply without a trip ID after approving is a failure, never an approval', async () => {
    await assert.rejects(
      sendApproval({ sessionId: 's', actionId: 'a', approved: true },
        fake(200, { session_id: 's', reply: 'The trip store is unavailable; nothing was saved.', result: null })),
      /nothing was saved/,
    )
  })

  it('a rejection is a rejection', async () => {
    const answer = await sendApproval({ sessionId: 's', actionId: 'a', approved: false },
      fake(200, { session_id: 's', reply: 'Nothing was saved.', result: null }))
    assert.equal(answer.status, 'rejected')
  })

  it('today’s backend shape still works and exposes the trip ID', async () => {
    const answer = await sendApproval({ sessionId: 's', actionId: 'a', approved: true },
      fake(200, { status: 'approved', message: 'Trip saved successfully.', trip: { trip_id: 'TRIP-3002' } }))
    assert.equal(answer.tripId, 'TRIP-3002')
  })
})
