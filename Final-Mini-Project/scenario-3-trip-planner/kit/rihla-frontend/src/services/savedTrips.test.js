import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  deleteSavedTrip, getSavedTrip, isTripSaved, listSavedTrips, MAX_SAVED_TRIPS, saveTrip, STORAGE_KEY,
  summarizeSavedTrip,
} from './savedTrips.js'
import { validateTripForm } from './tripForm.js'

function memoryStorage(initial = {}) {
  const data = { ...initial }
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v) },
    data,
  }
}

const RESULT = {
  session_id: 'sess-paris',
  status: 'pending_approval',
  pending_action: { type: 'save_trip', verification_status: 'verified' },
  message: 'Verified.',
  itinerary: {
    destination: 'Paris',
    currency: 'EUR',
    travellers: 2,
    budget_total: 1500,
    estimated_total: 30,
    days: [
      { date: '2026-12-01', activities: [{ name: 'Louvre', estimated_cost: 30 }] },
      { date: '2026-12-02', activities: [{ name: 'Seine walk', estimated_cost: null }] },
    ],
  },
}

const REQUEST = { destination: 'Paris, France', departureDate: '2026-12-01', returnDate: '2026-12-02', travellers: '2', budget: '1500', interests: ['Art & Museums'] }

describe('saved trips (browser-local)', () => {
  it('saves, lists, opens and deletes a generated trip', () => {
    const storage = memoryStorage()
    saveTrip(RESULT, REQUEST, storage, new Date('2026-10-30T10:00:00Z'))

    const listed = listSavedTrips(storage)
    assert.equal(listed.length, 1)
    assert.equal(getSavedTrip('sess-paris', storage).request.destination, 'Paris, France')
    assert.equal(isTripSaved('sess-paris', storage), true)

    const summary = summarizeSavedTrip(listed[0])
    assert.equal(summary.destination, 'Paris')
    assert.equal(summary.dayCount, 2)
    assert.equal(summary.activityCount, 2)
    assert.equal(summary.currency, 'EUR')
    assert.equal(summary.verified, true)

    assert.equal(deleteSavedTrip('sess-paris', storage), true)
    assert.equal(listSavedTrips(storage).length, 0)
    assert.equal(deleteSavedTrip('sess-paris', storage), false)
  })

  it('saving the same result twice updates it instead of duplicating', () => {
    const storage = memoryStorage()
    saveTrip(RESULT, REQUEST, storage, new Date('2026-10-30T10:00:00Z'))
    saveTrip(RESULT, REQUEST, storage, new Date('2026-10-30T11:00:00Z'))
    assert.equal(listSavedTrips(storage).length, 1)
  })

  it('lists newest first', () => {
    const storage = memoryStorage()
    saveTrip(RESULT, REQUEST, storage, new Date('2026-10-30T10:00:00Z'))
    saveTrip({ ...RESULT, session_id: 'sess-tokyo' }, REQUEST, storage, new Date('2026-10-30T12:00:00Z'))
    assert.deepEqual(listSavedTrips(storage).map((t) => t.id), ['sess-tokyo', 'sess-paris'])
  })

  it('refuses to save something that is not a generated itinerary', () => {
    const storage = memoryStorage()
    assert.throws(() => saveTrip({ status: 'needs_revision', itinerary: null }, REQUEST, storage), /Only a generated/)
    assert.throws(() => saveTrip(null, REQUEST, storage), /Only a generated/)
  })

  it('ignores corrupt or hand-edited storage instead of crashing', () => {
    assert.deepEqual(listSavedTrips(memoryStorage({ [STORAGE_KEY]: '{not json' })), [])
    const mixed = JSON.stringify([{ id: 'x' }, { id: 'y', savedAt: 'z', aiResult: { status: 'pending_approval' } }])
    assert.deepEqual(listSavedTrips(memoryStorage({ [STORAGE_KEY]: mixed })), [])
  })

  it('works (empty) when storage is unavailable, and saving explains why it fails', () => {
    assert.deepEqual(listSavedTrips(null), [])
    assert.throws(() => saveTrip(RESULT, REQUEST, null), /storage is blocked/)
  })

  it('reports a full storage quota as a readable error', () => {
    const storage = { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError') } }
    assert.throws(() => saveTrip(RESULT, REQUEST, storage), /full or blocked/)
  })

  it('caps the number of saved trips', () => {
    const storage = memoryStorage()
    for (let i = 0; i < MAX_SAVED_TRIPS; i += 1) {
      saveTrip({ ...RESULT, session_id: `s${i}` }, REQUEST, storage, new Date(1_800_000_000_000 + i))
    }
    assert.throws(() => saveTrip({ ...RESULT, session_id: 'one-more' }, REQUEST, storage), /up to/)
  })

  it('stores only the request fields the dashboard needs', () => {
    const storage = memoryStorage()
    const entry = saveTrip(RESULT, { ...REQUEST, apiKey: 'secret' }, storage)
    assert.equal('apiKey' in entry.request, false)
    assert.equal(storage.data[STORAGE_KEY].includes('secret'), false)
  })
})

describe('validateTripForm', () => {
  const NOW = new Date('2026-10-30T09:00:00')
  const ok = { destination: 'Tokyo, Japan', departureDate: '2026-11-02', returnDate: '2026-11-05', travellers: '2', budget: '1200' }

  it('accepts a valid trip', () => {
    assert.deepEqual(validateTripForm(ok, NOW), [])
  })

  it('accepts a one-day trip', () => {
    assert.deepEqual(validateTripForm({ ...ok, returnDate: ok.departureDate }, NOW), [])
  })

  it('rejects a return before departure', () => {
    assert.match(validateTripForm({ ...ok, returnDate: '2026-11-01' }, NOW).join(' '), /on or after/)
  })

  it('rejects a past departure date', () => {
    assert.match(validateTripForm({ ...ok, departureDate: '2026-10-01' }, NOW).join(' '), /in the past/)
  })

  it('rejects a missing, zero or negative budget', () => {
    for (const budget of ['', '0', '-5', 'abc']) {
      assert.match(validateTripForm({ ...ok, budget }, NOW).join(' '), /budget greater than 0/)
    }
  })

  it('rejects invalid traveller counts', () => {
    for (const travellers of ['0', '2.5', '21', '']) {
      assert.match(validateTripForm({ ...ok, travellers }, NOW).join(' '), /Travellers must be/)
    }
  })

  it('rejects an empty destination and over-long trips', () => {
    assert.match(validateTripForm({ ...ok, destination: ' ' }, NOW).join(' '), /Enter a destination/)
    assert.match(validateTripForm({ ...ok, returnDate: '2027-01-30' }, NOW).join(' '), /at most/)
  })
})
