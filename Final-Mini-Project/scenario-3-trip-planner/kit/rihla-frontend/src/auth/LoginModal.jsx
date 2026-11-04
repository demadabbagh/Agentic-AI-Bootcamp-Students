import { useEffect, useRef, useState } from 'react'
import { LockKeyhole, Plane, ShieldCheck, Sparkles, X } from 'lucide-react'
import { loadFirebaseAuth } from './firebaseClient.js'
import { signInErrorMessage } from './signInErrors.js'

const COPY = {
  navbar: {
    eyebrow: 'WELCOME TO RIHLA',
    title: 'Log in to Rihla.',
    text: 'Sign in to plan with AI, revise your itineraries and keep your approved trips in one place.',
  },
  generate: {
    eyebrow: 'ONE STEP AWAY',
    title: 'Log in to plan your trip.',
    text: 'Your trip details are kept exactly as you entered them. As soon as you are signed in, Rihla starts planning.',
  },
  protected: {
    eyebrow: 'PRIVATE TO YOUR ACCOUNT',
    title: 'Log in to continue.',
    text: 'Your trips, itineraries and profile are private. Sign in and Rihla takes you straight there.',
  },
  expired: {
    eyebrow: 'SESSION ENDED',
    title: 'Please log in again.',
    text: 'Your session has expired. Sign in again and Rihla picks up where you left off.',
  },
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" className="h-5 w-5">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  )
}

export default function LoginModal({ reason, onClose, onSignIn }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const googleButton = useRef(null)
  // Why sign-in cannot start here (firebaseClient.resolveFirebaseConfig): null while loading or when it can.
  const [setupError, setSetupError] = useState(null)
  const copy = COPY[reason] || COPY.navbar

  useEffect(() => {
    googleButton.current?.focus()
    // Load Firebase now, so the popup opens within the click (browsers block popups opened later).
    let active = true
    loadFirebaseAuth().then(
      () => { if (active) setSetupError(null) },
      (err) => { if (active && ['auth/not-configured', 'auth/project-mismatch'].includes(err?.code)) setSetupError(err) },
    )
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      active = false
      document.body.style.overflow = previousOverflow
    }
  }, [])

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const signIn = async () => {
    if (busy) return
    setError('')
    if (setupError) {
      setError(signInErrorMessage(setupError))
      return
    }
    setBusy(true)
    try {
      await onSignIn() // closes this modal when the backend has verified the sign-in
    } catch (err) {
      const message = signInErrorMessage(err)
      if (message) setError(message)
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#071c26]/60 p-4 backdrop-blur-md"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="login-modal-title"
        aria-describedby="login-modal-text"
        className="relative w-full max-w-[440px] overflow-hidden rounded-[30px] border border-white/25 bg-[#102f3b]/85 p-7 text-white shadow-[0_24px_90px_rgba(5,20,28,0.55)] backdrop-blur-2xl sm:p-9"
      >
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          aria-label="Close"
          className="absolute right-5 top-5 rounded-full p-2 text-white/70 transition hover:bg-white/10 hover:text-white disabled:opacity-40"
        >
          <X size={20} />
        </button>

        <div className="mb-6 flex items-center gap-2 text-xl font-semibold tracking-tight">
          <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white/15">
            <Plane size={20} />
          </span>
          Rihla<span className="-ml-2 text-[#aee8e6]">.</span>
        </div>

        <span className="mb-4 inline-flex items-center gap-2 rounded-full border border-white/25 bg-white/10 px-3 py-1.5 text-[11px] font-bold tracking-[0.18em] text-[#b7e6e4]">
          <Sparkles size={13} />
          {copy.eyebrow}
        </span>
        <h2 id="login-modal-title" className="text-3xl font-semibold tracking-tight">
          {copy.title}
        </h2>
        <p id="login-modal-text" className="mt-3 text-sm leading-6 text-white/75">
          {copy.text}
        </p>

        <button
          ref={googleButton}
          type="button"
          onClick={signIn}
          disabled={busy}
          className="mt-7 flex w-full items-center justify-center gap-3 rounded-2xl bg-white px-5 py-4 text-sm font-bold text-[#143b46] shadow-lg transition hover:-translate-y-0.5 hover:bg-[#f2fbfb] disabled:cursor-wait disabled:opacity-80 disabled:hover:translate-y-0"
        >
          <GoogleMark />
          {busy ? 'Signing in…' : 'Continue with Google'}
        </button>

        {error && (
          <p role="alert" className="mt-4 rounded-2xl border border-red-200/30 bg-red-400/10 px-4 py-3 text-sm text-red-100">
            {error}
          </p>
        )}

        <div className="mt-6 space-y-2 text-xs text-white/60">
          <p className="flex items-center gap-2">
            <ShieldCheck size={14} className="shrink-0 text-[#b7e6e4]" />
            Google confirms who you are; Rihla never sees your password.
          </p>
          <p className="flex items-center gap-2">
            <LockKeyhole size={14} className="shrink-0 text-[#b7e6e4]" />
            Your saved trips are private to your account.
          </p>
        </div>
      </div>
    </div>
  )
}
