import { buildTripView, formatDate } from './itineraryAdapter.js'
import { budgetNotes, getVerificationState, summarizeVerification } from './tripResult.js'

// The decision line shown on the dashboard and in the PDF. Backend approval
// and a local draft acceptance are different facts and are never merged.
export function decisionLabel({ backendApproval, draftAcceptedAt } = {}) {
  if (backendApproval?.status === 'approved') return 'Approved - saved to the Rihla Trip Store by the server'
  if (draftAcceptedAt) return 'Accepted by you as a draft (this browser only; not an AI verification)'
  return 'Not decided'
}

// Warnings exactly as the backend reported them (no invented ones).
export function resultWarnings(result) {
  const warnings = [...(result?.summary?.limitations || [])]
  const coverage = result?.coverage
  const uncovered = coverage ? [...(coverage.meal_only_dates || []), ...(coverage.empty_dates || [])].sort() : []
  if (uncovered.length) {
    warnings.push(`Nothing ${coverage.food_focused ? '' : 'beyond meals '}is planned on: ${uncovered.map(formatDate).join(', ')}.`)
  }
  const budget = budgetNotes(result?.budget)
  if (budget?.statusText) warnings.push(budget.statusText)
  warnings.push(...(budget?.priceDoubts || []))
  const verification = summarizeVerification(result?.verification)
  for (const finding of [...(verification?.problems || []), ...(verification?.unverifiable || [])]) {
    warnings.push(`Verification: ${finding.text}`)
  }
  return [...new Set(warnings)]
}

// Everything buildTripPdf needs, from a backend result (live or saved).
export function pdfInputFromResult(result, { sampleLabel = null, marks = {}, now = new Date() } = {}) {
  const verification = getVerificationState(result)
  return {
    view: buildTripView(result.itinerary, result.summary),
    verificationLabel: sampleLabel ? 'Not verified (sample)' : verification.label,
    verificationDetail: verification.detail,
    decisionLabel: decisionLabel(marks),
    warnings: resultWarnings(result),
    sampleLabel,
    generatedAt: now,
  }
}
