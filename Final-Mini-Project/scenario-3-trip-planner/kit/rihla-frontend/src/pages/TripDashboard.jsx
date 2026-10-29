import { normalizeItinerary } from '../services/itineraryAdapter'
import { useLocation } from 'react-router-dom'
import { useState } from 'react'
import {
  ArrowLeft, ArrowRight, CalendarDays, CheckCircle2, Clock3,
  Compass, MapPin, MessageCircle, Send, Sparkles, Wallet,
  Users, Plane, Heart, Coffee, Camera, ChevronRight, Download, LogIn, Bookmark,
} from 'lucide-react'

const demoDays = [
  {
    label: 'Day 01',
    date: 'October 29',
    title: 'Discover the heart of Istanbul',
    activities: [
      {
        time: '09:00 AM',
        name: 'Hagia Sophia',
        type: 'Culture & History',
        duration: '1.5 hours',
        price: '$25',
        image: 'https://images.unsplash.com/photo-1541432901042-2d8bd64b4a9b?w=500&q=85',
      },
      {
        time: '11:30 AM',
        name: 'Blue Mosque',
        type: 'Architecture',
        duration: '1 hour',
        price: 'Free',
        image: 'https://images.unsplash.com/photo-1527838832700-5059252407fa?w=500&q=85',
      },
      {
        time: '02:00 PM',
        name: 'Grand Bazaar',
        type: 'Shopping & Local Life',
        duration: '2 hours',
        price: '$40',
        image: 'https://images.unsplash.com/photo-1533676802871-eca1ae998cd5?w=500&q=85',
      },
    ],
  },
  {
    label: 'Day 02',
    date: 'October 30',
    title: 'A taste of Istanbul',
    activities: [
      {
        time: '10:00 AM',
        name: 'Bosphorus Cruise',
        type: 'Nature & Views',
        duration: '2 hours',
        price: '$35',
        image: 'https://images.unsplash.com/photo-1524231757912-21f4fe3a7200?w=500&q=85',
      },
      {
        time: '01:00 PM',
        name: 'Turkish Food Experience',
        type: 'Food & Culture',
        duration: '1.5 hours',
        price: '$30',
        image: 'https://images.unsplash.com/photo-1547592180-85f173990554?w=500&q=85',
      },
    ],
  },
  {
    label: 'Day 03',
    date: 'October 31',
    title: 'Hidden gems & unforgettable views',
    activities: [
      {
        time: '09:30 AM',
        name: 'Galata Tower',
        type: 'Landmarks',
        duration: '1.5 hours',
        price: '$30',
        image: 'https://images.unsplash.com/photo-1500534623283-312aade485b7?w=500&q=85',
      },
      {
        time: '01:00 PM',
        name: 'Explore Balat',
        type: 'Photography & Streets',
        duration: '2 hours',
        price: 'Free',
        image: 'https://images.unsplash.com/photo-1488646953014-85cb44e25828?w=500&q=85',
      },
    ],
  },
]

export default function TripDashboard() {
  const location = useLocation()
  const trip = location.state || {}

  const aiResult = trip.aiResult
  const itinerary = aiResult?.itinerary
  const hasAiItinerary = Boolean(
    itinerary && Array.isArray(itinerary.days) && itinerary.days.length > 0
  )

  const days = hasAiItinerary
    ? normalizeItinerary(itinerary)
    : demoDays

  const destinationName = hasAiItinerary
    ? itinerary.destination
    : 'Istanbul'

  const displayBudget = hasAiItinerary
    ? `${itinerary.budget_total ?? 'Not specified'} ${itinerary.currency || 'USD'}`
    : '$1,500'

  const totalExperiences = days.reduce(
    (total, day) => total + day.activities.length,
    0
  )

  const [activeDay, setActiveDay] = useState(0)
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      text: "Your Istanbul adventure is ready! ✨ Want to make any changes? I'm here to help.",
    },
  ])

  const sendMessage = (event) => {
    event.preventDefault()
    if (!message.trim()) return

    setMessages((previous) => [
      ...previous,
      { role: 'user', text: message },
      {
        role: 'assistant',
        text: 'Demo mode: AI trip revisions will be available when the backend is connected.',
      },
    ])
    setMessage('')
  }

  return (
    <div className="min-h-screen bg-[#edf5f7] text-[#1c3544]">
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
            <a href="/login" className="hover:text-[#256a77]">Log In</a>
          </nav>

          <span className="rounded-full bg-[#e8f6f5] px-4 py-2 text-xs font-semibold text-[#327783]">
            <Sparkles size={13} className="mr-1 inline" />
            AI-POWERED TRAVEL
          </span>
        </div>
      </header>

      {/* USER TRIP PREFERENCES */}
      <section className="mx-auto max-w-7xl px-6 pb-8">
        <div className="rounded-[28px] border border-[#dce9ec] bg-white p-6 shadow-sm md:p-8">
          <div className="mb-5 flex items-center gap-2 text-[#347989]">
            <Sparkles size={20} />
            <h2 className="text-xl font-bold">Your Trip Preferences</h2>
          </div>

          <div className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <p className="text-[#8297a0]">Destination</p>
              <p className="mt-1 font-semibold">
                {trip.destination || 'Not selected'}
              </p>
            </div>

            <div>
              <p className="text-[#8297a0]">Travel dates</p>
              <p className="mt-1 font-semibold">
                {trip.departureDate && trip.returnDate
                  ? `${trip.departureDate} to ${trip.returnDate}`
                  : 'Not selected'}
              </p>
            </div>

            <div>
              <p className="text-[#8297a0]">Travellers</p>
              <p className="mt-1 font-semibold">
                {trip.travellers || 'Not selected'}
              </p>
            </div>

            <div>
              <p className="text-[#8297a0]">Budget</p>
              <p className="mt-1 font-semibold">
                {trip.budget ? `$${trip.budget}` : 'Not selected'}
              </p>
            </div>
          </div>

          <div className="mt-6 border-t border-[#edf2f3] pt-5">
            <p className="mb-3 text-sm font-semibold">Your interests</p>

            {trip.interests?.length ? (
              <div className="flex flex-wrap gap-2">
                {trip.interests.map((interest) => (
                  <span
                    key={interest}
                    className="rounded-full bg-[#e5f4f4] px-4 py-2 text-xs font-semibold text-[#347989]"
                  >
                    {interest}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-sm text-[#8297a0]">
                No interests selected yet.
              </p>
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

          <p className="mt-6 text-xs leading-5 text-[#8297a0]">
            The Istanbul itinerary below is a design preview, not an AI-generated
            plan based on these preferences.
          </p>
        </div>
      </section>



      <main className="mx-auto max-w-7xl px-6 py-9">
        <a href="/" className="mb-7 inline-flex items-center gap-2 text-sm font-medium text-[#6b8791] hover:text-[#2d7783]">
          <ArrowLeft size={16} /> Back to explore
        </a>

        <div className="mb-8 flex flex-wrap items-end justify-between gap-5">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.2em] text-[#60a6b0]">
              <Sparkles size={15} /> YOUR PERSONALIZED ITINERARY
            </div>
            <h1 className="text-4xl font-bold tracking-tight md:text-5xl">
              Your {destinationName} <span className="font-light italic text-[#65aeb6]">escape.</span>
            </h1>
            <p className="mt-3 text-[#7a909b]">
              {hasAiItinerary ? `${days.length} days of discoveries, planned for you.` : "Three days of beautiful discoveries, thoughtfully planned for you."}
            </p>
          </div>

          <span className="rounded-full border border-amber-200 bg-amber-50 px-5 py-3 text-sm font-semibold text-amber-700">
            Preview itinerary · Not verified
          </span>
        </div>

        <div className="mb-6 flex flex-wrap gap-3">
          <a
            href="/my-trips"
            className="inline-flex items-center gap-2 rounded-2xl border border-[#d5e6e9] bg-white px-5 py-3 text-sm font-semibold text-[#245c6a] transition hover:bg-[#e7f3f4]"
          >
            <Bookmark size={17} /> My Trips
          </a>
          <button
            type="button"
            disabled
            title="PDF export is coming soon"
            className="inline-flex cursor-not-allowed items-center gap-2 rounded-2xl bg-[#dce9eb] px-5 py-3 text-sm font-semibold text-[#819ba3]"
          >
            <Download size={17} /> Download PDF ? Coming soon
          </button>
        </div>

        <div className="relative mb-8 overflow-hidden rounded-[30px] bg-[#183b4b]">
          <div
            className="absolute inset-0 bg-cover bg-center opacity-55"
            style={{
              backgroundImage:
                "linear-gradient(90deg, #123443 5%, transparent 100%), url('https://images.unsplash.com/photo-1524231757912-21f4fe3a7200?w=1600&q=90')",
            }}
          />
          <div className="relative flex min-h-[240px] flex-col justify-end p-8 text-white md:p-10">
            <span className="mb-3 flex items-center gap-2 text-sm text-[#c5e9e8]">
              <MapPin size={17} /> ISTANBUL, TÜRKIYE
            </span>
            <h2 className="mb-5 text-3xl font-semibold">A journey between two continents.</h2>
            <div className="flex flex-wrap gap-5 text-sm text-white/85">
              <span className="flex items-center gap-2"><CalendarDays size={17} /> Oct 29 – 31, 2026</span>
              <span className="flex items-center gap-2"><Users size={17} /> 2 travellers</span>
              <span className="flex items-center gap-2"><Wallet size={17} /> {displayBudget} budget</span>
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
                  3 DAYS · 7 EXPERIENCES
                </span>
              </div>

              <div className="mb-8 flex flex-wrap gap-3">
                {days.map((day, index) => (
                  <button
                    key={day.label}
                    onClick={() => setActiveDay(index)}
                    className={`rounded-2xl px-6 py-3 text-left transition ${
                      activeDay === index
                        ? 'bg-[#245c6a] text-white shadow-md'
                        : 'bg-[#f1f7f8] text-[#66828d] hover:bg-[#e1eff1]'
                    }`}
                  >
                    <span className="block text-sm font-bold">{day.label}</span>
                    <span className="text-xs opacity-75">{day.date}</span>
                  </button>
                ))}
              </div>

              <div className="mb-7">
                <h3 className="text-xl font-bold">{days[activeDay].title}</h3>
                <p className="mt-1 text-sm text-[#8297a0]">
                  A beautiful day of discovery awaits.
                </p>
              </div>

              <div className="space-y-4">
                {days[activeDay].activities.map((activity) => (
                  <article
                    key={activity.name}
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
                      <div className="mt-4 flex flex-wrap items-center gap-4 text-xs font-medium text-[#68818b]">
                        <span>{activity.duration}</span>
                        <span className="rounded-full bg-[#eaf5f4] px-3 py-1 text-[#3c8992]">
                          {activity.price}
                        </span>
                      </div>
                    </div>
                    <ChevronRight className="hidden self-center text-[#9bb9c0] sm:block" size={20} />
                  </article>
                ))}
              </div>

              <div className="mt-7 flex items-center justify-between">
                <button
                  disabled={activeDay === 0}
                  onClick={() => setActiveDay((day) => day - 1)}
                  className="rounded-xl px-4 py-3 text-sm font-semibold text-[#547a86] disabled:opacity-30"
                >
                  ← Previous day
                </button>
                <button
                  disabled={activeDay === days.length - 1}
                  onClick={() => setActiveDay((day) => day + 1)}
                  className="flex items-center gap-2 rounded-xl bg-[#245c6a] px-5 py-3 text-sm font-semibold text-white disabled:opacity-30"
                >
                  Next day <ArrowRight size={16} />
                </button>
              </div>
            </section>
          </div>

          <aside className="space-y-6">
            <section className="rounded-[28px] border border-[#e0ebed] bg-white p-6 shadow-sm">
              <p className="mb-2 text-xs font-bold uppercase tracking-[0.18em] text-[#79afb6]">
                YOUR TRIP AT A GLANCE
              </p>
              <h2 className="mb-6 text-xl font-bold">Budget overview</h2>

              <div className="mb-5 rounded-2xl bg-[#eaf5f5] p-5">
                <p className="text-sm text-[#64848d]">Your total budget</p>
                <p className="mt-1 text-3xl font-bold text-[#245c6a]">{displayBudget}</p>
              </div>

              <div className="space-y-4 text-sm">
                <div className="flex justify-between">
                  <span className="text-[#8297a0]">Estimated activities</span>
                  <strong>$160</strong>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#8297a0]">Remaining budget</span>
                  <strong className="text-[#368b84]">$1,340</strong>
                </div>
              </div>

              <div className="mt-6 h-2 overflow-hidden rounded-full bg-[#e6f1f2]">
                <div className="h-full w-[11%] rounded-full bg-[#67b7b7]" />
              </div>
              <p className="mt-3 text-xs leading-5 text-[#91a3ab]">
                Demo estimates only. Actual costs may vary.
              </p>
            </section>

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
                    className={`max-w-[95%] rounded-2xl p-3 text-sm leading-6 ${
                      item.role === 'user'
                        ? 'self-end bg-[#245c6a] text-white'
                        : 'self-start bg-[#edf5f6] text-[#476774]'
                    }`}
                  >
                    {item.text}
                  </div>
                ))}
              </div>

              <form onSubmit={sendMessage} className="flex gap-2 border-t border-[#edf2f3] p-4">
                <input
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="Ask for a change..."
                  className="min-w-0 flex-1 rounded-xl bg-[#f1f7f8] px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-[#a9d7dc]"
                />
                <button
                  type="submit"
                  aria-label="Send message"
                  className="rounded-xl bg-[#245c6a] p-3 text-white transition hover:bg-[#347989]"
                >
                  <Send size={18} />
                </button>
              </form>
            </section>

            <section className="rounded-[28px] border border-[#e0ebed] bg-white p-6 shadow-sm">
              <h2 className="mb-2 text-lg font-bold">Ready for your adventure?</h2>
              <p className="mb-5 text-sm leading-6 text-[#8297a0]">
                Approval will be enabled once the real itinerary passes verification.
              </p>
              <button
                disabled
                className="flex w-full cursor-not-allowed items-center justify-center gap-2 rounded-2xl bg-[#dce9eb] px-5 py-4 text-sm font-bold text-[#819ba3]"
              >
                <CheckCircle2 size={18} /> Approve itinerary
              </button>
              <p className="mt-3 text-center text-xs text-[#91a3ab]">
                Preview mode · Approval unavailable
              </p>
            </section>
          </aside>
        </div>

        <footer className="py-12 text-center text-sm text-[#8aa0aa]">
          Made for the curious. Powered by Rihla ✨
        </footer>
      </main>
    </div>
  )
}