import assert from 'node:assert/strict'
import { afterEach, describe, it } from 'node:test'

import { buildTripForm, createTrip } from './tripApi.js'

const TRIP = { destination: 'Amman', departureDate: '2026-11-02', returnDate: '2026-11-09', travellers: '2', budget: '2000', interests: ['History & Culture'] }
const realFetch = globalThis.fetch

function respondWith(status, body, { json = true } = {}) {
  globalThis.fetch = async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (!json) throw new SyntaxError('not JSON')
      return body
    },
  })
}

afterEach(() => {
  globalThis.fetch = realFetch
})

describe('createTrip backend failures', () => {
  it('explains a 402 from the provider', async () => {
    respondWith(402, { detail: 'Payment Required' })
    await assert.rejects(createTrip(TRIP, 's'), /payment required/i)
  })

  it('explains a 503 from the backend', async () => {
    respondWith(503, { detail: 'The trip planning service is temporarily unavailable.' })
    await assert.rejects(createTrip(TRIP, 's'), /temporarily unavailable/)
  })

  it('keeps a readable backend detail for other errors', async () => {
    respondWith(403, { detail: 'Trip has not passed real verification.' })
    await assert.rejects(createTrip(TRIP, 's'), /Trip has not passed real verification/)
  })

  it('falls back to the HTTP status when the error body is not JSON', async () => {
    respondWith(500, null, { json: false })
    await assert.rejects(createTrip(TRIP, 's'), /Request failed \(500\)/)
  })

  it('explains an unreachable backend', async () => {
    globalThis.fetch = async () => {
      throw new TypeError('Failed to fetch')
    }
    await assert.rejects(createTrip(TRIP, 's'), /Could not reach the Rihla backend/)
  })

  it('rejects an unreadable success response', async () => {
    respondWith(200, null, { json: false })
    await assert.rejects(createTrip(TRIP, 's'), /unreadable response/)
  })

  it('returns the parsed body and sends the trip as one message', async () => {
    let sent
    globalThis.fetch = async (url, init) => {
      sent = { url, body: JSON.parse(init.body) }
      return { ok: true, status: 200, json: async () => ({ status: 'needs_revision', itinerary: null }) }
    }
    const result = await createTrip(TRIP, 'session-1')
    assert.equal(result.status, 'needs_revision')
    assert.equal(sent.url, '/api/chat')
    assert.equal(sent.body.session_id, 'session-1')
    assert.match(sent.body.message, /Plan a trip to Amman/)
    assert.match(sent.body.message, /Travel interests: History & Culture/)
  })
})

describe('the structured trip form', () => {
  it('sends the exact selected interests and numeric fields with the message', async () => {
    let sent
    globalThis.fetch = async (_url, options) => {
      sent = JSON.parse(options.body)
      return { ok: true, status: 200, json: async () => ({ status: 'verification_required' }) }
    }
    await createTrip({ ...TRIP, interests: ['History & Culture', 'Shopping'], customPreferences: '  quiet cafes ' }, 's1')
    assert.deepEqual(sent.trip, {
      destination: 'Amman', start_date: '2026-11-02', end_date: '2026-11-09', travellers: 2, budget: 2000,
      currency: 'USD', interests: ['History & Culture', 'Shopping'], preferences: 'quiet cafes',
    })
    assert.match(sent.message, /Travel interests: History & Culture, Shopping\./)
  })

  it('sends no preferences when the free text is empty', () => {
    assert.equal(buildTripForm({ ...TRIP, customPreferences: '   ' }).preferences, null)
  })

  it('turns a 422 validation error into the backend reason', async () => {
    respondWith(422, { detail: [{ loc: ['body', 'trip'], msg: 'Value error, The return date is before the departure date.' }] })
    await assert.rejects(createTrip(TRIP, 's'), /Please check the trip details: The return date is before the departure date\./)
  })
})
