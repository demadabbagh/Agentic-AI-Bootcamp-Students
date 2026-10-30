// Statuses for which POST /api/chat returns a freshly generated itinerary.
// See app/main.py: "pending_approval" is only set after trusted verification.
const ITINERARY_STATUSES = new Set(['pending_approval', 'verification_required'])

function incomplete(what) {
  return new Error(
    `The planner returned an incomplete itinerary (${what}). No trip was generated, please try again.`
  )
}

// Throws an Error with a user-facing message unless `result` holds a
// complete, displayable itinerary from the backend.
export function validateTripResult(result) {
  if (!result || typeof result !== 'object') {
    throw new Error('The planner returned an empty response. No trip was generated.')
  }

  const { itinerary, status } = result

  if (!itinerary) {
    throw new Error(result.message || 'The planner could not generate an itinerary for this request.')
  }

  if (!ITINERARY_STATUSES.has(status)) {
    throw new Error(result.message || `The planner returned an unexpected status (${status || 'none'}).`)
  }

  // The backend never sends a low-coverage itinerary; refuse one anyway.
  if (result.coverage && result.coverage.sufficient === false) {
    throw new Error(result.message || 'The itinerary covers too few days of the trip. No trip was generated.')
  }

  if (typeof itinerary.destination !== 'string' || !itinerary.destination.trim()) {
    throw incomplete('missing destination')
  }

  if (!Array.isArray(itinerary.days) || itinerary.days.length === 0) {
    throw incomplete('no days')
  }

  itinerary.days.forEach((day, index) => {
    if (!day || typeof day.date !== 'string' || !day.date) {
      throw incomplete(`day ${index + 1} has no date`)
    }
    if (!Array.isArray(day.activities)) {
      throw incomplete(`day ${index + 1} has no activity list`)
    }
  })

  return result
}

export function isValidTripResult(result) {
  try {
    validateTripResult(result)
    return true
  } catch {
    return false
  }
}

// The backend's answer to a change request (reviseTrip), as the dashboard should treat it.
// - "revised": a complete new plan that passed the same checks as a first plan; it replaces the shown one.
// - "unchanged": anything else (revision_not_supported, needs_clarification, needs_revision, ...); the shown
//   plan stays, with the backend's own message.
// approvalLost is always true: app/main.py clears the session's pending approval on EVERY /api/chat call, so
// the approval offered for the plan shown before the message no longer exists on the server.
export function interpretRevision(response) {
  if (isValidTripResult(response)) {
    return { kind: 'revised', result: response, message: response.message || 'The planner returned an updated plan.', approvalLost: true }
  }
  const status = response && typeof response === 'object' ? response.status || null : null
  const message = response && typeof response.message === 'string' && response.message
    ? response.message
    : 'The planner did not return a changed plan.'
  return { kind: 'unchanged', status, message, approvalLost: true }
}

// Verification state comes only from the backend's verdict: the backend
// issues a save_trip pending_action solely when verification passed.
export function getVerificationState(result) {
  const action = result?.pending_action

  if (
    result?.status === 'pending_approval'
    && action?.type === 'save_trip'
    && action?.verification_status === 'verified'
  ) {
    return {
      level: 'verified',
      label: 'Verified · Awaiting your approval',
      detail: result.message || 'This itinerary passed verification.',
    }
  }

  // Passing every check on recorded (cached) data is not live verification: the server will not approve it.
  if (result?.verification?.state === 'supported_by_records') {
    return {
      level: 'unverified',
      label: 'Not verified · Checked against recorded data only',
      detail: result?.message || 'Every check passed, but only against recorded data, not live sources.',
    }
  }

  return {
    level: 'unverified',
    label: 'Not verified',
    detail: result?.message || 'This itinerary could not be fully verified. Check details before relying on it.',
  }
}

const VERIFICATION_HEADLINES = {
  confirmed_valid: 'Every check passed on live data.',
  supported_by_records:
    'Every check passed, but only against recorded data. The server approves only plans checked against live sources.',
  confirmed_problem: 'Verification found problems that could not be repaired automatically.',
  unverifiable: 'Some facts in this plan could not be checked.',
}

// The backend's verification block (app/orchestrator.py verification_summary): Python's verdict and findings,
// grouped the way the backend labels them. Nothing is added or upgraded here.
export function summarizeVerification(verification) {
  if (!verification || typeof verification !== 'object') return null
  const findings = Array.isArray(verification.findings) ? verification.findings : []
  const group = (state) => findings
    .filter((f) => f && f.state === state && typeof f.explanation === 'string' && f.explanation)
    .map((f) => ({ text: f.explanation, action: f.recommended_action || null, date: f.date || null }))
  return {
    state: verification.state || null,
    headline: VERIFICATION_HEADLINES[verification.state] || 'Verification result received.',
    evidence: verification.evidence_basis || null,
    problems: group('confirmed_problem'),
    unverifiable: group('unverifiable'),
    advice: group('advice'),
    repaired: Boolean(verification.revision?.attempts),
  }
}

// What the Budget Analyst says about its own numbers (planning/budget_analyst.py budget_breakdown + warnings):
// how complete the priced picture is, price ranges and per-person doubts, and its suggestions.
const PRICE_DOUBT = /sources disagree|the listing gives a range|per person or per group/i

export function budgetNotes(budget) {
  if (!budget || typeof budget !== 'object') return null
  const warnings = Array.isArray(budget.warnings) ? budget.warnings.filter((w) => typeof w === 'string') : []
  const statusText = {
    over_budget: 'The known costs alone are over your budget.',
    infeasible: 'Your budget cannot cover the places you asked for.',
    undetermined: 'Whether the plan fits your budget cannot be confirmed: some prices are unknown.',
  }[budget.status] || null
  return {
    confidence: ['low', 'medium', 'high'].includes(budget.confidence) ? budget.confidence : null,
    confidenceBasis: typeof budget.confidence_basis === 'string' ? budget.confidence_basis : null,
    statusText,
    priceDoubts: warnings.filter((w) => PRICE_DOUBT.test(w)),
    suggestions: Array.isArray(budget.suggestions) ? budget.suggestions.filter((s) => typeof s === 'string') : [],
  }
}

// plan_quality from the backend (app/coverage.py DensityReport): advice about
// how full the days are. A light plan is still a valid plan.
export function summarizeDensity(planQuality) {
  if (!planQuality || !Array.isArray(planQuality.days)) return null

  return {
    light: planQuality.level === 'light',
    total: planQuality.total_non_meal ?? 0,
    targetMin: planQuality.target_min ?? null,
    targetMax: planQuality.target_max ?? null,
    lightDays: planQuality.days.filter((d) => d.non_meal < d.target),
  }
}

// estimatedTotal is the sum of KNOWN costs. With unknown costs it is only a
// lower bound, so the remaining budget is an upper bound, never a fact.
export function summarizeBudget(budgetTotal, estimatedTotal, unknownCostCount = 0) {
  const hasBudget = Number.isFinite(budgetTotal)
  const hasEstimate = Number.isFinite(estimatedTotal)
  const costComplete = hasEstimate && unknownCostCount === 0
  const left = hasBudget && hasEstimate ? budgetTotal - estimatedTotal : null
  const overBudget = left !== null && left < 0  // known costs alone already exceed it
  const percentUsed = hasBudget && hasEstimate && budgetTotal > 0
    ? Math.min(100, Math.max(0, (estimatedTotal / budgetTotal) * 100))
    : null

  return {
    budgetTotal: hasBudget ? budgetTotal : null,
    estimatedTotal: hasEstimate ? estimatedTotal : null,
    unknownCostCount,
    costComplete,
    remaining: costComplete && !overBudget ? left : null,
    maxRemaining: !costComplete && !overBudget ? left : null,
    overrun: overBudget ? -left : null,
    percentUsed,
    overBudget,
  }
}
