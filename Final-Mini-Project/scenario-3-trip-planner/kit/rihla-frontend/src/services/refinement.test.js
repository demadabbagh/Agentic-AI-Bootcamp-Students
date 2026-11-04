import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { reviewIsRunning, reviewView, getReview, applyReview } from './refinement.js'
import { prepareDestination } from './tripApi.js'

const answer = (status, data) => async (path, options) => ({
  status,
  ok: status < 400,
  json: async () => ({ ...data, path, sent: JSON.parse(options.body) }),
})

test('a running review never says the plan changed', () => {
  assert.equal(reviewIsRunning('running'), true)
  assert.equal(reviewView({ status: 'running' }).kind, 'running')
})

test('an offer shows at most six changes, as text', () => {
  const view = reviewView({ status: 'ready', changes: [1, 2, 3, 4, 5, 6, 7] })
  assert.equal(view.kind, 'ready')
  assert.deepEqual(view.changes, ['1', '2', '3', '4', '5', '6'])
})

test('a finished or failed review is only a note, a stale one offers nothing', () => {
  assert.equal(reviewView({ status: 'no_change' }).kind, 'done')
  assert.equal(reviewView({ status: 'failed' }).kind, 'done')
  assert.notEqual(reviewView({ status: 'stale' })?.kind, 'ready')
})

test('reading and applying name the plan on screen', async () => {
  const read = await getReview('s-1', 'v2', answer(200, { status: 'running' }))
  assert.equal(read.path, '/api/plan/refinement')
  assert.deepEqual(read.sent, { session_id: 's-1', base_version: 'v2' })
  assert.equal((await applyReview('s-1', null, answer(200, {}))).path, '/api/plan/refinement/apply')
})

test('a backend that cannot be reached says the itinerary did not change', async () => {
  await assert.rejects(getReview('s', 'v', async () => { throw new Error('offline') }), /not changed/)
  await assert.rejects(getReview('s', 'v', answer(500, {})), /not changed/)
})

test('picking a destination asks for a head start with its label, capped at the form limit', async () => {
  const sent = []
  await prepareDestination('Paris, France', async (path, options) => sent.push([path, JSON.parse(options.body)]))
  await prepareDestination('x'.repeat(150), async (path, options) => sent.push([path, JSON.parse(options.body)]))
  assert.deepEqual(sent[0], ['/api/plan/prepare', { destination: 'Paris, France' }])
  assert.equal(sent[1][1].destination.length, 100)
})

test('a failed head start never reaches the page', async () => {
  assert.equal(await prepareDestination('Paris', async () => { throw new Error('offline') }), undefined)
  assert.equal(await prepareDestination('Paris', () => { throw new Error('sync') }), undefined)
})
