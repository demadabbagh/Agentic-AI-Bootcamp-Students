import { useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { createTrip } from './services/tripApi'

import {
  ArrowRight,
  CalendarDays,
  Compass,
  MapPin,
  Menu,
  Plane,
  Sparkles,
  Users,
  Wallet,
} from 'lucide-react'

export default function App() {
  const navigate = useNavigate()
  const [destination, setDestination] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [travellers, setTravellers] = useState('2')
  const [budget, setBudget] = useState('')
  const [interests, setInterests] = useState([])
  const [customPreferences, setCustomPreferences] = useState('')

  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')

    const trip = {
      destination,
      departureDate: startDate,
      returnDate: endDate,
      travellers,
      budget,
      interests,
      customPreferences,
    }

    if (import.meta.env.VITE_ENABLE_LIVE_AI !== 'true') {
      navigate('/trip', { state: trip })
      return
    }

    setIsLoading(true)

    try {
      const sessionId = crypto.randomUUID()
      const result = await createTrip(trip, sessionId)

      if (!result?.itinerary || !Array.isArray(result.itinerary.days) || result.itinerary.days.length === 0) {
        throw new Error(result?.message || 'The AI could not generate a complete trip. Please try another destination.')
      }

      navigate('/trip', {
        state: {
          ...trip,
          sessionId,
          aiResult: result,
        },
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to plan your trip.')
    } finally {
      setIsLoading(false)
    }
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
        <nav className="relative z-10 mx-auto flex w-full max-w-7xl items-center justify-between px-7 py-7 md:px-12">
          <a href="/" className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white/20 backdrop-blur-lg">
              <Plane size={22} />
            </span>
            Rihla<span className="text-[#9bd9dd]">.</span>
          </a>

          <div className="hidden items-center gap-9 text-sm font-medium text-white/85 md:flex">
            <a href="#explore" className="transition hover:text-white">Explore</a>
            <a href="#planner" className="transition hover:text-white">AI Trip Planner</a>
            <a href="#about" className="transition hover:text-white">About Rihla</a>
          </div>

          <button className="hidden items-center gap-2 rounded-full border border-white/40 bg-white/15 px-5 py-3 text-sm font-medium backdrop-blur-xl transition hover:bg-white/25 md:flex">
            Start Exploring <ArrowRight size={16} />
          </button>

          <Menu className="md:hidden" />
        </nav>

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
                <label className="block">
                  <span className="mb-2 block text-xs font-medium text-white/85">
                    Destination
                  </span>
                  <div className="flex items-center gap-3 rounded-2xl border border-white/30 bg-white/15 px-4 py-4">
                    <MapPin size={18} className="shrink-0 text-[#b7e6e4]" />
                    <input
                      required
                      value={destination}
                      onChange={(e) => setDestination(e.target.value)}
                      placeholder="Where do you dream of going?"
                      className="w-full bg-transparent text-sm text-white outline-none placeholder:text-white/55"
                    />
                  </div>
                </label>

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
                      ['Shopping', '???'],
                      ['Art & Museums', 'Art'],
                      ['Adventure', 'Adventure'],
                      ['Relaxation', '???'],
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
                  className="mt-3 flex w-full items-center justify-center gap-3 rounded-2xl bg-[#c7eeec] px-5 py-4 text-sm font-bold text-[#143b46] shadow-lg transition hover:-translate-y-0.5 hover:bg-white"
                >
                  <Sparkles size={18} />
                  {isLoading ? 'Planning your trip...' : 'Plan my dream trip'}
                  <ArrowRight size={18} />
                </button>

                <p className="text-center text-xs text-white/65">
                  Thoughtful journeys, powered by AI Ã¢Å“Â¨
                </p>
                {error && (
                  <p role="alert" className="text-center text-sm text-red-200">
                    {error}
                  </p>
                )}
              </form>
            </div>
          </div>
        </div>

        <div className="relative z-10 mx-auto flex w-full max-w-7xl items-center justify-between px-7 pb-8 text-xs text-white/70 md:px-12">
          <span>RIHLA Ã‚Â· REIMAGINE THE WAY YOU TRAVEL</span>
          <span className="hidden md:block">SCROLL TO DISCOVER Ã¢â€ â€œ</span>
        </div>
      </section>
    </main>
  )
}

