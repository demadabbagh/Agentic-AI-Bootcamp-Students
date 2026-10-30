// Client for POST /api/approve (app/main.py). The backend saves a trip to the
// Trip Store only for a verified pending save_trip action of a live session.

// What the dashboard may offer for this result: backend approval only for a
// verified pending action; otherwise the traveller can accept it as a local draft.
export function approvalOptions(result) {
  const action = result?.pending_action
  const canApprove = Boolean(
    result?.status === 'pending_approval'
    && action?.type === 'save_trip'
    && action?.verification_status === 'verified'
    && action?.action_id
    && result?.session_id
  )
  return { canApprove, canAcceptDraft: !canApprove }
}

const STATUS_MESSAGES = {
  400: 'This approval request no longer matches the planned trip. Plan the trip again to approve it.',
  403: 'Approval refused: the trip has not passed verification, or it changed after verification.',
  404: 'There is no approval waiting for this trip on the server (it may have restarted, or the trip was already decided). Plan the trip again to approve it.',
  422: 'The approval request was incomplete.',
}

// Returns { status: 'approved' | 'rejected', message, trip? } or throws an
// Error whose message is safe to show. Never reports approval without the
// backend saying "approved".
export async function sendApproval({ sessionId, actionId, approved }, fetchImpl = globalThis.fetch) {
  let response
  try {
    response = await fetchImpl('/api/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, action_id: actionId, approved }),
    })
  } catch {
    throw new Error('Could not reach the Rihla backend. Nothing was approved.')
  }

  let body = null
  try {
    body = await response.json()
  } catch {
    // Not JSON: handled below as a failed or unexpected answer.
  }

  if (!response.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : ''
    if (response.status === 503) {
      throw new Error(`The trip could not be saved on the server${detail ? `: ${detail}` : ''}. Nothing was approved.`)
    }
    throw new Error(
      `${STATUS_MESSAGES[response.status] || `Approval failed (${response.status}).`}${detail ? ` (${detail})` : ''}`
    )
  }

  if (body?.status === 'approved') {
    return { status: 'approved', message: body.message || 'Trip saved successfully.', trip: body.trip ?? null }
  }
  if (body?.status === 'rejected') {
    return { status: 'rejected', message: body.message || 'Trip was not saved.' }
  }
  throw new Error('The server gave an unexpected approval answer. Nothing is shown as approved.')
}
