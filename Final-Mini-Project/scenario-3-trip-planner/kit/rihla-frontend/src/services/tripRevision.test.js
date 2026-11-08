import assert from 'node:assert/strict'
import { afterEach, describe, it } from 'node:test'

import { reviseTrip } from './tripApi.js'
import { applyRevision, revisionBlockedReason } from './tripRevision.js'
import { getSavedTrip, saveTrip } from './savedTrips.js'
import { getVerificationState } from './tripResult.js'
import { pdfModel } from './pdfExport.js'

const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

function respond(status, body) {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options: { ...options, body: JSON.parse(options.body) } })
    return { ok: status >= 200 && status < 300, status, json: async () => body }
  }
  return calls
}

function memoryStorage() {
  const data = new Map()
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)) }
}

const day = (date, names) => ({ date, activities: names.map((name) => ({ name, estimated_cost: 10 })) })
const CURRENT = {
  session_id: 's-1', status: 'pending_approval', plan_version: 'v1', message: 'Verified.',
  pending_action: { action_id: 'old-action', type: 'save_trip', verification_status: 'verified' },
  itinerary: { destination: 'Istanbul', currency: 'USD', budget_total: 700, days: [
    day('2026-11-02', ['Topkapı Palace']), day('2026-11-03', ['Pera Museum', 'Galata Tower'])] },
}
const REVISED = {
  session_id: 's-1', status: 'verification_required', plan_version: 'v2', pending_action: null,
  message: 'Your itinerary has been updated. It could not be fully verified.',
  itinerary: { ...CURRENT.itinerary, budget_total: 560, days: [CURRENT.itinerary.days[0], day('2026-11-03', ['Galata Tower'])] },
  revision: { outcome: 'revised', changes: ['Budget changed from 700.00 to 560.00 USD.', 'Day 2 (Tue 03 Nov): removed Pera Museum.'] },
}

describe('reviseTrip', () => {
  it('sends the change with the plan version it was made on', async () => {
    const calls = respond(200, REVISED)
    const body = await reviseTrip('s-1', 'Reduce my budget by 20%', 'v1')
    assert.equal(calls[0].url, '/api/chat')
    assert.deepEqual(calls[0].options.body,
      { session_id: 's-1', message: 'Reduce my budget by 20%', revision: true, base_version: 'v1' })
    assert.equal(body.plan_version, 'v2')
  })

  it('says the itinerary was not changed when the backend fails or cannot be reached', async () => {
    respond(503, { detail: 'down' })
    await assert.rejects(reviseTrip('s-1', 'x', 'v1'), /temporarily unavailable.*not changed/)
    respond(500, { detail: 'The planner failed (error id ab12).' })
    await assert.rejects(reviseTrip('s-1', 'x', 'v1'), /error id ab12\). Your itinerary was not changed/)
    globalThis.fetch = async () => { throw new TypeError('network') }
    await assert.rejects(reviseTrip('s-1', 'x', 'v1'), /Could not reach the Rihla backend/)
  })
})

describe('applyRevision', () => {
  it('replaces the itinerary with the revised one and lists only the actual changes', () => {
    const next = applyRevision(CURRENT, REVISED)
    assert.equal(next.outcome, 'revised')
    assert.equal(next.result.itinerary.budget_total, 560)
    assert.equal(next.result.plan_version, 'v2')
    assert.deepEqual(next.changes, REVISED.revision.changes)
  })

  it('never carries the old approval to the new version: unverified is not approvable', () => {
    const next = applyRevision({ ...CURRENT, approval: { trip_id: 'T1' } }, { ...REVISED, approval: { trip_id: 'T1' } })
    assert.equal(next.result.approval, undefined)
    assert.equal(next.result.pending_action, null)
    assert.equal(getVerificationState(next.result).level, 'unverified')
  })

  it('a verified revision is approvable only through its own new action', () => {
    const verified = { ...REVISED, status: 'pending_approval',
      pending_action: { action_id: 'new-action', type: 'save_trip', verification_status: 'verified' } }
    const next = applyRevision(CURRENT, verified)
    assert.equal(getVerificationState(next.result).level, 'verified')
    assert.equal(next.result.pending_action.action_id, 'new-action')
  })

  for (const [outcome, status] of [['clarification', 'needs_clarification'], ['not_feasible', 'revision_not_feasible'],
    ['stale_plan', 'needs_clarification'], ['no_plan', 'needs_clarification']]) {
    it(`keeps the itinerary on screen for ${outcome}`, () => {
      const next = applyRevision(CURRENT, { status, message: 'Which day?', itinerary: null, revision: { outcome } })
      assert.equal(next.result, null)
      assert.equal(next.reply, 'Which day?')
      assert.deepEqual(next.changes, [])
    })
  }

  it('refuses an incomplete revised itinerary instead of showing it', () => {
    assert.throws(() => applyRevision(CURRENT, { ...REVISED, itinerary: { destination: 'Istanbul', days: [] } }),
      /incomplete itinerary/)
  })

  it('caps a long change list', () => {
    const changes = Array.from({ length: 15 }, (_, n) => `Change ${n}`)
    const next = applyRevision(CURRENT, { ...REVISED, revision: { outcome: 'revised', changes } })
    assert.equal(next.changes.length, 12)
    assert.equal(next.moreChanges, 3)
  })

  it('a saved draft and the PDF use the revised data, not the stale version', () => {
    const storage = memoryStorage()
    saveTrip(CURRENT, { destination: 'Istanbul', budget: 700 }, storage)
    const next = applyRevision(CURRENT, REVISED)
    saveTrip(next.result, { destination: 'Istanbul', budget: 560 }, storage)
    const draft = getSavedTrip('s-1', storage)
    assert.equal(draft.aiResult.plan_version, 'v2')
    assert.equal(draft.request.budget, 560)
    const pdf = JSON.stringify(pdfModel({ aiResult: next.result, request: draft.request }))
    assert.ok(!pdf.includes('Pera Museum'))
    assert.ok(pdf.includes('Galata Tower'))
  })
})

describe('revisionBlockedReason', () => {
  it('allows changes to a plan the backend keeps', () => {
    assert.equal(revisionBlockedReason(CURRENT), null)
  })

  it('explains why sample, approved and saved-only trips cannot be changed here', () => {
    assert.match(revisionBlockedReason(CURRENT, { sample: true }), /Sample mode/)
    assert.match(revisionBlockedReason(CURRENT, { approved: true }), /approved and saved/)
    assert.match(revisionBlockedReason({ ...CURRENT, status: 'approved' }), /approved and saved/)
    assert.match(revisionBlockedReason({ ...CURRENT, plan_version: undefined }), /no longer has it in memory/)
  })
})

describe('applyRevision: what the assistant understood', () => {
  it('shows how the request was read, for a revision and for a question', () => {
    const revised = applyRevision(CURRENT, { ...REVISED, revision: { ...REVISED.revision,
      understood: ['budget set to 200.00 EUR = 223.86 USD (rate EUR->USD 1.1193)'] } })
    assert.deepEqual(revised.understood, ['budget set to 200.00 EUR = 223.86 USD (rate EUR->USD 1.1193)'])
    const question = applyRevision(CURRENT, { status: 'needs_clarification',
      message: 'Do you want your total trip budget to be 20 EUR?',
      revision: { outcome: 'clarification', interpreted_by: 'revision_agent' } })
    assert.equal(question.result, null)
    assert.equal(question.outcome, 'clarification')
    assert.deepEqual(question.understood, [])
  })
})
