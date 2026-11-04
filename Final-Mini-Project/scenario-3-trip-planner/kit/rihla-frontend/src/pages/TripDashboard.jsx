import { buildTripView, FALLBACK_IMAGE, formatDate, formatMoney } from '../services/itineraryAdapter'
import {
  approvalBlockedReason, getVerificationState, isValidTripResult, summarizeBudget, summarizeDensity,
} from '../services/tripResult'
import { getSavedTrip, isTripSaved, markApproved, saveTrip } from '../services/savedTrips'
import { approveTrip, rejectTrip, reviseTrip } from '../services/tripApi'
import { ACCEPTANCE_LABELS, acceptanceOptions, approvalOptions, sendAcceptance } from '../services/approval'
import { AuthRequiredError } from '../auth/authApi'
import { useAuth } from '../auth/authContext'
import { applyRevision, revisionBlockedReason } from '../services/tripRevision'
import RefinementPanel from './RefinementPanel'
import WhatIfPanel from './WhatIfPanel'
import { TravelInfoBoundary, TripTravelInfo } from '../features/trip-travel'
import { sourceLinks } from '../services/citations'
import { SAMPLE_HERO_IMAGE, SAMPLE_TRIP } from '../data/sampleTrip'
import { useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import {
  ArrowLeft, ArrowRight, CalendarDays, CheckCircle2, Clock3, ExternalLink, FlaskConical,
  Loader2, MapPin, Send, ShieldAlert, ShieldCheck, Sparkles, TriangleAlert, Wallet,
  Users, Plane, ChevronRight, Download, Bookmark, BookmarkCheck, Footprints, Map as MapIcon, XCircle,
} from 'lucide-react'

const SCOPE_NOTE =
  'Covers only the entry fees and meals priced in this plan. Accommodation, transport, other meals and incidentals are not included. Unknown prices are not counted as free.'

const BADGE_STYLES = {
  verified: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  recorded: 'border-sky-200 bg-sky-50 text-sky-800',
  unverified: 'border-amber-200 bg-amber-50 text-amber-700',
  sample: 'border-slate-300 bg-slate-100 text-slate-700',
}

function Header() {
  const { user } = useAuth() // signed in: their name, not a "Log In" link
  return (
    <header className="border-b border-[#dbe8eb] bg-white/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
        <a href="/" className="flex items-center gap-2 text-2xl font-bold">
          <span className="rounded-xl bg-[#d6eeee] p-2 text-[#2e7b87]">
            <Plane size={21} />
          </span>
          Rihla<span className="text-[#69b7bf]">.</span>
        </a>

        <nav className="hidden gap-8 text-sm font-medium text-[#69808b] md:flex">
          <a href="/" className="hover:text-[#256a77]">Home</a>
          <a href="/trip" className="text-[#256a77]">My Trip</a>
          <a href="#assistant" className="hover:text-[#256a77]">AI Assistant</a>
          <a href="/my-trips" className="hover:text-[#256a77]">My Trips</a>
          {user
            ? <a href="/profile" className="hover:text-[#256a77]">{user.name || user.email || 'My Profile'}</a>
            : <a href="/login" className="hover:text-[#256a77]">Log In</a>}
        </nav>

        <span className="rounded-full bg-[#e8f6f5] px-4 py-2 text-xs font-semibold text-[#327783]">
          <Sparkles size={13} className="mr-1 inline" />
          AI-POWERED TRAVEL
        </span>
      </div>
    </header>
  )
}

function NoTrip() {
  const navigate = useNavigate()

  return (
    <div className="min-h-screen bg-[#edf5f7] text-[#1c3544]">
      <Header />
      <main className="mx-auto max-w-2xl px-6 py-20 text-center">
        <div className="rounded-[28px] border border-[#dce9ec] bg-white p-10 shadow-sm">
          <h1 className="text-2xl font-bold">No trip to show yet</h1>
          <p className="mt-3 text-sm leading-6 text-[#6b8791]">
            There is no generated itinerary on this page. Plan a trip from the home page, or open
            the sample trip to preview the dashboard with demo data.
          </p>
          <div className="mt-7 flex flex-wrap justify-center gap-3">
            <a
              href="/"
              className="inline-flex items-center gap-2 rounded-2xl bg-[#245c6a] px-5 py-3 text-sm font-semibold text-white hover:bg-[#347989]"
            >
              <ArrowLeft size={16} /> Plan a trip
            </a>
            <button
              type="button"
              onClick={() => navigate('/trip', { state: { sample: true } })}
              className="inline-flex items-center gap-2 rounded-2xl border border-[#d5e6e9] bg-white px-5 py-3 text-sm font-semibold text-[#245c6a] hover:bg-[#e7f3f4]"
            >
              <FlaskConical size={16} /> View Sample Trip
            </button>
          </div>
        </div>
      </main>
    </div>
  )
}

export default function TripDashboard() {
  const location = useLocation()
  const navigate = useNavigate()
  const { sessionExpired } = useAuth()
  const trip = location.state || {}
  const aiResult = trip.aiResult
  const isSample = trip.sample === true && !aiResult
  // A labelled sample opened from My Trips ("Load Sample Trips"): a real result shape, but demo data.
  const sampleEntry = trip.sampleTrip === true
  const isReal = isValidTripResult(aiResult)

  const view = isReal ? buildTripView(aiResult.itinerary) : SAMPLE_TRIP
  const destination = view.destination
  const verification = isReal
    ? getVerificationState(aiResult)
    : {
        level: 'sample',
        label: 'Sample trip · Demo data',
        detail: 'Hand-written demo data, not generated by AI and not based on your request.',
      }
  const coverage = isReal ? aiResult.coverage : null
  const uncoveredDates = coverage
    ? [...(coverage.meal_only_dates || []), ...(coverage.empty_dates || [])].sort()
    : []
  const density = isReal ? summarizeDensity(aiResult.plan_quality) : null
  // Python's own summary (app/orchestrator.traveller_summary): how each selected interest is represented by the
  // places actually scheduled, and the plan's limitations. Absent for the sample trip.
  const summary = isReal && aiResult.summary ? aiResult.summary : null
  const coverageByInterest = Object.fromEntries((summary?.interest_coverage || []).map((c) => [c.interest, c]))
  const limitations = summary?.limitations || []
  const sources = isReal ? sourceLinks(aiResult.citations) : [] // the backend's citations only, never invented
  const planDays = isReal ? aiResult.itinerary.days || [] : [] // ISO dates, for a trip opened without its form
  const [detailsOpen, setDetailsOpen] = useState(false) // the status strip's full text: collapsed by default
  const budget = summarizeBudget(view.budgetTotal, view.estimatedTotal, view.unknownCostCount)
  const money = (value) => formatMoney(value, view.currency)
  const totalExperiences = view.days.reduce((total, day) => total + day.activities.length, 0)

  const [activeDay, setActiveDay] = useState(0)
  const tripId = aiResult?.session_id || trip.sessionId || null
  const [saved, setSaved] = useState(() => isReal && isTripSaved(tripId))
  const [saveError, setSaveError] = useState('')

  const handleSave = () => {
    setSaveError('')
    try {
      saveTrip(aiResult, trip)
      setSaved(true)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'The trip could not be saved.')
    }
  }

  // Approval: only for the backend's save_trip action of a plan its approval policy made eligible (verified on
  // live evidence, or every check passed on recorded data, said so). The trip counts as approved only after the
  // backend confirmed it saved the trip in the Rihla Trip Store.
  const [approval, setApproval] = useState(
    () => aiResult?.approval || (isReal ? getSavedTrip(tripId)?.approval : null) || null,
  )
  const [approving, setApproving] = useState(false)
  const [rejected, setRejected] = useState(false)
  const [approveError, setApproveError] = useState('')
  const [approveNote, setApproveNote] = useState('')
  const options = isReal && !sampleEntry ? approvalOptions(aiResult) : { canApprove: false, basis: null, disclosure: null }
  const actionId = options.canApprove ? aiResult.pending_action.action_id : null
  const canApprove = options.canApprove && !approval && !rejected

  const handleReject = async () => {
    if (!canApprove || approving) return
    setApproving(true)
    setApproveError('')
    try {
      const { message: reply } = await rejectTrip(tripId, actionId)
      setRejected(true)
      setApproveNote(reply || 'You rejected this plan. Nothing was saved.')
    } catch (err) {
      if (err instanceof AuthRequiredError) {
        sessionExpired(() => handleReject())
        return
      }
      setApproveError(err instanceof Error ? err.message : 'The rejection could not be sent. Nothing was saved.')
    } finally {
      setApproving(false)
    }
  }

  const handleApprove = async () => {
    if (!canApprove || approving) return // one approval at a time
    setApproving(true)
    setApproveError('')
    try {
      const { trip: savedTrip, alreadyApproved } = await approveTrip(tripId, actionId)
      // basis: what the approval relied on (live | recorded), kept for the PDF and the dashboard
      const local = { trip_id: savedTrip.trip_id, approvedAt: new Date().toISOString(), basis: options.basis }
      try {
        markApproved(aiResult, trip, { ...savedTrip, basis: options.basis })
        setSaved(true)
      } catch {
        // the backend saved it; only the local copy failed (storage blocked): My Trips still lists it
      }
      setApproval(local)
      setApproveNote(alreadyApproved
        ? `This trip was already approved and saved. Trip ID: ${savedTrip.trip_id}.`
        : `Approved and saved to My Trips. Trip ID: ${savedTrip.trip_id}.`)
    } catch (err) {
      if (err instanceof AuthRequiredError) { // the session ended: log in again, then approve once more
        sessionExpired(() => handleApprove())
        return
      }
      setApproveError(err instanceof Error ? err.message : 'The trip could not be approved.')
    } finally {
      setApproving(false)
    }
  }

  // A plan that is not eligible for approval (some fact could not be confirmed, or the verifier found a problem)
  // can still be kept, as the traveller's own decision: a confirmation lists what is missing, an explicit
  // acknowledgment is required, and POST /api/accept saves it with that status. It is never shown as approved.
  const accept = isReal && !sampleEntry ? acceptanceOptions(aiResult) : { canAccept: false }
  const canAccept = accept.canAccept && !approval
  const [acceptOpen, setAcceptOpen] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)

  const openAccept = () => {
    if (!canAccept || approving) return
    setApproveError('')
    setApproveNote('')
    setAcknowledged(false)
    setAcceptOpen(true)
  }

  const cancelAccept = () => {
    if (approving) return
    setAcceptOpen(false)
    setAcknowledged(false)
    setApproveNote('Nothing was saved. You can accept this itinerary later, or ask for a change.')
  }

  const handleAccept = async () => {
    if (!canAccept || !acknowledged || approving) return // one save at a time, only after the acknowledgment
    setApproving(true)
    setApproveError('')
    try {
      const out = await sendAcceptance({ sessionId: tripId, acceptanceId: accept.acceptanceId, mode: accept.mode,
        acknowledged: true })
      const local = { trip_id: out.tripId, approvedAt: new Date().toISOString(), mode: out.mode }
      try {
        markApproved(aiResult, trip, { trip_id: out.tripId, mode: out.mode })
        setSaved(true)
      } catch {
        // the backend saved it; only the local copy failed (storage blocked): My Trips still lists it
      }
      setApproval(local)
      setAcceptOpen(false)
      setApproveNote(`${out.alreadySaved ? 'This trip was already saved' : 'Saved to My Trips'} as `
        + `"${ACCEPTANCE_LABELS[out.mode]}". Trip ID: ${out.tripId}.`)
    } catch (err) {
      if (err instanceof AuthRequiredError) { // the session ended: log in again, then confirm once more
        sessionExpired(() => setAcceptOpen(true))
        return
      }
      setApproveError(err instanceof Error ? err.message : 'The trip could not be saved. Nothing was saved.')
    } finally {
      setApproving(false)
    }
  }

  const [pdfError, setPdfError] = useState('')
  const handlePdf = async () => {
    setPdfError('')
    try {
      // loaded on first use: the PDF library is not part of the page's own bundle
      const { downloadItineraryPdf } = await import('../services/pdfExport')
      downloadItineraryPdf({ aiResult, request: trip, approval })
    } catch (err) {
      setPdfError(err instanceof Error ? `The PDF could not be created: ${err.message}` : 'The PDF could not be created.')
    }
  }
  // Change requests: sent to the backend, which revises the plan it keeps for this session and verifies it
  // again. Only a revised itinerary replaces the one on screen; anything else leaves it as it is.
  const revisionBlocked = revisionBlockedReason(aiResult, { sample: isSample || sampleEntry,
    approved: Boolean(approval) && !approval.mode, saved: Boolean(approval?.mode) })
  const [message, setMessage] = useState('')
  const [revising, setRevising] = useState(false)
  const [messages, setMessages] = useState(() => [
    {
      role: 'assistant',
      text: isSample
        ? 'This is a sample Istanbul trip for previewing the dashboard. It was not planned for you.'
        : `Your ${destination} itinerary is ready to review. Check the verification status and any warnings before relying on it.`
          + (revisionBlocked ? '' : ' You can ask me for changes, for example "Reduce my budget by 20%" or "Remove the museum on Day 2".'),
    },
  ])
  const chatEnd = useRef(null)
  useEffect(() => {
    chatEnd.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }, [messages, revising])

  if (!isReal && !isSample) return <NoTrip />

  const day = view.days[Math.min(activeDay, view.days.length - 1)]

  const sendMessage = async (event) => {
    event.preventDefault()
    const text = message.trim()
    if (!text || revising) return // one change at a time
    setMessage('')
    if (revisionBlocked) {
      setMessages((previous) => [...previous, { role: 'user', text }, { role: 'assistant', text: revisionBlocked }])
      return
    }

    setMessages((previous) => [...previous, { role: 'user', text }])
    await runRevision(text)
  }

  // The revised itinerary replaces the old one everywhere on the page (and survives a refresh). Its approval is the
  // backend's new one, or none: the previous version's approval no longer applies.
  const showRevised = (result) => {
    const request = { ...trip, aiResult: result, budget: result.itinerary.budget_total ?? trip.budget }
    navigate('/trip', { replace: true, state: request })
    setApproval(null)
    setRejected(false)
    setAcceptOpen(false)
    setApproveNote('')
    setApproveError('')
    setPdfError('')
    setActiveDay((current) => Math.min(current, result.itinerary.days.length - 1))
    if (saved) {
      try {
        saveTrip(result, request) // the draft in this browser is the latest version too
      } catch (err) {
        setSaveError(err instanceof Error ? err.message : 'The saved draft could not be updated.')
      }
    }
  }

  // An applied WHAT IF (WhatIfPanel): the traveller reviewed the proposal and chose it.
  const onWhatIfApplied = (response, info) => {
    const next = applyRevision(aiResult, response)
    if (!next.result) return
    showRevised(next.result)
    setMessages((previous) => [...previous, {
      role: 'assistant', text: `Applied the WHAT IF: ${info?.title || 'a hypothetical change'} ${next.reply}`,
      changes: next.changes, moreChanges: next.moreChanges, outcome: 'revised',
    }])
  }

  // The AI review's refined plan (RefinementPanel): the traveller chose it, and it is a new version of the plan.
  const onReviewApplied = (response) => {
    const next = applyRevision(aiResult, response)
    if (!next.result) {
      setMessages((previous) => [...previous, { role: 'assistant', text: next.reply, outcome: next.outcome }])
      return
    }
    showRevised(next.result)
    setMessages((previous) => [...previous, {
      role: 'assistant', text: `Applied the AI review's refined plan. ${next.reply}`,
      changes: next.changes, moreChanges: next.moreChanges, outcome: 'revised',
    }])
  }

  const runRevision = async (text) => {
    setRevising(true)
    try {
      const response = await reviseTrip(tripId, text, aiResult.plan_version)
      const next = applyRevision(aiResult, response)
      if (next.result) showRevised(next.result)
      setMessages((previous) => [...previous, {
        role: 'assistant', text: next.reply, changes: next.changes, moreChanges: next.moreChanges,
        outcome: next.outcome,
      }])
    } catch (err) {
      if (err instanceof AuthRequiredError) { // the session ended: log in again, then send the same change once
        sessionExpired(() => runRevision(text))
        return
      }
      setMessages((previous) => [...previous, {
        role: 'assistant', error: true,
        text: err instanceof Error ? err.message : 'The change failed. Your itinerary was not changed.',
      }])
    } finally {
      setRevising(false)
    }
  }

  const BadgeIcon = verification.level === 'verified' ? ShieldCheck : verification.level === 'sample' ? FlaskConical : ShieldAlert

  return (
    <div className="min-h-screen bg-[#edf5f7] text-[#1c3544]">
      <Header />

      {isSample && (
        <div className="border-b border-slate-300 bg-slate-800 px-6 py-3 text-center text-sm font-semibold text-white">
          <FlaskConical size={16} className="mr-2 inline" />
          SAMPLE TRIP: demo data only. Not AI-generated and not based on your request.
        </div>
      )}
      {sampleEntry && (
        <div className="border-b border-violet-300 bg-violet-800 px-6 py-3 text-center text-sm font-semibold text-white">
          <FlaskConical size={16} className="mr-2 inline" />
          SAMPLE TRIP: {trip.sampleLabel || 'demo data'}. It cannot be approved or changed.
        </div>
      )}

      {/* USER TRIP PREFERENCES: what the traveller asked for (real trips only) */}
      {isReal && (
        <section className="mx-auto max-w-7xl px-6 pt-8">
          <div className="rounded-[28px] border border-[#dce9ec] bg-white p-6 shadow-sm md:p-8">
            <div className="mb-5 flex items-center gap-2 text-[#347989]">
              <Sparkles size={20} />
              <h2 className="text-xl font-bold">What you asked for</h2>
            </div>

            <div className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <p className="text-[#8297a0]">Destination</p>
                <p className="mt-1 font-semibold">{trip.destination || view.destination || 'Not selected'}</p>
              </div>
              <div>
                <p className="text-[#8297a0]">Travel dates</p>
                <p className="mt-1 font-semibold">
                  {trip.departureDate && trip.returnDate
                    ? `${trip.departureDate} to ${trip.returnDate}`
                    : view.startDate ? `${view.startDate} to ${view.endDate}` : 'Not selected'}
                </p>
              </div>
              <div>
                <p className="text-[#8297a0]">Travellers</p>
                <p className="mt-1 font-semibold">{trip.travellers || view.travellers || 'Not selected'}</p>
              </div>
              <div>
                <p className="text-[#8297a0]">Budget</p>
                <p className="mt-1 font-semibold">
                  {trip.budget ? `$${trip.budget}` : Number.isFinite(view.budgetTotal) ? money(view.budgetTotal) : 'Not selected'}
                </p>
              </div>
            </div>

            <div className="mt-6 border-t border-[#edf2f3] pt-5">
              <p className="mb-3 text-sm font-semibold">Your interests</p>
              {trip.interests?.length ? (
                <div className="flex flex-wrap gap-2">
                  {trip.interests.map((interest) => {
                    const covered = coverageByInterest[interest]
                    return (
                      <span
                        key={interest}
                        title={covered?.places?.length ? covered.places.join(', ') : undefined}
                        className={`rounded-full px-4 py-2 text-xs font-semibold ${
                          covered && covered.scheduled === 0
                            ? 'bg-amber-50 text-amber-800'
                            : 'bg-[#e5f4f4] text-[#347989]'
                        }`}
                      >
                        {interest}
                        {covered && ` · ${covered.scheduled} ${covered.scheduled === 1 ? 'place' : 'places'}`}
                      </span>
                    )
                  })}
                </div>
              ) : (
                <p className="text-sm text-[#8297a0]">No interests selected.</p>
              )}

              {trip.customPreferences && (
                <div className="mt-5 rounded-2xl bg-[#f0f7f8] p-4">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[#79afb6]">
                    Anything else you'd love to experience
                  </p>
                  <p className="whitespace-pre-wrap break-words text-sm leading-6 text-[#456774]">
                    {trip.customPreferences}
                  </p>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      <main className="mx-auto max-w-7xl px-6 py-9">
        <a href="/" className="mb-7 inline-flex items-center gap-2 text-sm font-medium text-[#6b8791] hover:text-[#2d7783]">
          <ArrowLeft size={16} /> Back to explore
        </a>

        <div className="mb-8 flex flex-wrap items-end justify-between gap-5">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.2em] text-[#60a6b0]">
              <Sparkles size={15} /> {isSample ? 'SAMPLE ITINERARY' : 'YOUR GENERATED ITINERARY'}
            </div>
            <h1 className="text-4xl font-bold tracking-tight md:text-5xl">
              Your {destination} <span className="font-light italic text-[#65aeb6]">escape.</span>
            </h1>
            <p className="mt-3 text-[#7a909b]">
              {view.days.length} {view.days.length === 1 ? 'day' : 'days'}, {totalExperiences}{' '}
              {totalExperiences === 1 ? 'planned activity' : 'planned activities'}.
            </p>
          </div>

          <span className={`flex items-center gap-2 rounded-full border px-5 py-3 text-sm font-semibold ${BADGE_STYLES[verification.level]}`}>
            <BadgeIcon size={16} /> {verification.label}
          </span>
        </div>

        {/* STATUS AND LIMITATIONS FROM THE BACKEND: one line, the full text one click away (nothing is left out) */}
        {isReal && (
          <details
            open={detailsOpen}
            onToggle={(e) => setDetailsOpen(e.currentTarget.open)}
            className={`mb-6 rounded-[18px] border text-sm ${BADGE_STYLES[verification.level]}`}
          >
            <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 px-4 py-3">
              <BadgeIcon size={15} aria-hidden="true" />
              <span role="status" className="font-semibold">{verification.label}</span>
              {limitations.length > 0 && (
                <span>· {limitations.length} {limitations.length === 1 ? 'limitation' : 'limitations'}</span>
              )}
              <span className="ml-auto text-xs font-semibold underline underline-offset-2">
                {detailsOpen ? 'Hide details' : 'Show details'}
              </span>
            </summary>
            <div className="border-t border-current/20 px-4 pb-4 pt-3 leading-6">
              <p>{verification.detail}</p>
              {limitations.length > 0 && (
                <>
                  <p className="mt-3 flex items-center gap-2 font-semibold">
                    <TriangleAlert size={15} aria-hidden="true" /> What this plan does not cover
                  </p>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {limitations.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </details>
        )}

        {coverage && uncoveredDates.length > 0 && (
          <section className="mb-6 rounded-[22px] border border-amber-200 bg-amber-50 p-5 text-sm leading-6 text-amber-800">
            <p className="flex items-center gap-2 font-semibold">
              <TriangleAlert size={16} /> Coverage: {coverage.covered_dates?.length ?? 0} of {coverage.trip_days} days
              have {coverage.food_focused ? 'planned activities' : 'sightseeing or other non-meal activities'}
            </p>
            <p className="mt-1">
              Nothing {coverage.food_focused ? '' : 'beyond meals '}is planned on:{' '}
              {uncoveredDates.map((d) => formatDate(d)).join(', ')}.
            </p>
          </section>
        )}

        {density?.light && (
          <section className="mb-6 rounded-[22px] border border-sky-200 bg-sky-50 p-5 text-sm leading-6 text-sky-900">
            <p className="flex items-center gap-2 font-semibold">
              <TriangleAlert size={16} /> Light itinerary: {density.total} sightseeing{' '}
              {density.total === 1 ? 'activity' : 'activities'}
              {density.targetMin !== null && ` (a full plan has about ${density.targetMin} to ${density.targetMax})`}
            </p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {density.lightDays.map((d) => (
                <li key={d.date} className="rounded-full bg-white px-3 py-1 text-xs font-medium">
                  {formatDate(d.date)}: {d.non_meal} of {d.target}
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="mb-6 flex flex-wrap items-center gap-3">
          {isReal && (
            <button
              type="button"
              onClick={handleSave}
              disabled={saved}
              title="Saved in this browser only, not to an account"
              className="inline-flex items-center gap-2 rounded-2xl bg-[#245c6a] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[#347989] disabled:cursor-default disabled:bg-emerald-600"
            >
              {saved ? <BookmarkCheck size={17} /> : <Bookmark size={17} />}
              {saved
                ? (approval ? 'Saved in My Trips' : 'Saved as a draft in this browser')
                : (verification.level === 'verified' ? 'Save as a draft' : 'Save as a draft (not verified)')}
            </button>
          )}
          <a
            href="/my-trips"
            className="inline-flex items-center gap-2 rounded-2xl border border-[#d5e6e9] bg-white px-5 py-3 text-sm font-semibold text-[#245c6a] transition hover:bg-[#e7f3f4]"
          >
            <Bookmark size={17} /> My Trips
          </a>
          {isReal && (
            <button
              type="button"
              onClick={handlePdf}
              title={approval || verification.level === 'verified' ? 'Download this itinerary as a PDF'
                : 'Download a draft PDF, marked "Draft - Not Verified"'}
              className="inline-flex items-center gap-2 rounded-2xl border border-[#d5e6e9] bg-white px-5 py-3 text-sm font-semibold text-[#245c6a] transition hover:bg-[#e7f3f4]"
            >
              <Download size={17} />
              {approval || verification.level === 'verified' ? 'Download PDF' : 'Download draft PDF'}
            </button>
          )}
          {saveError && (
            <p role="alert" className="w-full text-sm text-red-700">{saveError}</p>
          )}
          {pdfError && (
            <p role="alert" className="w-full text-sm text-red-700">{pdfError}</p>
          )}
          {isReal && !saveError && !approval && (
            <p className="w-full text-xs text-[#8297a0]">
              A draft stays in this browser on this device only. Approving a verified trip, or accepting one with
              its limitations, saves it to your trips in the Rihla Trip Store.
            </p>
          )}
        </div>

        <div className="relative mb-8 overflow-hidden rounded-[30px] bg-[#183b4b]">
          <div
            className="absolute inset-0 bg-cover bg-center opacity-55"
            style={{
              backgroundImage: `linear-gradient(90deg, #123443 5%, transparent 100%), url('${
                isSample ? SAMPLE_HERO_IMAGE : FALLBACK_IMAGE.replace('w=500', 'w=1600')
              }')`,
            }}
          />
          <div className="relative flex min-h-[240px] flex-col justify-end p-8 text-white md:p-10">
            <span className="mb-3 flex items-center gap-2 text-sm text-[#c5e9e8]">
              <MapPin size={17} /> {destination.toUpperCase()}
            </span>
            <h2 className="mb-5 text-3xl font-semibold">
              {isSample ? 'A sample travel adventure.' : 'Your personalized travel adventure.'}
            </h2>
            <div className="flex flex-wrap gap-5 text-sm text-white/85">
              <span className="flex items-center gap-2">
                <CalendarDays size={17} /> {formatDate(view.startDate) || 'Start date'} - {formatDate(view.endDate) || 'End date'}
              </span>
              <span className="flex items-center gap-2">
                <Users size={17} /> {view.travellers ?? 'Not specified'} {view.travellers === 1 ? 'traveller' : 'travellers'}
              </span>
              <span className="flex items-center gap-2">
                <Wallet size={17} /> {money(budget.budgetTotal)} budget
              </span>
            </div>
          </div>
        </div>

        <div className="grid gap-7 lg:grid-cols-[1fr_340px]">
          <div>
            <section className="rounded-[28px] border border-[#e0ebed] bg-white p-6 shadow-sm md:p-8">
              <div className="mb-7 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="mb-2 text-xs font-bold uppercase tracking-[0.18em] text-[#79afb6]">
                    YOUR ADVENTURE
                  </p>
                  <h2 className="text-2xl font-bold">Day-by-day itinerary</h2>
                </div>
                <span className="rounded-full bg-[#e9f5f5] px-4 py-2 text-xs font-semibold text-[#4e8e98]">
                  {view.days.length} DAYS - {totalExperiences} EXPERIENCES
                </span>
              </div>

              <div className="mb-8 flex flex-wrap gap-3">
                {view.days.map((d, index) => (
                  <button
                    key={d.label}
                    onClick={() => setActiveDay(index)}
                    className={`rounded-2xl px-6 py-3 text-left transition ${
                      activeDay === index
                        ? 'bg-[#245c6a] text-white shadow-md'
                        : 'bg-[#f1f7f8] text-[#66828d] hover:bg-[#e1eff1]'
                    }`}
                  >
                    <span className="block text-sm font-bold">{d.label}</span>
                    <span className="text-xs opacity-75">{d.date}</span>
                  </button>
                ))}
              </div>

              <div className="mb-7">
                <h3 className="text-xl font-bold">{day.title}</h3>
                <p className="mt-1 text-sm text-[#8297a0]">
                  {day.activities.length
                    ? `${day.activities.length} planned ${day.activities.length === 1 ? 'activity' : 'activities'}.`
                    : 'Nothing is planned for this day.'}
                </p>
                {isReal && day.activities.length > 0 && !day.activities.some((a) => a.travel) && (
                  <p className="mt-1 text-xs text-[#8297a0]">
                    Transportation information unavailable: travel times between stops were not provided.
                  </p>
                )}
              </div>

              <div className="space-y-4">
                {day.activities.map((activity, index) => (
                  <article
                    key={`${activity.name}-${index}`}
                    className="flex flex-col gap-5 rounded-[22px] border border-[#e7eff1] bg-[#fbfdfd] p-4 transition hover:border-[#b8d9dd] hover:shadow-md sm:flex-row"
                  >
                    <img
                      src={activity.image}
                      alt={activity.name}
                      className="h-44 w-full rounded-2xl object-cover sm:h-32 sm:w-36"
                    />
                    <div className="flex flex-1 flex-col justify-center">
                      <span className="mb-2 flex items-center gap-2 text-xs font-semibold text-[#70a7b0]">
                        <Clock3 size={14} /> {activity.time}
                      </span>
                      <h4 className="text-lg font-bold">{activity.name}</h4>
                      <p className="mt-1 text-sm text-[#8196a0]">{activity.type}</p>
                      {activity.notes && (
                        <p className="mt-2 text-xs leading-5 text-[#7a909b]">{activity.notes}</p>
                      )}
                      <div className="mt-4 flex flex-wrap items-center gap-4 text-xs font-medium text-[#68818b]">
                        <span>{activity.duration}{activity.durationIsEstimate && activity.duration !== 'Flexible duration' ? ' (estimate)' : ''}</span>
                        <span
                          className={`rounded-full px-3 py-1 ${
                            activity.priceUnknown || activity.price === 'Price unknown'
                              ? 'bg-amber-50 text-amber-700'
                              : 'bg-[#eaf5f4] text-[#3c8992]'
                          }`}
                        >
                          {activity.price}
                        </span>
                        {activity.sourceUrl && (
                          <a
                            href={activity.sourceUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-[#3c8992] hover:underline"
                          >
                            {/google\.[a-z.]+\/maps|maps\.google\./.test(activity.sourceUrl) ? 'Google Maps' : 'Source'}{' '}
                            <ExternalLink size={12} />
                          </a>
                        )}
                        {activity.mapUrl && (
                          <a
                            href={activity.mapUrl}
                            target="_blank"
                            rel="noreferrer"
                            title="Searches OpenStreetMap for this name; not a verified location or route"
                            className="inline-flex items-center gap-1 text-[#3c8992] hover:underline"
                          >
                            <MapIcon size={12} /> Find on map
                          </a>
                        )}
                      </div>
                      {activity.travel && (
                        <p className="mt-2 flex items-center gap-1 text-xs text-[#68818b]">
                          <Footprints size={12} /> {activity.travel}
                        </p>
                      )}
                    </div>
                    <ChevronRight className="hidden self-center text-[#9bb9c0] sm:block" size={20} />
                  </article>
                ))}
              </div>

              <div className="mt-7 flex items-center justify-between">
                <button
                  disabled={activeDay === 0}
                  onClick={() => setActiveDay((d) => d - 1)}
                  className="flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-[#547a86] disabled:opacity-30"
                >
                  <ArrowLeft size={16} /> Previous day
                </button>
                <button
                  disabled={activeDay === view.days.length - 1}
                  onClick={() => setActiveDay((d) => d + 1)}
                  className="flex items-center gap-2 rounded-xl bg-[#245c6a] px-5 py-3 text-sm font-semibold text-white disabled:opacity-30"
                >
                  Next day <ArrowRight size={16} />
                </button>
              </div>
            </section>

            {/* Weather and optional flight prices (features/trip-travel): for a generated trip only, never the
                sample. FROM and TO come from the home page's choices (trip.originPlace, trip.destinationPlace);
                a trip saved before they existed shows the typed destination and no forecast. Nothing here
                changes the itinerary or its budget. */}
            {isReal && !sampleEntry && (
              <div className="mt-7">
                <TravelInfoBoundary>
                <TripTravelInfo
                  destination={trip.destinationPlace || null}
                  destinationText={trip.destination || view.destination}
                  origin={trip.originPlace || null}
                  startDate={trip.departureDate || planDays[0]?.date || ''}
                  endDate={trip.returnDate || planDays[planDays.length - 1]?.date || ''}
                  travellers={Number(trip.travellers) || 1}
                />
                </TravelInfoBoundary>
              </div>
            )}
          </div>

          <aside className="space-y-6">
            <section className="rounded-[28px] border border-[#e0ebed] bg-white p-6 shadow-sm">
              <p className="mb-2 text-xs font-bold uppercase tracking-[0.18em] text-[#79afb6]">
                YOUR TRIP AT A GLANCE
              </p>
              <h2 className="mb-6 text-xl font-bold">Budget overview</h2>

              <div className="mb-5 rounded-2xl bg-[#eaf5f5] p-5">
                <p className="text-sm text-[#64848d]">Your total budget</p>
                <p className="mt-1 text-3xl font-bold text-[#245c6a]">{money(budget.budgetTotal)}</p>
              </div>

              <div className="space-y-4 text-sm">
                <div className="flex justify-between gap-3">
                  <span className="text-[#8297a0]">Estimated planned costs</span>
                  <strong className="text-right">
                    {budget.estimatedTotal === null ? 'Unknown' : money(budget.estimatedTotal)}
                    {budget.unknownCostCount > 0 && (
                      <span className="block text-xs font-medium text-amber-700">
                        + {budget.unknownCostCount} unknown {budget.unknownCostCount === 1 ? 'price' : 'prices'}
                      </span>
                    )}
                  </strong>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-[#8297a0]">
                    Budget left after priced fees and meals
                    <span className="block text-xs">before hotels, flights and transport</span>
                  </span>
                  {budget.overBudget ? (
                    <strong className="text-right text-red-700">Over by {money(budget.overrun)}</strong>
                  ) : budget.remaining !== null ? (
                    <strong className="text-[#368b84]">{money(budget.remaining)}</strong>
                  ) : budget.maxRemaining !== null ? (
                    <strong className="text-right text-amber-700">
                      At most {money(budget.maxRemaining)}
                      <span className="block text-xs font-medium">some prices unknown</span>
                    </strong>
                  ) : (
                    <strong className="text-[#8297a0]">Cannot be confirmed</strong>
                  )}
                </div>
              </div>

              {budget.percentUsed !== null && (
                <div className="mt-6 h-2 overflow-hidden rounded-full bg-[#e6f1f2]">
                  <div
                    className={`h-full rounded-full ${budget.overBudget ? 'bg-red-500' : 'bg-[#67b7b7]'}`}
                    style={{ width: `${Math.max(budget.percentUsed, 1)}%` }}
                  />
                </div>
              )}
              <p className="mt-3 text-xs leading-5 text-[#91a3ab]">
                {isSample ? 'Sample figures only, not real estimates.' : SCOPE_NOTE}
              </p>
            </section>

            {/* The sources the backend cited for this itinerary (aiResult.citations); no list when it sent none. */}
            {sources.length > 0 && (
              <section aria-labelledby="sources-title" className="rounded-[28px] border border-[#e0ebed] bg-white p-6 shadow-sm">
                <h2 id="sources-title" className="mb-1 text-xl font-bold">Sources</h2>
                <p className="mb-4 text-xs text-[#8297a0]">Where the places and prices in this plan come from.</p>
                <ol className="space-y-2 text-sm">
                  {sources.map((source) => (
                    <li key={source.id} className="min-w-0">
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-start gap-2 rounded-xl px-2 py-1.5 text-[#2e7b87] hover:bg-[#eef6f7]"
                      >
                        <ExternalLink size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                        <span className="min-w-0">
                          <span className="block truncate font-medium">{source.title}</span>
                          {source.title !== source.host && (
                            <span className="block truncate text-xs text-[#8297a0]">{source.host}</span>
                          )}
                        </span>
                      </a>
                    </li>
                  ))}
                </ol>
              </section>
            )}

            <section
              id="assistant"
              className="overflow-hidden rounded-[28px] border border-[#d7e8ea] bg-white shadow-sm"
            >
              <div className="bg-[#245c6a] p-6 text-white">
                <div className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wider text-[#b6e3e3]">
                  <Sparkles size={15} /> YOUR AI COMPANION
                </div>
                <h2 className="text-xl font-bold">Let's make it yours.</h2>
                <p className="mt-2 text-sm text-white/75">
                  Ask Rihla to personalize your journey.
                </p>
              </div>

              <div className="flex h-64 flex-col gap-3 overflow-y-auto p-5">
                {messages.map((item, index) => (
                  <div
                    key={index}
                    role={item.error ? 'alert' : undefined}
                    className={`max-w-[95%] rounded-2xl p-3 text-sm leading-6 ${
                      item.role === 'user'
                        ? 'self-end bg-[#245c6a] text-white'
                        : item.error
                          ? 'self-start bg-red-50 text-red-700'
                          : 'self-start bg-[#edf5f6] text-[#476774]'
                    }`}
                  >
                    {item.text}
                    {item.changes?.length > 0 && (
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5">
                        {item.changes.map((change) => <li key={change}>{change}</li>)}
                        {item.moreChanges > 0 && <li>and {item.moreChanges} more</li>}
                      </ul>
                    )}
                  </div>
                ))}
                {revising && (
                  <div role="status" className="flex items-center gap-2 self-start rounded-2xl bg-[#edf5f6] p-3 text-sm text-[#476774]">
                    <Loader2 size={15} className="animate-spin" /> Updating your itinerary...
                  </div>
                )}
                <div ref={chatEnd} />
              </div>

              <form onSubmit={sendMessage} className="flex gap-2 border-t border-[#edf2f3] p-4">
                <input
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder={revising ? 'Updating your itinerary...' : 'Ask for a change...'}
                  disabled={revising}
                  maxLength={1000}
                  className="min-w-0 flex-1 rounded-xl bg-[#f1f7f8] px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-[#a9d7dc] disabled:opacity-60"
                />
                <button
                  type="submit"
                  aria-label="Send message"
                  disabled={revising || !message.trim()}
                  aria-busy={revising}
                  className="rounded-xl bg-[#245c6a] p-3 text-white transition hover:bg-[#347989] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {revising ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
                </button>
              </form>
              {revisionBlocked && !isSample && (
                <p className="px-4 pb-4 text-xs leading-5 text-[#91a3ab]">{revisionBlocked}</p>
              )}
            </section>

            {isReal && !isSample && aiResult.refinement && !approval && !rejected && (
              <RefinementPanel key={`review-${aiResult.plan_version}`} sessionId={tripId}
                planVersion={aiResult.plan_version} initial={aiResult.refinement}
                onApplied={onReviewApplied} onSessionExpired={sessionExpired} />
            )}

            {isReal && !isSample && (
              <WhatIfPanel key={aiResult.plan_version || 'no-plan'} aiResult={aiResult} sessionId={tripId}
                blockedReason={revisionBlocked}
                onApplied={onWhatIfApplied} />
            )}

            <section className="rounded-[28px] border border-[#e0ebed] bg-white p-6 shadow-sm">
              <h2 className="mb-2 text-lg font-bold">Ready for your adventure?</h2>
              <p className="mb-5 text-sm leading-6 text-[#8297a0]">
                {approval?.mode
                  ? `Saved in My Trips as "${ACCEPTANCE_LABELS[approval.mode] || 'Saved · Not Fully Verified'}": you accepted it with its limitations. It is not fully verified.`
                  : approval
                    ? 'You approved this itinerary and it is saved in your trips.'
                    : rejected
                      ? 'You rejected this itinerary. Nothing was saved. Ask for a change, or plan another trip.'
                      : sampleEntry || verification.level === 'sample'
                        ? 'A sample trip cannot be approved.'
                        : options.canApprove && options.basis === 'live'
                          ? 'This itinerary passed verification on live data. Approve it to save it to My Trips. Rihla never books or pays for anything.'
                          : options.canApprove
                            ? 'Every check passed, on recorded data. Approve it to save it to My Trips, or reject it. Rihla never books or pays for anything.'
                            : canAccept && accept.mode === 'unverified_draft'
                              ? 'The verifier found problems it could not repair, so this itinerary cannot be approved. You can still save it as an unverified draft, with those problems disclosed.'
                              : canAccept
                                ? 'Some information could not be confirmed (such as prices, opening hours or travel times), so this itinerary cannot be approved as verified. You can still accept it and save it to My Trips with its limitations.'
                                : verification.level === 'verified'
                                  ? 'This itinerary passed verification, but there is no approval waiting for it. Plan the trip again to approve it.'
                                  : approvalBlockedReason(aiResult)}
              </p>
              {canApprove && options.basis === 'recorded' && (
                <p className="mb-4 rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs leading-5 text-sky-900">
                  <strong>Recorded data, not live confirmation.</strong>{' '}
                  {options.disclosure || 'Opening hours, prices and travel times were not confirmed today.'} Check
                  them before you go.
                </p>
              )}
              {canAccept || approval?.mode ? (
                <button
                  type="button"
                  onClick={openAccept}
                  disabled={!canAccept || approving}
                  aria-busy={approving}
                  aria-haspopup="dialog"
                  className={`flex w-full items-center justify-center gap-2 rounded-2xl px-5 py-4 text-sm font-bold transition ${
                    canAccept && !approving
                      ? 'bg-amber-600 text-white hover:bg-amber-700'
                      : 'cursor-default border border-amber-300 bg-amber-50 text-amber-800'
                  }`}
                >
                  {approval?.mode ? <BookmarkCheck size={18} /> : <ShieldAlert size={18} />}
                  {approval?.mode ? ACCEPTANCE_LABELS[approval.mode] : approving ? 'Saving…' : accept.button}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleApprove}
                  disabled={!canApprove || approving}
                  aria-busy={approving}
                  className={`flex w-full items-center justify-center gap-2 rounded-2xl px-5 py-4 text-sm font-bold transition ${
                    canApprove && !approving
                      ? 'bg-[#245c6a] text-white hover:bg-[#347989]'
                      : approval
                        ? 'cursor-default bg-emerald-600 text-white'
                        : 'cursor-not-allowed bg-[#dce9eb] text-[#819ba3]'
                  }`}
                >
                  <CheckCircle2 size={18} />
                  {approval ? 'Approved' : approving ? 'Sending…' : 'Approve itinerary'}
                </button>
              )}
              {canApprove && (
                <button
                  type="button"
                  onClick={handleReject}
                  disabled={approving}
                  aria-label="Reject itinerary"
                  className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl border border-[#e3cfcf] px-5 py-3 text-sm font-semibold text-[#a35c5c] transition hover:bg-red-50 disabled:opacity-60"
                >
                  <XCircle size={17} /> Reject
                </button>
              )}
              {approveNote && (
                <p role="status" className={`mt-3 rounded-xl p-3 text-center text-sm font-semibold ${
                  rejected || (!approval && canAccept) ? 'bg-slate-100 text-slate-700'
                    : approval?.mode ? 'bg-amber-50 text-amber-800' : 'bg-emerald-50 text-emerald-700'}`}>
                  {approveNote}
                </p>
              )}
              {approval && !approveNote && (
                <p className={`mt-3 text-center text-xs ${approval.mode ? 'text-amber-800' : 'text-emerald-700'}`}>
                  Saved trip ID: {approval.trip_id}
                </p>
              )}
              {approveError && (
                <p role="alert" className="mt-3 rounded-xl bg-red-50 p-3 text-center text-sm text-red-700">
                  {approveError}
                </p>
              )}
              {!canApprove && !canAccept && !approval && !rejected && !approveError && (
                <p className="mt-3 text-center text-xs text-[#91a3ab]">Approval unavailable</p>
              )}
            </section>

            {acceptOpen && canAccept && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#102f3b]/50 px-4 py-6"
                onKeyDown={(e) => { if (e.key === 'Escape') cancelAccept() }}>
                <div role="dialog" aria-modal="true" aria-labelledby="accept-title"
                  className="max-h-full w-full max-w-lg overflow-y-auto rounded-[24px] bg-white p-6 shadow-xl">
                  <h2 id="accept-title" className="mb-1 flex items-center gap-2 text-lg font-bold text-[#1c3544]">
                    <ShieldAlert size={20} className="text-amber-600" />
                    {accept.mode === 'unverified_draft' ? 'Save an unverified draft?' : 'Accept with limitations?'}
                  </h2>
                  <p className="mb-4 text-sm leading-6 text-[#62808a]">
                    This itinerary is <strong>not fully verified</strong>. It will be saved to My Trips as
                    {' '}<strong>"{accept.label}"</strong>, never as approved or verified. Nothing is booked or paid.
                  </p>
                  {accept.problems.length > 0 && (
                    <div className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">
                      <p className="mb-1 font-semibold">Confirmed problems the verifier could not repair:</p>
                      <ul className="list-disc space-y-1 pl-5">
                        {accept.problems.slice(0, 6).map((p) => <li key={p}>{p}</li>)}
                      </ul>
                    </div>
                  )}
                  {accept.limitations.length > 0 && (
                    <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                      <p className="mb-1 font-semibold">What could not be confirmed:</p>
                      <ul className="list-disc space-y-1 pl-5">
                        {accept.limitations.slice(0, 8).map((l) => <li key={l}>{l}</li>)}
                      </ul>
                    </div>
                  )}
                  <dl className="mb-4 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 rounded-xl bg-[#f1f7f8] p-3 text-sm">
                    <dt className="text-[#62808a]">Known subtotal (priced items only)</dt>
                    <dd className="text-right font-semibold">
                      {accept.costs.known_subtotal != null && Number.isFinite(Number(accept.costs.known_subtotal))
                        ? formatMoney(Number(accept.costs.known_subtotal), accept.costs.currency || view.currency)
                          + (Number(accept.costs.known_subtotal) === 0 && (accept.costs.unpriced_items || []).length
                            ? ' (no planned item has a price)' : '')
                        : 'Unknown'}
                    </dd>
                    <dt className="text-[#62808a]">Items with price unavailable</dt>
                    <dd className="text-right font-semibold">{(accept.costs.unpriced_items || []).length}</dd>
                    <dt className="col-span-2 mt-1 text-xs text-[#62808a]">
                      Unavailable prices are not counted as free, so the final budget cannot be guaranteed.
                    </dt>
                  </dl>
                  <label className="mb-5 flex items-start gap-3 text-sm leading-6 text-[#1c3544]">
                    <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)}
                      disabled={approving} className="mt-1 h-4 w-4 accent-amber-600" />
                    <span>
                      I understand this itinerary is not fully verified, and I want to save it with these
                      {accept.mode === 'unverified_draft' ? ' problems' : ' limitations'}.
                    </span>
                  </label>
                  <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                    <button type="button" onClick={cancelAccept} disabled={approving}
                      className="rounded-2xl border border-[#d5e6e9] px-5 py-3 text-sm font-semibold text-[#245c6a] transition hover:bg-[#e7f3f4] disabled:opacity-60">
                      Cancel
                    </button>
                    <button type="button" onClick={handleAccept} disabled={!acknowledged || approving}
                      aria-busy={approving}
                      className="rounded-2xl bg-amber-600 px-5 py-3 text-sm font-bold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50">
                      {approving ? 'Saving…' : accept.button}
                    </button>
                  </div>
                  {approveError && (
                    <p role="alert" className="mt-3 rounded-xl bg-red-50 p-3 text-center text-sm text-red-700">
                      {approveError}
                    </p>
                  )}
                </div>
              </div>
            )}
          </aside>
        </div>

        <footer className="py-12 text-center text-sm text-[#8aa0aa]">
          Made for the curious. Powered by Rihla.
        </footer>
      </main>
    </div>
  )
}
