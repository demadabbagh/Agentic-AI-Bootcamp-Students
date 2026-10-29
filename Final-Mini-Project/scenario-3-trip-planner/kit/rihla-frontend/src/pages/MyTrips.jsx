import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  CalendarDays,
  Compass,
  Download,
  MapPin,
  Plane,
  Plus,
  Search,
  Sparkles,
  BriefcaseBusiness,
  Users,
  Wallet,
} from 'lucide-react'

const demoTrips = [
  {
    id: 1,
    destination: 'Istanbul',
    country: 'TÃ¼rkiye',
    dates: 'Oct 29 â€“ 31, 2026',
    travellers: 2,
    budget: '$1,500',
    days: 3,
    status: 'Upcoming',
    image:
      'https://images.unsplash.com/photo-1524231757912-21f4fe3a7200?w=1100&q=85',
  },
  {
    id: 2,
    destination: 'Paris',
    country: 'France',
    dates: 'Dec 10 â€“ 15, 2026',
    travellers: 2,
    budget: '$2,400',
    days: 6,
    status: 'Completed',
    image:
      'https://images.unsplash.com/photo-1502602898657-3e91760cbb34?w=1100&q=85',
  },
  {
    id: 3,
    destination: 'Rome',
    country: 'Italy',
    dates: 'Jan 5 â€“ 9, 2027',
    travellers: 1,
    budget: '$1,800',
    days: 5,
    status: 'Draft',
    image:
      'https://images.unsplash.com/photo-1552832230-c0197dd311b5?w=1100&q=85',
  },
]

export default function MyTrips() {
  const [search, setSearch] = useState('')
  const [activeFilter, setActiveFilter] = useState('All Trips')

  const filteredTrips = demoTrips.filter((trip) => {
    const matchesSearch = `${trip.destination} ${trip.country}`
      .toLowerCase()
      .includes(search.toLowerCase())

    const matchesStatus =
      activeFilter === 'All Trips' || trip.status === activeFilter

    return matchesSearch && matchesStatus
  })

  return (
    <main className="min-h-screen bg-[#edf5f7] text-[#1c3544]">
      {/* NAVBAR */}
      <header className="sticky top-0 z-20 border-b border-[#dce9ec] bg-white/85 backdrop-blur-xl">
        <nav className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-5">
          <Link to="/" className="flex items-center gap-2 text-2xl font-bold">
            <span className="rounded-xl bg-[#d6eeee] p-2 text-[#2e7b87]">
              <Plane size={21} />
            </span>
            Rihla<span className="text-[#69b7bf]">.</span>
          </Link>

          <div className="hidden items-center gap-8 text-sm font-medium text-[#69808b] md:flex">
            <Link to="/" className="hover:text-[#256a77]">
              Home
            </Link>
            <Link to="/trip" className="hover:text-[#256a77]">
              Trip Planner
            </Link>
            <span className="font-bold text-[#256a77]">My Trips</span>
          </div>

          <Link
            to="/login"
            className="rounded-full bg-[#245c6a] px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-[#347989]"
          >
            Log In
          </Link>
        </nav>
      </header>

      <div className="mx-auto max-w-7xl px-6 pb-16 pt-10">
        {/* HERO */}
        <section className="relative mb-10 overflow-hidden rounded-[32px] bg-[#173c4a]">
          <div
            className="absolute inset-0 bg-cover bg-center opacity-50"
            style={{
              backgroundImage:
                "linear-gradient(90deg, #123443 5%, transparent 100%), url('https://images.unsplash.com/photo-1470770841072-f978cf4d019e?w=1800&q=90')",
            }}
          />

          <div className="relative flex min-h-[290px] flex-col justify-center p-8 text-white md:p-12">
            <span className="mb-5 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-[#b8e9e8]">
              <Sparkles size={16} />
              YOUR PERSONAL TRAVEL COLLECTION
            </span>

            <h1 className="max-w-2xl text-4xl font-semibold leading-tight tracking-tight md:text-5xl">
              Your journeys,
              <br />
              <span className="font-light italic text-[#b8e9e8]">
                beautifully remembered.
              </span>
            </h1>

            <p className="mt-5 max-w-xl text-sm leading-7 text-white/80 md:text-base">
              Every destination has a story. Keep your adventures together
              and revisit the moments that inspire your next journey.
            </p>
          </div>
        </section>

        {/* HEADER + SEARCH */}
        <section className="mb-8 flex flex-col justify-between gap-5 md:flex-row md:items-end">
          <div>
            <p className="mb-2 text-xs font-bold uppercase tracking-[0.18em] text-[#79afb6]">
              THE ADVENTURE CONTINUES
            </p>
            <h2 className="text-3xl font-bold tracking-tight">My Trips</h2>
            <p className="mt-2 text-sm text-[#8297a0]">
              Preview collection Â· Trips are not saved to an account yet.
            </p>
          </div>

          <Link
            to="/"
            className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[#245c6a] px-6 py-3.5 text-sm font-semibold text-white shadow-lg shadow-[#245c6a]/10 transition hover:-translate-y-0.5 hover:bg-[#347989]"
          >
            <Plus size={18} />
            Plan a new trip
            <ArrowRight size={17} />
          </Link>
        </section>

        {/* SEARCH */}
        <div className="mb-8 flex items-center gap-3 rounded-2xl border border-[#dce9ec] bg-white px-5 py-4 shadow-sm md:max-w-md">
          <Search size={20} className="text-[#83a7af]" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search destinations..."
            aria-label="Search trips"
            className="w-full bg-transparent text-sm text-[#245c6a] outline-none placeholder:text-[#a0b4bc]"
          />
        </div>

        {/* TRIP FILTERS */}
        <div className="mb-8 flex flex-wrap gap-3">
          {['All Trips', 'Draft', 'Upcoming', 'Completed'].map((filter) => (
            <button
              key={filter}
              type="button"
              onClick={() => setActiveFilter(filter)}
              className={`rounded-full px-5 py-2.5 text-sm font-semibold transition ${
                activeFilter === filter
                  ? 'bg-[#245c6a] text-white shadow-md'
                  : 'border border-[#dce9ec] bg-white text-[#69808b] hover:bg-[#e1eff1]'
              }`}
            >
              {filter}
            </button>
          ))}
        </div>

        {/* TRIP CARDS */}
        {filteredTrips.length > 0 ? (
          <div className="grid gap-7 md:grid-cols-2 xl:grid-cols-3">
            {filteredTrips.map((trip) => (
              <article
                key={trip.id}
                className="group overflow-hidden rounded-[28px] border border-[#dfeaec] bg-white shadow-sm transition duration-300 hover:-translate-y-1 hover:shadow-[0_18px_45px_rgba(31,75,88,0.12)]"
              >
                <div className="relative h-56 overflow-hidden">
                  <img
                    src={trip.image}
                    alt={`${trip.destination}, ${trip.country}`}
                    className="h-full w-full object-cover transition duration-700 group-hover:scale-105"
                  />

                  <div className="absolute inset-0 bg-gradient-to-t from-[#102f3b]/60 via-transparent to-transparent" />

                  <span className="absolute left-5 top-5 rounded-full border border-white/40 bg-white/85 px-4 py-2 text-xs font-bold text-[#245c6a] backdrop-blur-xl">
                    {trip.status}
                  </span>

                  <div className="absolute bottom-5 left-6 text-white">
                    <p className="mb-1 flex items-center gap-1.5 text-xs text-white/85">
                      <MapPin size={14} />
                      {trip.country}
                    </p>
                    <h3 className="text-3xl font-bold">{trip.destination}</h3>
                  </div>
                </div>

                <div className="p-6">
                  <div className="grid grid-cols-2 gap-4 text-sm text-[#6f8993]">
                    <div className="flex items-center gap-2">
                      <CalendarDays size={17} className="text-[#65aab3]" />
                      <span>{trip.dates}</span>
                    </div>

                    <div className="flex items-center gap-2">
                      <Compass size={17} className="text-[#65aab3]" />
                      <span>{trip.days} days</span>
                    </div>

                    <div className="flex items-center gap-2">
                      <Users size={17} className="text-[#65aab3]" />
                      <span>
                        {trip.travellers}{' '}
                        {trip.travellers === 1 ? 'traveller' : 'travellers'}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <Wallet size={17} className="text-[#65aab3]" />
                      <span>{trip.budget}</span>
                    </div>
                  </div>

                  <div className="mt-6 flex items-center gap-3 border-t border-[#edf2f3] pt-5">
                    <Link
                      to="/trip"
                      className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[#245c6a] px-4 py-3 text-sm font-semibold text-white transition hover:bg-[#347989]"
                    >
                      View itinerary
                      <ArrowRight size={16} />
                    </Link>

                    <button
                      type="button"
                      disabled
                      title="PDF export is coming soon"
                      aria-label={`Download ${trip.destination} PDF - coming soon`}
                      className="cursor-not-allowed rounded-xl bg-[#edf5f6] p-3 text-[#a0b4bc]"
                    >
                      <Download size={18} />
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="rounded-[28px] border border-[#dfeaec] bg-white px-6 py-20 text-center">
            <BriefcaseBusiness size={38} className="mx-auto mb-4 text-[#79afb6]" />
            <h3 className="text-xl font-bold">No matching trips</h3>
            <p className="mt-2 text-sm text-[#8297a0]">
              Try searching for another destination.
            </p>
          </div>
        )}

        <div className="mt-10 rounded-2xl border border-[#d7e8ea] bg-[#e6f2f3] p-5 text-sm leading-6 text-[#64848d]">
          <strong>Design preview:</strong> These are sample trips for
          demonstrating the interface. Real trip history will appear here
          after we connect user accounts and persistent storage.
        </div>

        <footer className="pt-12 text-center text-sm text-[#8aa0aa]">
          Made for the curious. Powered by Rihla âœ¨
        </footer>
      </div>
    </main>
  )
}
