import { isValidTripResult } from './tripResult.js'
import { SAMPLE_TRIP_ENTRIES } from '../data/sampleTrips.js'

// Trips saved in THIS browser only (localStorage): no account, no cloud copy,
// no sync between devices. The backend has no "list my trips" endpoint for the
// frontend yet, so this is the honest local substitute.
export const STORAGE_KEY = 'rihla.savedTrips.v1'
export const MAX_SAVED_TRIPS = 30

function defaultStorage() {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null // blocked storage (private mode, disabled site data)
  }
}

function readAll(storage) {
  if (!storage) return []
  try {
    const parsed = JSON.parse(storage.getItem(STORAGE_KEY) || '[]')
    // Drop anything that is no longer a displayable trip (old format, hand-edited).
    return Array.isArray(parsed) ? parsed.filter(isSavedTrip) : []
  } catch {
    return []
  }
}

function writeAll(storage, trips) {
  if (!storage) throw new Error('This browser does not allow saving trips (storage is blocked).')
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(trips))
  } catch {
    throw new Error('The trip could not be saved: browser storage is full or blocked.')
  }
}

function isSavedTrip(entry) {
  return Boolean(
    entry
    && typeof entry === 'object'
    && typeof entry.id === 'string'
    && typeof entry.savedAt === 'string'
    && isValidTripResult(entry.aiResult)
  )
}

// Only what the dashboard needs to show the trip again; never tokens or keys.
function requestFields(request = {}) {
  return {
    destination: String(request.destination || ''),
    departureDate: String(request.departureDate || ''),
    returnDate: String(request.returnDate || ''),
    travellers: request.travellers ?? null,
    budget: request.budget ?? null,
    interests: Array.isArray(request.interests) ? request.interests.map(String) : [],
    customPreferences: String(request.customPreferences || ''),
  }
}

export function summarizeSavedTrip(entry) {
  const itinerary = entry.aiResult.itinerary
  const days = itinerary.days
  return {
    id: entry.id,
    savedAt: entry.savedAt,
    destination: itinerary.destination,
    startDate: days[0]?.date || null,
    endDate: days[days.length - 1]?.date || null,
    dayCount: days.length,
    activityCount: days.reduce((n, d) => n + (d.activities?.length || 0), 0),
    travellers: itinerary.travellers ?? null,
    budgetTotal: itinerary.budget_total ?? null,
    currency: itinerary.currency || 'USD',
    verified: entry.aiResult.status === 'pending_approval'
      && entry.aiResult.pending_action?.verification_status === 'verified',
    sample: entry.sample === true,
    sampleLabel: entry.sample === true ? entry.sampleLabel || 'Demo data' : null,
    // Two different facts: the backend saved it after approval, or the traveller accepted an unverified draft here.
    backendApproved: entry.backendApproval?.status === 'approved',
    draftAccepted: Boolean(entry.draftAcceptedAt),
  }
}

export function listSavedTrips(storage = defaultStorage()) {
  return readAll(storage)
    .sort((a, b) => b.savedAt.localeCompare(a.savedAt))
}

export function getSavedTrip(id, storage = defaultStorage()) {
  return readAll(storage).find((entry) => entry.id === id) || null
}

// The session id identifies one generated result, so saving it twice updates
// the same entry instead of creating a duplicate.
// `marks` records the traveller's decision: { draftAcceptedAt } or { backendApproval }.
// Saving again keeps any decision recorded earlier for the same trip.
export function saveTrip(aiResult, request, storage = defaultStorage(), now = new Date(), marks = {}) {
  if (!isValidTripResult(aiResult)) {
    throw new Error('Only a generated itinerary can be saved.')
  }

  const id = String(aiResult.session_id || request?.sessionId || `trip-${now.getTime()}`)
  if (id.startsWith(SAMPLE_PREFIX)) {
    throw new Error('Sample trips are added with "Load Sample Trips", not saved as your own.')
  }
  const all = readAll(storage)
  const previous = all.find((t) => t.id === id)
  const entry = {
    id,
    savedAt: now.toISOString(),
    request: requestFields(request),
    aiResult,
    ...(previous?.draftAcceptedAt ? { draftAcceptedAt: previous.draftAcceptedAt } : {}),
    ...(previous?.backendApproval ? { backendApproval: previous.backendApproval } : {}),
    ...pickMarks(marks),
  }

  const others = all.filter((t) => t.id !== id)
  if (others.length >= MAX_SAVED_TRIPS) {
    throw new Error(`You can keep up to ${MAX_SAVED_TRIPS} trips in this browser. Delete one to save another.`)
  }
  writeAll(storage, [entry, ...others])
  return entry
}

export function deleteSavedTrip(id, storage = defaultStorage()) {
  const trips = readAll(storage)
  const remaining = trips.filter((t) => t.id !== id)
  if (remaining.length !== trips.length) writeAll(storage, remaining)
  return remaining.length !== trips.length
}

export function isTripSaved(id, storage = defaultStorage()) {
  return Boolean(id) && readAll(storage).some((t) => t.id === id)
}

const SAMPLE_PREFIX = 'sample-'

function pickMarks(marks) {
  const out = {}
  if (typeof marks.draftAcceptedAt === 'string') out.draftAcceptedAt = marks.draftAcceptedAt
  if (marks.backendApproval?.status === 'approved') {
    out.backendApproval = {
      status: 'approved',
      approvedAt: String(marks.backendApproval.approvedAt || ''),
      tripId: marks.backendApproval.tripId ? String(marks.backendApproval.tripId) : null,
    }
  }
  return out
}

export function getSavedTripMarks(id, storage = defaultStorage()) {
  const entry = id ? getSavedTrip(id, storage) : null
  return {
    draftAcceptedAt: entry?.draftAcceptedAt || null,
    backendApproval: entry?.backendApproval || null,
  }
}

export function hasSampleTrips(storage = defaultStorage()) {
  return readAll(storage).some((t) => t.sample === true)
}

// Adds the demo entries that are not there yet; never touches the user's own trips.
export function loadSampleTrips(storage = defaultStorage(), now = new Date()) {
  const all = readAll(storage)
  const present = new Set(all.map((t) => t.id))
  const added = SAMPLE_TRIP_ENTRIES
    .filter((sample) => !present.has(sample.id))
    .map((sample, index) => ({
      id: sample.id,
      savedAt: new Date(now.getTime() - (index + 1) * 1000).toISOString(),
      sample: true,
      sampleLabel: sample.sampleLabel,
      request: requestFields(sample.request),
      aiResult: sample.aiResult,
    }))
  if (added.length) writeAll(storage, [...all, ...added])
  return added.length
}

export function removeSampleTrips(storage = defaultStorage()) {
  const all = readAll(storage)
  const remaining = all.filter((t) => t.sample !== true)
  if (remaining.length !== all.length) writeAll(storage, remaining)
  return all.length - remaining.length
}
