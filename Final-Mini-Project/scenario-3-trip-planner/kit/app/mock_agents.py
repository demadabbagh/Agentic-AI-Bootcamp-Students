def research_trip(user_request: str) -> dict:
    """Simulate the Research Agent."""
    return {
        "city": "Istanbul",
        "attractions": [
            {"name": "Hagia Sophia", "category": "history"},
            {"name": "Topkapi Palace", "category": "history"},
            {"name": "Basilica Cistern", "category": "history"},
        ],
    }


def plan_trip(research: dict) -> dict:
    """Simulate the Planning Agent."""
    return {
        "city": research["city"],
        "days": [
            {
                "day": 1,
                "activities": ["Hagia Sophia", "Basilica Cistern"],
            },
            {
                "day": 2,
                "activities": ["Topkapi Palace"],
            },
        ],
        "status": "draft",
    }


def verify_trip(itinerary: dict) -> dict:
    """Simulate the Verification Agent."""
    return {
        "approved": True,
        "issues": [],
    }