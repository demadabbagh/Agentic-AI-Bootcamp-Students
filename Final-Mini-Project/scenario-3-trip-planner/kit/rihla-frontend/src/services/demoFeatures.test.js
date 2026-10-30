import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { describe, it } from 'node:test'

import { approvalOptions, sendApproval } from './approval.js'
import {
  getSavedTripMarks, hasSampleTrips, listSavedTrips, loadSampleTrips, removeSampleTrips, saveTrip, summarizeSavedTrip,
} from './savedTrips.js'
import { buildTripPdf, pdfFileName, pdfText } from './tripPdf.js'
import { decisionLabel, pdfInputFromResult } from './tripExport.js'
import { isValidTripResult } from './tripResult.js'
import { SAMPLE_TRIP_ENTRIES } from '../data/sampleTrips.js'

function memoryStorage() {
  const data = {}
  return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v) } }
}

const VERIFIED = {
  session_id: 'sess-1',
  status: 'pending_approval',
  pending_action: { action_id: 'act-1', type: 'save_trip', verification_status: 'verified' },
  message: 'Verified.',
  itinerary: {
    destination: 'Barcelona',
    currency: 'EUR',
    travellers: 2,
    budget_total: 800,
    estimated_total: 26,
    days: [{ date: '2026-12-01', activities: [{ name: 'Sagrada Família', start_time: '09:00', end_time: '11:00', estimated_cost: 26 }] }],
  },
}
const UNVERIFIED = { ...VERIFIED, session_id: 'sess-2', status: 'verification_required', pending_action: null }
const REQUEST = { destination: 'Barcelona, Spain', departureDate: '2026-12-01', returnDate: '2026-12-01', travellers: '2', budget: '800' }

function fakeFetch(status, body) {
  const calls = []
  const impl = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) })
    return { ok: status >= 200 && status < 300, status, json: async () => body }
  }
  return { impl, calls }
}

describe('sample trips', () => {
  it('are valid results, clearly labelled, never verified', () => {
    for (const sample of SAMPLE_TRIP_ENTRIES) {
      assert.equal(isValidTripResult(sample.aiResult), true, sample.id)
      assert.ok(sample.sampleLabel.length > 10)
      assert.notEqual(sample.aiResult.status, 'pending_approval')
    }
  })

  it('load on request, never duplicate, and are removed without touching real trips', () => {
    const storage = memoryStorage()
    saveTrip(UNVERIFIED, REQUEST, storage)
    assert.equal(hasSampleTrips(storage), false)

    assert.equal(loadSampleTrips(storage), SAMPLE_TRIP_ENTRIES.length)
    assert.equal(loadSampleTrips(storage), 0) // pressing again adds nothing
    const all = listSavedTrips(storage)
    assert.equal(all.length, SAMPLE_TRIP_ENTRIES.length + 1)

    const summaries = all.map(summarizeSavedTrip)
    assert.ok(summaries.filter((t) => t.sample).every((t) => t.sampleLabel && !t.verified))
    assert.equal(summaries.find((t) => t.id === 'sess-2').sample, false)

    assert.equal(removeSampleTrips(storage), SAMPLE_TRIP_ENTRIES.length)
    assert.deepEqual(listSavedTrips(storage).map((t) => t.id), ['sess-2'])
  })

  it('the recorded Amman sample is the real AI output, unverified', () => {
    const amman = SAMPLE_TRIP_ENTRIES.find((s) => s.id === 'sample-amman-recorded')
    assert.equal(amman.aiResult.status, 'verification_required')
    assert.match(amman.sampleLabel, /Recorded real AI output/)
    assert.ok(amman.aiResult.itinerary.days.length === 2)
  })

  it('cannot be saved as the user’s own trip', () => {
    assert.throws(() => saveTrip(SAMPLE_TRIP_ENTRIES[0].aiResult, {}, memoryStorage()), /Load Sample Trips/)
  })
})

describe('decisions: backend approval vs local draft acceptance', () => {
  it('offers backend approval only for a verified pending save_trip action', () => {
    assert.deepEqual(approvalOptions(VERIFIED), { canApprove: true, canAcceptDraft: false })
    assert.deepEqual(approvalOptions(UNVERIFIED), { canApprove: false, canAcceptDraft: true })
    const unverifiedAction = { ...VERIFIED, pending_action: { ...VERIFIED.pending_action, verification_status: 'unverified' } }
    assert.equal(approvalOptions(unverifiedAction).canApprove, false)
  })

  it('records a draft acceptance without changing the AI verification status', () => {
    const storage = memoryStorage()
    saveTrip(UNVERIFIED, REQUEST, storage, new Date(), { draftAcceptedAt: '2026-10-30T10:00:00Z' })
    const summary = summarizeSavedTrip(listSavedTrips(storage)[0])
    assert.equal(summary.draftAccepted, true)
    assert.equal(summary.verified, false)
    assert.equal(summary.backendApproved, false)
    assert.match(decisionLabel(getSavedTripMarks('sess-2', storage)), /draft.*not an AI verification/i)
  })

  it('keeps an earlier decision when the trip is saved again', () => {
    const storage = memoryStorage()
    saveTrip(UNVERIFIED, REQUEST, storage, new Date(), { draftAcceptedAt: '2026-10-30T10:00:00Z' })
    saveTrip(UNVERIFIED, REQUEST, storage)
    assert.equal(getSavedTripMarks('sess-2', storage).draftAcceptedAt, '2026-10-30T10:00:00Z')
  })

  it('only stores an approval the backend confirmed', () => {
    const storage = memoryStorage()
    saveTrip(VERIFIED, REQUEST, storage, new Date(), { backendApproval: { status: 'pending' } })
    assert.equal(getSavedTripMarks('sess-1', storage).backendApproval, null)
    assert.equal(decisionLabel({}), 'Not decided')
    assert.match(decisionLabel({ backendApproval: { status: 'approved' } }), /^Approved/)
  })
})

describe('sendApproval', () => {
  const args = { sessionId: 'sess-1', actionId: 'act-1', approved: true }

  it('sends the documented payload and reports a confirmed approval', async () => {
    const { impl, calls } = fakeFetch(200, { status: 'approved', message: 'Trip saved successfully.', trip: { trip_id: 'T-9' } })
    const answer = await sendApproval(args, impl)
    assert.deepEqual(calls[0], { url: '/api/approve', body: { session_id: 'sess-1', action_id: 'act-1', approved: true } })
    assert.equal(answer.status, 'approved')
    assert.equal(answer.trip.trip_id, 'T-9')
  })

  it('reports a rejection as a rejection', async () => {
    const { impl } = fakeFetch(200, { status: 'rejected', message: 'Trip was not saved' })
    assert.equal((await sendApproval({ ...args, approved: false }, impl)).status, 'rejected')
  })

  it('refuses an unverified trip (403) and never claims approval', async () => {
    const { impl } = fakeFetch(403, { detail: 'Trip has not passed real verification.' })
    await assert.rejects(sendApproval(args, impl), /not passed verification[\s\S]*real verification/)
  })

  it('explains a lost session (404) and a server save failure (503)', async () => {
    await assert.rejects(sendApproval(args, fakeFetch(404, { detail: 'No pending action for this session' }).impl), /Plan the trip again/)
    await assert.rejects(sendApproval(args, fakeFetch(503, { detail: 'Traveller ID is not configured.' }).impl), /Traveller ID is not configured[\s\S]*Nothing was approved/)
  })

  it('handles an unreachable backend and an unexpected 200 answer', async () => {
    await assert.rejects(sendApproval(args, async () => { throw new TypeError('fetch failed') }), /Could not reach/)
    await assert.rejects(sendApproval(args, fakeFetch(200, { status: 'maybe' }).impl), /unexpected/)
  })
})

describe('trip PDF', () => {
  const pdfString = (doc) => Buffer.from(doc.output('arraybuffer')).toString('latin1')

  it('contains the trip data, verification status and unknown-price markers', () => {
    const amman = SAMPLE_TRIP_ENTRIES.find((s) => s.id === 'sample-amman-recorded')
    const input = pdfInputFromResult(amman.aiResult, { sampleLabel: amman.sampleLabel, now: new Date('2026-10-30T12:00:00Z') })
    const raw = pdfString(buildTripPdf(input))
    assert.ok(raw.startsWith('%PDF-'))
    for (const text of ['Rihla.', 'Amman, Jordan', 'SAMPLE TRIP', 'Travellers: 2', 'Budget: $900.00 \\(USD\\)', // PDF escapes parentheses
      'AI verification: Not verified \\(sample\\)', 'Day 01', 'Day 02', 'Amman Citadel', 'Price unknown',
      'Warnings and limitations', 'Page 1 of']) {
      assert.ok(raw.includes(text), `missing: ${text}`)
    }
    assert.equal(raw.includes('Istanbul'), false) // no unrelated sample content
  })

  it('spreads a long multi-day trip over several pages', () => {
    const days = Array.from({ length: 7 }, (_, i) => ({
      date: `2026-12-0${i + 1}`,
      activities: Array.from({ length: 6 }, (_, j) => ({
        name: `Place ${i}-${j}`, start_time: '09:00', end_time: '10:00', estimated_cost: j % 2 ? null : 10,
        notes: 'A long description that should wrap onto several lines without overlapping the next activity. '.repeat(3),
      })),
    }))
    const result = { ...UNVERIFIED, itinerary: { ...UNVERIFIED.itinerary, days } }
    const doc = buildTripPdf(pdfInputFromResult(result))
    assert.ok(doc.getNumberOfPages() >= 3, `pages: ${doc.getNumberOfPages()}`)
  })

  it('does not crash on missing fields', () => {
    const sparse = {
      session_id: 's', status: 'verification_required',
      itinerary: { destination: 'Tokyo', days: [{ date: '2026-11-10', activities: [{ name: '浅草寺 Senso-ji' }] }, { date: '2026-11-11', activities: [] }] },
    }
    const raw = pdfString(buildTripPdf(pdfInputFromResult(sparse)))
    assert.ok(raw.includes('Budget: Not specified'))
    assert.ok(raw.includes('Nothing is planned for this day.'))
    assert.ok(raw.includes('Senso-ji'))
  })

  it('maps characters the PDF font cannot draw and makes a safe file name', () => {
    assert.equal(pdfText('Café – “Nice”'), 'Café - "Nice"')
    assert.equal(pdfText('東京'), '??')
    assert.equal(pdfFileName('Sagrada Família, Barcelona', '2026-12-01'), 'rihla-sagrada-familia-barcelona-2026-12-01.pdf')
  })
})
