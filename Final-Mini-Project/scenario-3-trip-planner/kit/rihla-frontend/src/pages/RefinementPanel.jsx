import { useEffect, useState } from 'react'
import { CheckCircle2, Loader2, Sparkles } from 'lucide-react'
import { AuthRequiredError } from '../auth/authApi'
import { applyReview, getReview, REVIEW_MAX_MS, REVIEW_POLL_MS, reviewIsRunning, reviewView } from '../services/refinement'

// The agents' background review of a fast plan. It offers, it never replaces: see services/refinement.js.
export default function RefinementPanel({ sessionId, planVersion, initial, onApplied, onSessionExpired }) {
  const [review, setReview] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    if (!reviewIsRunning(review?.status)) return undefined
    let stopped = false
    const started = Date.now()
    let timer = null
    const ask = async () => {
      try {
        const next = await getReview(sessionId, planVersion)
        if (stopped) return
        setReview(next)
        if (!reviewIsRunning(next.status)) return
      } catch (err) {
        if (stopped || err instanceof AuthRequiredError) return // the page's own requests ask to log in again
      }
      if (!stopped && Date.now() - started < REVIEW_MAX_MS) timer = setTimeout(ask, REVIEW_POLL_MS)
    }
    timer = setTimeout(ask, REVIEW_POLL_MS)
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [sessionId, planVersion, review?.status])

  const view = reviewView(review)
  if (!view || dismissed) return null

  const apply = async () => {
    setBusy(true)
    setError('')
    try {
      const response = await applyReview(sessionId, planVersion)
      onApplied(response)
    } catch (err) {
      if (err instanceof AuthRequiredError) {
        onSessionExpired?.(apply)
        return
      }
      setError(err instanceof Error ? err.message : 'The refined plan could not be applied. Your plan was not changed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-label="AI review" className="rounded-[28px] border border-[#cfe3e6] bg-white p-6 shadow-sm">
      <h2 className="mb-2 flex items-center gap-2 text-lg font-bold">
        {view.kind === 'running' ? <Loader2 size={18} className="animate-spin text-[#65aeb6]" />
          : view.kind === 'ready' ? <Sparkles size={18} className="text-[#65aeb6]" />
            : <CheckCircle2 size={18} className="text-[#65aeb6]" />}
        {view.title}
      </h2>
      <p role="status" className="text-sm leading-6 text-[#8297a0]">{view.text}</p>
      {view.kind === 'ready' && (
        <>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-6 text-[#47606b]">
            {view.changes.map((line) => <li key={line}>{line}</li>)}
          </ul>
          <div className="mt-4 flex flex-col gap-2">
            <button type="button" onClick={apply} disabled={busy} aria-busy={busy}
              className="rounded-2xl bg-[#245c6a] px-5 py-3 text-sm font-bold text-white transition hover:bg-[#347989] disabled:opacity-60">
              {busy ? 'Applying…' : 'Use the refined plan'}
            </button>
            <button type="button" onClick={() => setDismissed(true)} disabled={busy}
              className="rounded-2xl border border-[#d5e6e9] px-5 py-3 text-sm font-semibold text-[#245c6a] transition hover:bg-[#e7f3f4] disabled:opacity-60">
              Keep my plan
            </button>
          </div>
        </>
      )}
      {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    </section>
  )
}
