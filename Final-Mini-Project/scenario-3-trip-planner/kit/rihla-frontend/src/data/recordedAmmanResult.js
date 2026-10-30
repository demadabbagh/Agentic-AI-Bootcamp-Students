// A REAL response from POST /api/chat, recorded on 2026-10-30 (Amman, 2 days, live AI).
// Kept unchanged except the session id, so it can be loaded as a clearly labelled SAMPLE.
// The backend did NOT verify it (status verification_required): never show it as verified.
export const RECORDED_AMMAN_RESULT = {
  "status": "verification_required",
  "itinerary": {
    "destination": "Amman, Jordan",
    "currency": "USD",
    "travellers": 2,
    "budget_total": 900.0,
    "estimated_total": 50.76,
    "days": [
      {
        "date": "2026-11-12",
        "activities": [
          {
            "name": "Cave of the Seven Sleepers",
            "start_time": "10:00",
            "end_time": "11:30",
            "estimated_cost": 0.0,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#See",
            "notes": "Listed as free; Duration is a planning estimate"
          },
          {
            "name": "Jordan National Gallery of Fine Arts",
            "start_time": "13:50",
            "end_time": "15:20",
            "estimated_cost": 14.1,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#See",
            "notes": "Price basis not stated in the source: budgeted per person; 5 JOD per person x 2, converted JOD->USD 1.410437 (Central Bank of Jordan peg via USD, 2026-10-23) = 14.1 USD: estimate; Estimated cost from the listed price; Duration is a planning estimate"
          },
          {
            "name": "The Jordan Museum",
            "start_time": "15:59",
            "end_time": "17:29",
            "estimated_cost": 14.1,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#See",
            "notes": "Price basis not stated in the source: budgeted per person; 1-5 JOD per person x 2, converted JOD->USD 1.410437 (Central Bank of Jordan peg via USD, 2026-10-23) = 2.82-14.1 USD (sources disagree; 14.1 used for the budget): estimate; Sources disagree: 2.82-14.10 USD; 14.10 USD used for the budget (estimate); Duration is a planning estimate"
          },
          {
            "name": "Hashem",
            "start_time": "18:30",
            "end_time": "19:30",
            "estimated_cost": 8.46,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#Eat",
            "notes": "Opening hours unknown: check before you go; 3 JOD per person x 2, converted JOD->USD 1.410437 (Central Bank of Jordan peg via USD, 2026-10-23) = 8.46 USD: estimate; Estimated cost from the listed price; Duration is a planning estimate"
          }
        ]
      },
      {
        "date": "2026-11-13",
        "activities": [
          {
            "name": "Roman Theater",
            "start_time": "09:00",
            "end_time": "10:30",
            "estimated_cost": 5.64,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#See",
            "notes": "Opening hours unknown: check before you go; Price basis not stated in the source: budgeted per person; 2 JOD per person x 2, converted JOD->USD 1.410437 (Central Bank of Jordan peg via USD, 2026-10-23) = 5.64 USD: estimate; Estimated cost from the listed price; Duration is a planning estimate"
          },
          {
            "name": "Roman Nymphaeum",
            "start_time": "10:30",
            "end_time": "12:00",
            "estimated_cost": null,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#See",
            "notes": "Opening hours unknown: check before you go; Cost unknown: no price in the sources; Duration is a planning estimate"
          },
          {
            "name": "Zahret Al Madaen Restaurant & Butchery",
            "start_time": "12:05",
            "end_time": "13:05",
            "estimated_cost": null,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#Eat",
            "notes": "Opening hours unknown: check before you go; Cost unknown: no price in the sources; Duration is a planning estimate"
          },
          {
            "name": "Jordan Archaeological Museum",
            "start_time": "13:22",
            "end_time": "14:52",
            "estimated_cost": null,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#See",
            "notes": "Opening hours unknown: check before you go; Cost unknown: no price in the sources; Duration is a planning estimate"
          },
          {
            "name": "Amman Citadel",
            "start_time": "14:54",
            "end_time": "16:24",
            "estimated_cost": 8.46,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#See",
            "notes": "Opening hours unknown: check before you go; Price basis not stated in the source: budgeted per person; 3 JOD per person x 2, converted JOD->USD 1.410437 (Central Bank of Jordan peg via USD, 2026-10-23) = 8.46 USD: estimate; Estimated cost from the listed price; Duration is a planning estimate"
          },
          {
            "name": "Darat al Funun",
            "start_time": "16:44",
            "end_time": "18:14",
            "estimated_cost": null,
            "source_url": "https://en.wikivoyage.org/wiki/Amman#See",
            "notes": "Opening hours unknown: check before you go; Cost unknown: no price in the sources; Duration is a planning estimate"
          }
        ]
      }
    ]
  },
  "pending_action": null,
  "message": "The itinerary could not be fully verified. No approval is allowed.",
  "coverage": {
    "trip_days": 2,
    "nights": 1,
    "required_days": 1,
    "food_focused": false,
    "sufficient": true,
    "covered_dates": [
      "2026-11-12",
      "2026-11-13"
    ],
    "meal_only_dates": [],
    "empty_dates": [],
    "warning": null
  },
  "plan_quality": {
    "level": "full",
    "total_non_meal": 8,
    "meals": 2,
    "target_min": 4,
    "target_max": 6,
    "days": [
      {
        "date": "2026-11-12",
        "non_meal": 3,
        "target": 2
      },
      {
        "date": "2026-11-13",
        "non_meal": 5,
        "target": 2
      }
    ],
    "light_dates": [],
    "matching_attractions": 13,
    "selected_non_meal": 8,
    "excluded_matching": 5,
    "dropped_for_budget": 0,
    "unscheduled": 0,
    "message": null
  },
  "summary": {
    "interest_coverage": [
      {
        "interest": "History & Culture",
        "supported": true,
        "scheduled": 7,
        "places": [
          "The Jordan Museum",
          "Jordan Archaeological Museum",
          "Darat al Funun",
          "Roman Theater",
          "Amman Citadel",
          "Roman Nymphaeum",
          "Cave of the Seven Sleepers"
        ]
      },
      {
        "interest": "Art & Museums",
        "supported": true,
        "scheduled": 5,
        "places": [
          "The Jordan Museum",
          "Jordan Archaeological Museum",
          "Darat al Funun",
          "Jordan National Gallery of Fine Arts",
          "Roman Theater"
        ]
      }
    ],
    "limitations": [
      "No usable price in the sources for: Jordan Archaeological Museum, Darat al Funun, Roman Nymphaeum, Zahret Al Madaen Restaurant & Butchery. Their cost is unknown, not zero.",
      "These totals cover only the entry fees and meals priced in this plan. Accommodation, transport, other meals and incidentals are not included, so a plan within budget does not mean the whole trip is. All prices are estimates from the sources shown.",
      "2 walk(s) between stops take longer than 30 minutes (walking is the only travel mode this plan measures; public transport may be faster): The walk to Jordan National Gallery of Fine Arts on 2026-11-12 takes 140 minutes; no reorder of this day's places using walking times already measured avoids it; other day assignments were not compared. The walk to The Jordan Museum on 2026-11-12 takes 39 minutes; no reorder of this day's places using walking times already measured avoids it; other day assignments were not compared."
    ],
    "planned_by": "agent",
    "days": [
      {
        "date": "2026-11-12",
        "activities": [
          {
            "name": "Cave of the Seven Sleepers",
            "kind": "see",
            "start": "10:00",
            "end": "11:30",
            "meal": false,
            "walk_minutes": null
          },
          {
            "name": "Jordan National Gallery of Fine Arts",
            "kind": "see",
            "start": "13:50",
            "end": "15:20",
            "meal": false,
            "walk_minutes": 140
          },
          {
            "name": "The Jordan Museum",
            "kind": "see",
            "start": "15:59",
            "end": "17:29",
            "meal": false,
            "walk_minutes": 39
          },
          {
            "name": "Hashem",
            "kind": "eat",
            "start": "18:30",
            "end": "19:30",
            "meal": true,
            "walk_minutes": 23
          }
        ]
      },
      {
        "date": "2026-11-13",
        "activities": [
          {
            "name": "Roman Theater",
            "kind": "see",
            "start": "09:00",
            "end": "10:30",
            "meal": false,
            "walk_minutes": null
          },
          {
            "name": "Roman Nymphaeum",
            "kind": "see",
            "start": "10:30",
            "end": "12:00",
            "meal": false,
            "walk_minutes": 0
          },
          {
            "name": "Zahret Al Madaen Restaurant & Butchery",
            "kind": "eat",
            "start": "12:05",
            "end": "13:05",
            "meal": true,
            "walk_minutes": 5
          },
          {
            "name": "Jordan Archaeological Museum",
            "kind": "see",
            "start": "13:22",
            "end": "14:52",
            "meal": false,
            "walk_minutes": 17
          },
          {
            "name": "Amman Citadel",
            "kind": "see",
            "start": "14:54",
            "end": "16:24",
            "meal": false,
            "walk_minutes": 2
          },
          {
            "name": "Darat al Funun",
            "kind": "see",
            "start": "16:44",
            "end": "18:14",
            "meal": false,
            "walk_minutes": 20
          }
        ]
      }
    ],
    "sightseeing_per_day": [
      3,
      5
    ],
    "meals_per_day": [
      1,
      1
    ]
  },
  "session_id": "sample-amman-recorded"
}
