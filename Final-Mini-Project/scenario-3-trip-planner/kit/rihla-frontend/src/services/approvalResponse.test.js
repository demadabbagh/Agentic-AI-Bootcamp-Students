import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { approvalOptions } from './approval.js'
import { getVerificationState } from './tripResult.js'

// What the backend's PendingAction lets through (app/schemas.py; tests/test_pending_action_public.py holds the other
// side). When the response dropped verification_status, Approve silently never appeared: this pins the shape together.
const publicAction = (over = {}) => ({
  action_id: 'act-1',
  type: 'save_trip',
  summary: 'Save the trip.',
  payload: { traveller_id: 'RIHLA-x', title: 'Amman, 2 days', itinerary: {} },
  verification_status: 'verified_on_records',
  evidence_basis: 'recorded',
  approval_basis: 'recorded',
  disclosure: 'Every check passed, but against recorded data.',
  ...over,
})
const result = (action) => ({ status: 'pending_approval', session_id: 's-1', pending_action: action })

describe('the approval fields the backend sends are the ones the dashboard needs', () => {
  it('offers Approve for a plan that passed, saying what it relies on', () => {
    const recorded = approvalOptions(result(publicAction()))
    assert.equal(recorded.canApprove, true)
    assert.equal(recorded.basis, 'recorded')
    assert.match(recorded.disclosure, /recorded data/)
    const live = approvalOptions(result(publicAction({ verification_status: 'verified', evidence_basis: 'fresh',
      approval_basis: 'live', disclosure: null })))
    assert.equal(live.canApprove, true)
    assert.equal(live.basis, 'live')
    assert.equal(getVerificationState(result(publicAction({ verification_status: 'verified' }))).level, 'verified')
  })

  it('never offers Approve without a status the policy made approvable', () => {
    for (const verification_status of [undefined, null, 'unverified', 'UNKNOWN', 'FAIL', 'PASS']) {
      assert.equal(approvalOptions(result(publicAction({ verification_status }))).canApprove, false, verification_status)
    }
    assert.equal(approvalOptions(result(null)).canApprove, false)
  })
})
