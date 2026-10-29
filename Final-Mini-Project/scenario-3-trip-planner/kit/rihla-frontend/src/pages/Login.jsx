import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  Eye,
  EyeOff,
  LockKeyhole,
  Mail,
  Plane,
  Sparkles,
  UserRound,
} from 'lucide-react'

export default function Login() {
  const [mode, setMode] = useState('login')
  const [showPassword, setShowPassword] = useState(false)
  const [form, setForm] = useState({
    name: '',
    email: '',
    password: '',
  })

  const isSignup = mode === 'signup'

  const handleSubmit = (event) => {
    event.preventDefault()

    // UI preview only. No credentials are stored or sent.
    alert(
      'Rihla account system is coming soon! This is a design preview only.'
    )
  }

  return (
    <main className="min-h-screen bg-cover bg-center p-3 text-white md:p-5" style={{ backgroundImage: "linear-gradient(90deg, rgba(9,35,46,.62), rgba(9,35,46,.35)), url(https://images.unsplash.com/photo-1470770841072-f978cf4d019e?w=2000&q=90)" }}>
      <div className="mx-auto grid min-h-[calc(100dvh-24px)] max-w-[1600px] overflow-hidden rounded-[30px] bg-transparent shadow-[0_24px_90px_rgba(30,70,85,0.12)] md:min-h-[calc(100dvh-40px)] lg:grid-cols-2">

        {/* LEFT: CINEMATIC IMAGE */}
        <section
          className="relative hidden min-h-0 flex-col justify-center overflow-hidden bg-cover bg-center p-12 text-white lg:flex"
          style={{
            backgroundImage:
              "linear-gradient(180deg, rgba(10,35,47,.35), rgba(10,35,47,.12) 40%, rgba(10,35,47,.78)), url('https://images.unsplash.com/photo-1470770841072-f978cf4d019e?w=1400&q=90')",
          }}
        >
          <Link
            to="/"
            className="absolute left-10 top-8 z-10 flex w-fit items-center gap-3 text-2xl font-semibold tracking-tight"
          >
            <span className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/30 bg-white/15 backdrop-blur-xl">
              <Plane size={23} />
            </span>
            Rihla<span className="-ml-3 text-[#aee8e6]">.</span>
          </Link>

          <div className="relative z-10 max-w-lg">
            <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/30 bg-white/15 px-4 py-2 text-xs font-semibold tracking-[0.15em] backdrop-blur-xl">
              <Sparkles size={15} />
              EVERY JOURNEY HAS A STORY
            </span>

            <h1 className="text-5xl font-semibold leading-[1.12] tracking-tight xl:text-6xl">
              Your next chapter
              <br />
              starts <span className="font-light italic text-[#c7eeec]">here.</span>
            </h1>

            <p className="mt-6 max-w-md text-base leading-8 text-white/80">
              Beautiful journeys deserve a beautiful beginning.
              Join Rihla and keep every adventure close.
            </p>

            <div className="mt-10 flex items-center gap-3 text-sm text-white/75">
              <span className="h-px w-12 bg-white/50" />
              EXPLORE MORE. REMEMBER FOREVER.
            </div>
          </div>
        </section>

        {/* RIGHT: AUTH FORM */}
        <section className="flex flex-col border border-white/25 bg-[#102f3b]/45 px-7 py-8 backdrop-blur-xl sm:px-12 lg:px-12 xl:px-20">
          <div className="flex items-center justify-between">
            <Link
              to="/"
              className="inline-flex items-center gap-2 text-sm font-medium text-white/75 transition hover:text-[#245c6a]"
            >
              <ArrowLeft size={17} />
              Back to home
            </Link>

            <span className="rounded-full bg-white/15 px-4 py-2 text-xs font-semibold tracking-wide text-[#c7eeec]">
              RIHLA EXPERIENCE
            </span>
          </div>

          <div className="mx-auto flex w-full max-w-[420px] flex-1 flex-col justify-center py-3">
            <div className="mb-3">
              <span className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-[20px] bg-white/15 text-[#c7eeec]">
                <Sparkles size={25} />
              </span>

              <p className="mb-3 text-xs font-bold uppercase tracking-[0.22em] text-[#b7e6e4]">
                {isSignup ? 'BEGIN YOUR JOURNEY' : 'WELCOME BACK'}
              </p>

              <h2 className="text-3xl font-semibold tracking-tight text-white">
                {isSignup ? 'Create your account.' : 'Welcome to Rihla.'}
              </h2>

              <p className="mt-2 leading-6 text-white/75">
                {isSignup
                  ? 'Your next unforgettable adventure is just a few steps away.'
                  : 'Sign in to revisit your journeys and discover what comes next.'}
              </p>
            </div>

            <div className="mb-5 grid grid-cols-2 rounded-2xl bg-white/15 p-1.5">
              <button
                type="button"
                onClick={() => setMode('login')}
                className={`rounded-xl px-4 py-3 text-sm font-semibold transition ${
                  !isSignup
                    ? 'bg-white/25 text-white shadow-sm'
                    : 'text-white/65 hover:text-[#245c6a]'
                }`}
              >
                Log In
              </button>

              <button
                type="button"
                onClick={() => setMode('signup')}
                className={`rounded-xl px-4 py-3 text-sm font-semibold transition ${
                  isSignup
                    ? 'bg-white/25 text-white shadow-sm'
                    : 'text-white/65 hover:text-[#245c6a]'
                }`}
              >
                Sign Up
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-2">
              {isSignup && (
                <label className="block">
                  <span className="mb-2 block text-sm font-semibold text-white/85">
                    Full name
                  </span>
                  <div className="flex items-center gap-3 rounded-2xl border border-white/30 bg-white/15 px-4 py-2.5 transition focus-within:border-[#7bbcc2] focus-within:ring-2 focus-within:ring-[#d9f0f0]">
                    <UserRound size={19} className="text-[#b9e5e5]" />
                    <input
                      type="text"
                      required
                      autoComplete="name"
                      value={form.name}
                      onChange={(e) =>
                        setForm({ ...form, name: e.target.value })
                      }
                      placeholder="Your full name"
                      className="w-full bg-transparent text-sm outline-none placeholder:text-white/50"
                    />
                  </div>
                </label>
              )}

              <label className="block">
                <span className="mb-2 block text-sm font-semibold text-white/85">
                  Email address
                </span>
                <div className="flex items-center gap-3 rounded-2xl border border-white/30 bg-white/15 px-4 py-2.5 transition focus-within:border-[#7bbcc2] focus-within:ring-2 focus-within:ring-[#d9f0f0]">
                  <Mail size={19} className="text-[#b9e5e5]" />
                  <input
                    type="email"
                    required
                    autoComplete="email"
                    value={form.email}
                    onChange={(e) =>
                      setForm({ ...form, email: e.target.value })
                    }
                    placeholder="you@example.com"
                    className="w-full bg-transparent text-sm outline-none placeholder:text-white/50"
                  />
                </div>
              </label>

              <label className="block">
                <span className="mb-2 block text-sm font-semibold text-white/85">
                  Password
                </span>
                <div className="flex items-center gap-3 rounded-2xl border border-white/30 bg-white/15 px-4 py-2.5 transition focus-within:border-[#7bbcc2] focus-within:ring-2 focus-within:ring-[#d9f0f0]">
                  <LockKeyhole size={19} className="text-[#b9e5e5]" />
                  <input
                    type={showPassword ? 'text' : 'password'}
                    required
                    minLength={8}
                    autoComplete={isSignup ? 'new-password' : 'current-password'}
                    value={form.password}
                    onChange={(e) =>
                      setForm({ ...form, password: e.target.value })
                    }
                    placeholder="At least 8 characters"
                    className="w-full bg-transparent text-sm outline-none placeholder:text-white/50"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="text-[#b9e5e5] transition hover:text-[#245c6a]"
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                  >
                    {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
                  </button>
                </div>
              </label>

              <button
                type="submit"
                className="mt-2 flex w-full items-center justify-center gap-3 rounded-2xl bg-[#245c6a] px-5 py-4 text-sm font-bold text-white shadow-[0_10px_25px_rgba(36,92,106,.18)] transition hover:-translate-y-0.5 hover:bg-[#347989]"
              >
                {isSignup ? 'Create my account' : 'Log In'}
                <ArrowRight size={18} />
              </button>
            </form>

            <p className="mt-4 text-center text-sm text-white/75">
              {isSignup ? 'Already part of Rihla?' : 'New to Rihla?'}{' '}
              <button
                type="button"
                onClick={() => setMode(isSignup ? 'login' : 'signup')}
                className="font-bold text-[#c7eeec] hover:underline"
              >
                {isSignup ? 'Log In' : 'Create an account'}
              </button>
            </p>

            <p className="mt-3 text-center text-xs text-white/65">
              Design preview only · Account authentication is not connected yet.
            </p>
          </div>

          <div className="text-center text-xs text-white/60">
            © 2026 Rihla · Travel beautifully.
          </div>
        </section>
      </div>
    </main>
  )
}