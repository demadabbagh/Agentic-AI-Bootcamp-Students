import { useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import { createTrip, prepareDestination } from './services/tripApi'
import { validateTripResult } from './services/tripResult'
import { validateTripForm } from './services/tripForm'
import { useAuth } from './auth/authContext'
import { AuthRequiredError } from './auth/authApi'
import NavAuth, { Avatar } from './auth/NavAuth'
import { DestinationSearch } from './features/destinations'

const LIVE_AI_ENABLED = import.meta.env.VITE_ENABLE_LIVE_AI === 'true'

import {
  ArrowRight,
  CalendarDays,
  Compass,
  Menu,
  X,
  Plane,
  Sparkles,
  Users,
  Wallet,
} from 'lucide-react'

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, loggingOut, requireAuth, openLogin, logout, sessionExpired } = useAuth()
  const [destination, setDestination] = useState('')
  // The cities chosen from the suggestions (coordinates, country): for the trip page's weather and flights only,
  // never sent to the planner. null when the text was typed without choosing, or changed after choosing.
  const [destinationPlace, setDestinationPlace] = useState(null)

  // A chosen city: its guide is read while the rest of the form is filled in.
  useEffect(() => {
    if (destinationPlace?.label) prepareDestination(destinationPlace.label)
  }, [destinationPlace])
  const [originPlace, setOriginPlace] = useState(null)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [travellers, setTravellers] = useState('2')
  const [budget, setBudget] = useState('')
  const [interests, setInterests] = useState([])
  const [customPreferences, setCustomPreferences] = useState('')

  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [menuOpen, setMenuOpen] = useState(false)
  // Set synchronously: a second submit before React re-renders (double Enter, double click) sees it at once.
  const requestInFlight = useRef(false)

  // A real clock while the backend works (it reports no stages yet): never an invented progress bar.
  useEffect(() => {
    if (!isLoading) return undefined
    const started = Date.now()
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(timer)
  }, [isLoading])

  // An old /login link, or a private page visited as a guest (RequireAuth), lands here with { openLogin: true }:
  // open the modal over the home page, once. With returnTo, signing in goes back to that page.
  useEffect(() => {
    if (!location.state?.openLogin) return
    const returnTo = location.state.returnTo
    if (returnTo) requireAuth(() => navigate(returnTo), 'protected')
    else openLogin()
    navigate(location.pathname, { replace: true, state: null })
  }, [location.state, location.pathname, navigate, openLogin, requireAuth])

  // `owner` is the signed-in user the trip is generated for: the dashboard shows it to that account only.
  const generateTrip = async (trip, owner) => {
    if (requestInFlight.current) return // one request at a time, even before the next render
    requestInFlight.current = true
    setElapsed(0)
    setIsLoading(true)

    try {
      const sessionId = crypto.randomUUID()
      const result = validateTripResult(await createTrip(trip, sessionId))

      navigate('/trip', {
        state: {
          ...trip,
          sessionId,
          aiResult: result,
          ownerUid: owner.uid,
        },
      })
    } catch (err) {
      if (err instanceof AuthRequiredError) {
        // The session ended: log in again, then this same trip is requested once more.
        sessionExpired((signedIn) => generateTrip(trip, signedIn))
        return
      }
      setError(err instanceof Error ? err.message : 'Unable to plan your trip.')
    } finally {
      requestInFlight.current = false
      setIsLoading(false)
    }
  }

  const handleSubmit = (event) => {
    event.preventDefault()
    if (isLoading || requestInFlight.current) return // one request at a time
    setError('')

    const trip = {
      destination,
      departureDate: startDate,
      returnDate: endDate,
      travellers,
      budget,
      interests,
      customPreferences,
      // not sent to /api/chat (buildTripForm and buildTripMessage pick their own fields): the trip page reads them
      destinationPlace,
      originPlace,
    }

    const problems = validateTripForm(trip)
    if (problems.length) {
      setError(problems.join(' '))
      return
    }

    // Never fall through to the Dashboard without a generated itinerary.
    if (!LIVE_AI_ENABLED) {
      setError(
        'Live AI trip generation is turned off (VITE_ENABLE_LIVE_AI), so no trip was generated. Use "View Sample Trip" to preview the dashboard.'
      )
      return
    }

    // A guest is asked to log in first; nothing is sent to the planner until then. The form keeps what they
    // entered, and this exact trip is generated once, right after they sign in.
    requireAuth((signedIn) => generateTrip(trip, signedIn), 'generate')
  }

  return (
    <main className="min-h-screen bg-[#e8f1f3] p-3 font-sans text-white md:p-5">
      <section
        className="relative flex min-h-[calc(100vh-24px)] flex-col overflow-hidden rounded-[28px] bg-cover bg-center md:min-h-[calc(100vh-40px)] md:rounded-[36px]"
        style={{
          backgroundImage:
            "linear-gradient(90deg, rgba(8,29,41,.76), rgba(8,29,41,.2)), url('https://images.unsplash.com/photo-1470770841072-f978cf4d019e?w=2000&q=90')",
        }}
      >
        {/* z-30, above the hero content (z-10, painted later): the account menu opens over the planner card. */}
        <nav className="relative z-30 mx-auto flex w-full max-w-7xl items-center justify-between px-7 py-7 md:px-12">
          <a href="/" className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white/20 backdrop-blur-lg">
              <Plane size={22} />
            </span>
            Rihla<span className="text-[#9bd9dd]">.</span>
          </a>

          <div className="hidden items-center gap-9 text-sm font-medium text-white/85 md:flex">
            <a href="#explore" className="transition hover:text-white">Explore</a>
            <a href="#planner" className="transition hover:text-white">AI Trip Planner</a>
            <a href="/my-trips" className="transition hover:text-white">My Trips</a>
          </div>

          <div className="hidden items-center gap-3 md:flex">
            <button className="hidden items-center gap-2 rounded-full border border-white/40 bg-white/15 px-5 py-3 text-sm font-medium backdrop-blur-xl transition hover:bg-white/25 lg:flex">
              Start Exploring <ArrowRight size={16} />
            </button>
            <NavAuth />
          </div>

          <button
            type="button"
            className="md:hidden"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X /> : <Menu />}
          </button>
        </nav>

        {menuOpen && (
          <div className="relative z-10 mx-7 flex flex-col gap-1 rounded-2xl border border-white/30 bg-[#0d2a37]/90 p-3 text-sm backdrop-blur-xl md:hidden">
            {user && (
              <div className="mb-1 flex items-center gap-3 border-b border-white/10 px-3 pb-3 pt-1">
                <Avatar user={user} />
                <div className="min-w-0">
                  <p className="truncate font-semibold">{user.name}</p>
                  {user.email && <p className="truncate text-xs text-white/60">{user.email}</p>}
                </div>
              </div>
            )}
            <a href="#planner" onClick={() => setMenuOpen(false)} className="rounded-xl px-3 py-2 hover:bg-white/10">AI Trip Planner</a>
            {user && <a href="/profile" className="rounded-xl px-3 py-2 hover:bg-white/10">My Profile</a>}
            <a href="/my-trips" className="rounded-xl px-3 py-2 hover:bg-white/10">My Trips</a>
            {user ? (
              <button
                type="button"
                disabled={loggingOut}
                onClick={() =>
                  logout().catch((err) => setError(`${err.message} You are still signed in; please try again.`))
                }
                className="rounded-xl px-3 py-2 text-left hover:bg-white/10 disabled:cursor-wait disabled:opacity-60"
              >
                {loggingOut ? 'Logging out…' : 'Log Out'}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false)
                  openLogin()
                }}
                className="rounded-xl px-3 py-2 text-left hover:bg-white/10"
              >
                Log In
              </button>
            )}
          </div>
        )}

        <div className="relative z-10 mx-auto flex w-full max-w-7xl flex-1 flex-col justify-center gap-12 px-7 py-14 md:px-12 lg:flex-row lg:items-center lg:gap-20">
          <div className="max-w-2xl flex-1">
            <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-white/30 bg-white/15 px-4 py-2 text-xs font-medium tracking-wide backdrop-blur-xl">
              <Sparkles size={15} className="text-[#b8f2f1]" />
              YOUR JOURNEY, BEAUTIFULLY PLANNED
            </div>

            <h1 className="text-5xl font-semibold leading-[1.08] tracking-tight md:text-7xl">
              The world is
              <br />
              yours to <span className="italic text-[#b7e6e4]">explore.</span>
            </h1>

            <p className="mt-7 max-w-lg text-base leading-8 text-white/80 md:text-lg">
              Meet your personal AI travel companion. Discover extraordinary
              places and create a journey that feels uniquely yours.
            </p>

            <div className="mt-10 flex items-center gap-4 text-sm text-white/80">
              <div className="flex -space-x-3">
                {['A', 'M', 'S'].map((letter, index) => (
                  <div
                    key={letter}
                    className={`flex h-10 w-10 items-center justify-center rounded-full border-2 border-white/80 text-xs font-bold ${
                      ['bg-[#89b7ba]', 'bg-[#d7a78f]', 'bg-[#8296ae]'][index]
                    }`}
                  >
                    {letter}
                  </div>
                ))}
              </div>
              <span>Every great journey starts with a dream.</span>
            </div>
          </div>

          <div id="planner" className="w-full max-w-[465px]">
            <div className="rounded-[30px] border border-white/35 bg-white/15 p-5 shadow-2xl backdrop-blur-2xl md:p-7">
              <div className="mb-7 flex items-center justify-between">
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-[#b7e6e4]">
                    YOUR NEXT ADVENTURE
                  </p>
                  <h2 className="text-2xl font-semibold">Where to next?</h2>
                </div>
                <span className="rounded-2xl bg-white/20 p-3">
                  <Compass size={23} />
                </span>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                {/* Worldwide city search (docs/destinations.md): the form still sends plain text; a chosen
                    suggestion sends its full label ("Paris, Île-de-France Region, France"), never a guess. */}
                {/* FROM (optional) -> TO. FROM only starts the trip page's flight search; it is not sent to the
                    planner. Both keep the chosen place (coordinates, country) through onSelect. */}
                <DestinationSearch
                  label="From (optional, for flights)"
                  tone="dark"
                  placeholder="Your city (e.g. London)"
                  clearLabel="Clear departure city"
                  onSelect={setOriginPlace}
                />
                <DestinationSearch
                  label="To"
                  tone="dark"
                  placeholder="City, Country (e.g. Paris, France)"
                  defaultValue={destination}
                  onTextChange={setDestination}
                  onSelect={setDestinationPlace}
                  required
                />


                <div className="grid grid-cols-2 gap-3">
                  {[
                    ['Departure', startDate, setStartDate],
                    ['Return', endDate, setEndDate],
                  ].map(([label, value, setter]) => (
                    <label key={label} className="block">
                      <span className="mb-2 block text-xs font-medium text-white/85">
                        {label}
                      </span>
                      <div className="flex items-center gap-2 rounded-2xl border border-white/30 bg-white/15 px-3 py-4">
                        <CalendarDays size={17} className="hidden shrink-0 text-[#b7e6e4] sm:block" />
                        <input
                          type="date"
                          required
                          value={value}
                          min={label === 'Return' ? startDate : undefined}
                          onChange={(e) => setter(e.target.value)}
                          className="w-full min-w-0 bg-transparent text-xs text-white outline-none [color-scheme:dark]"
                        />
                      </div>
                    </label>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <label>
                    <span className="mb-2 block text-xs font-medium text-white/85">
                      Travellers
                    </span>
                    <div className="flex items-center gap-2 rounded-2xl border border-white/30 bg-white/15 px-3 py-4">
                      <Users size={18} className="shrink-0 text-[#b7e6e4]" />
                      <input
                        type="number"
                        min="1"
                        max="20"
                        value={travellers}
                        onChange={(e) => setTravellers(e.target.value)}
                        className="w-full min-w-0 bg-transparent text-sm text-white outline-none"
                      />
                    </div>
                  </label>

                  <label>
                    <span className="mb-2 block text-xs font-medium text-white/85">
                      Budget (USD)
                    </span>
                    <div className="flex items-center gap-2 rounded-2xl border border-white/30 bg-white/15 px-3 py-4">
                      <Wallet size={18} className="shrink-0 text-[#b7e6e4]" />
                      <input
                        type="number"
                        min="1"
                        value={budget}
                        onChange={(e) => setBudget(e.target.value)}
                        placeholder="e.g. 1500"
                        className="w-full min-w-0 bg-transparent text-sm text-white outline-none placeholder:text-white/55"
                      />
                    </div>
                  </label>
                </div>

                {/* TRAVEL PREFERENCES */}
                <div className="rounded-2xl border border-white/25 bg-white/10 p-4">
                  <h3 className="text-sm font-semibold text-white">
                    What do you love exploring?
                  </h3>
                  <p className="mb-3 mt-1 text-xs text-white/65">
                    Choose your interests to personalize your journey.
                  </p>

                  <div className="flex flex-wrap gap-2">
                    {[
                      ['History & Culture', 'History'],
                      ['Nature & Outdoors', 'Nature'],
                      ['Food & Cuisine', 'Food'],
                      ['Shopping', 'Shopping'],
                      ['Art & Museums', 'Art'],
                      ['Adventure', 'Adventure'],
                      ['Relaxation', 'Relaxation'],
                      ['Entertainment', 'Entertainment'],
                    ].map(([interest]) => {
                      const selected = interests.includes(interest)

                      return (
                        <button
                          key={interest}
                          type="button"
                          aria-pressed={selected}
                          onClick={() =>
                            setInterests((current) =>
                              current.includes(interest)
                                ? current.filter((item) => item !== interest)
                                : [...current, interest]
                            )
                          }
                          className={`rounded-xl border px-3 py-2 text-xs font-medium transition ${
                            selected
                              ? 'border-[#c7eeec] bg-[#c7eeec] text-[#143b46]'
                              : 'border-white/25 bg-white/10 text-white hover:bg-white/20'
                          }`}
                        >

                          {interest}
                        </button>
                      )
                    })}
                  </div>
                </div>




                {/* CUSTOM TRAVEL PREFERENCES */}
                <div className="rounded-2xl border border-white/25 bg-white/10 p-4">
                  <label
                    htmlFor="custom-preferences"
                    className="mb-2 block text-sm font-semibold text-white"
                  >
                    Anything else you'd love to experience?
                  </label>

                  <textarea
                    id="custom-preferences"
                    value={customPreferences}
                    onChange={(event) =>
                      setCustomPreferences(event.target.value)
                    }
                    rows={3}
                    maxLength={500}
                    placeholder="e.g. Ancient castles, hidden cafes, photography spots, quiet places..."
                    className="w-full resize-none rounded-xl border border-white/25 bg-white/10 px-4 py-3 text-sm text-white outline-none placeholder:text-white/50 focus:border-[#c7eeec]"
                  />

                  <p className="mt-2 text-xs text-white/60">
                    The more you share, the more personal your trip becomes.
                  </p>
                </div>

<button
                  disabled={isLoading}
                  type="submit"
                  className="mt-3 flex w-full items-center justify-center gap-3 rounded-2xl bg-[#c7eeec] px-5 py-4 text-sm font-bold text-[#143b46] shadow-lg transition hover:-translate-y-0.5 hover:bg-white disabled:cursor-wait disabled:opacity-80 disabled:hover:translate-y-0"
                >
                  <Sparkles size={18} />
                  {isLoading ? `Planning your trip... ${elapsed}s` : 'Plan my dream trip'}
                  <ArrowRight size={18} />
                </button>

                {isLoading && (
                  <p role="status" className="text-center text-xs text-white/80">
                    Researching places, pricing them, building and checking the day plan.{' '}
                    This usually takes a few seconds; please keep this page open.
                  </p>
                )}

                <p className="text-center text-xs text-white/65">
                  Thoughtful journeys, powered by AI ✨
                </p>
                {error && (
                  <p role="alert" className="text-center text-sm text-red-200">
                    {error}
                  </p>
                )}

                <button
                  type="button"
                  onClick={() => navigate('/trip', { state: { sample: true } })}
                  className="w-full rounded-2xl border border-white/35 bg-white/10 px-5 py-3 text-xs font-semibold text-white/85 transition hover:bg-white/20"
                >
                  View Sample Trip (demo data, not AI-generated)
                </button>
              </form>
            </div>
          </div>
        </div>

        <div className="relative z-10 mx-auto flex w-full max-w-7xl items-center justify-between px-7 pb-8 text-xs text-white/70 md:px-12">
          <span>RIHLA · REIMAGINE THE WAY YOU TRAVEL</span>
          <span className="hidden md:block">SCROLL TO DISCOVER ↓</span>
        </div>
      </section>
    </main>
  )
}

