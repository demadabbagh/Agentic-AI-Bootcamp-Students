"""Prices as guides around the world really write them: every text below is from a real listing, or its local form.

Offline. TEST FIXTURE (labelled): the rate services' answers are stand-ins for http_get; the code under test is real.
A wrong currency is worse than an unknown price: a number with no currency stays unread.
"""
import pytest

from app.destinations import service as destinations
from planning.budget import ExchangeRate
from services import travel_data as td


def read(text, country=None):
    p = td.parse_price(text, country)
    return p["currency"], p["min"], p["max"]


@pytest.mark.parametrize("text, expected", [
    ("R$99 (online), R$110 (box office)", ("BRL", 99, 110)),  # was read as US$99: five times too much
    ("Adults R$10", ("BRL", 10, 10)),
    ("R$ 1.200,50", ("BRL", 1200.5, 1200.5)),
    ("R$12,50", ("BRL", 12.5, 12.5)),
    ("BRL 45", ("BRL", 45, 45)),
    ("₩3,000", ("KRW", 3000, 3000)),
    ("₩5,000-10,000", ("KRW", 5000, 10000)),
    ("Adult ₩3,000, students ₩2,000", ("KRW", 2000, 3000)),
    ("5000₩", ("KRW", 5000, 5000)),
    ("15,000 won", ("KRW", 15000, 15000)),
    ("KRW 8000", ("KRW", 8000, 8000)),
    ("Ft800-1050", ("HUF", 800, 1050)),
    ("Ft10,800", ("HUF", 10800, 10800)),
    ("Ft390-1 700", ("HUF", 390, 1700)),  # thousands by a space
    ("Ft 2 ,500", ("HUF", 2500, 2500)),
    ("HUF1100-2200", ("HUF", 1100, 2200)),
    ("3 500 Ft", ("HUF", 3500, 3500)),
    ("E£50", ("EGP", 50, 50)),
    ("EGP 200", ("EGP", 200, 200)),
    ("٥٠ جنيه", ("EGP", 50, 50)),  # Arabic-Indic digits, Arabic word
    ("١٬٢٠٠ ج.م", ("EGP", 1200, 1200)),
    ("MAD 70", ("MAD", 70, 70)),
    ("US$20 per person", ("USD", 20, 20)),
    ("$15", ("USD", 15, 15)),
    ("£12", ("GBP", 12, 12)),
])
def test_a_price_is_read_in_its_own_currency(text, expected):
    assert read(text) == expected


@pytest.mark.parametrize("text, country, expected", [
    ("LE120", "egypt", ("EGP", 120, 120)),
    ("LE550 (students LE300)", "egypt", ("EGP", 300, 550)),
    ("Medium LE20, Large LE25, Special LE30", "egypt", ("EGP", 20, 30)),
    ("170 dirham, 30 dirham extra for the Berber Museum", "morocco", ("MAD", 30, 170)),
    ("٧٠ درهم", "morocco", ("MAD", 70, 70)),
    ("50 Dh", "morocco", ("MAD", 50, 50)),
    ("50 dirham", "united arab emirates", ("AED", 50, 50)),
])
def test_a_word_means_a_currency_only_in_its_country(text, country, expected):
    assert read(text, country) == expected


@pytest.mark.parametrize("text", ["50 dirham", "LE120"])
def test_without_the_country_such_a_word_stays_unread(text):
    assert td.parse_price(text)["parsed"] is False


@pytest.mark.parametrize("text", ["A$30", "C$12", "HK$100", "NZ$20", "S$15"])
def test_another_countrys_dollar_is_never_a_us_dollar(text):
    assert td.parse_price(text)["currency"] != "USD"


@pytest.mark.parametrize("text", ["550", "Adult: 3200; senior, student: 1600", "20 (10 for students)", "Lunch menu: 1590"])
def test_a_number_with_no_currency_stays_unknown(text):
    p = td.parse_price(text, "hungary")
    assert p["parsed"] is False and p["currency"] is None and p["min"] is None


def test_a_labelled_extra_does_not_stand_in_for_an_unlabelled_entry_price():
    p = td.parse_price("550, students 275. Tickets for videography are available at LE300", "egypt")
    assert p["parsed"] is False and p["min"] is None  # was 300: the camera ticket as the entry price


@pytest.mark.parametrize("text", ["0₩", "₩0", "R$0"])
def test_a_zero_price_is_free(text):
    p = td.parse_price(text)
    assert p["free"] is True and p["currency"] is None and p["min"] == 0


def test_a_free_entry_keeps_being_free_and_a_price_for_some_is_not():
    assert td.parse_price("Free entry; guided tours cost ₩20,000")["free"] is True
    assert td.parse_price("₩4,000. Free for ages under 24")["free"] is False


def test_a_listing_is_read_with_its_country_and_the_country_stays_private():
    listing = {"id": "x", "name": "Saadian Tombs", "kind": "see", "price_raw": "100 dirham", "country": "morocco",
               "source_url": "https://en.wikivoyage.org/wiki/Marrakech", "city": "Marrakech", "last_edit": None}
    item = td.enrich(listing)
    assert item["price"]["currency"] == "MAD" and "country" not in item


@pytest.fixture
def rates(monkeypatch):
    """TEST FIXTURE: the shapes of the two rate services."""
    ecb = {"kind": "fx", "base": "EUR", "date": "2026-11-04", "rates": {"USD": 1.1, "KRW": 1500.0}, "source": "Frankfurter (ECB reference rates)",
           "source_url": "https://ecb.example", "licence": "ECB", "retrieved_at": "t"}
    market = {"kind": "fx", "base": "USD", "date": "2026-11-03", "rates": {"EGP": 50.0, "MAD": 10.0}, "source": "ExchangeRate-API open rates",
              "source_url": "https://open.example", "licence": "attribution", "retrieved_at": "t"}
    monkeypatch.setattr(td, "fx_rates", lambda: (ecb, "live"))
    monkeypatch.setattr(td, "open_fx_rates", lambda: (market, "live"))


def test_a_currency_the_ecb_does_not_publish_comes_from_the_second_source_and_says_so(rates):
    out = td.convert(100, "EGP", "USD")
    assert out["rate"] == 0.02 and out["converted"] == 2.0
    assert out["method"] == "market_rate_via_usd" and out["rate_date"] == "2026-11-03"
    assert "ExchangeRate-API" in out["citation"]["title"] and "ExchangeRate-API" in out["source"]
    assert "ExchangeRate-API" in ExchangeRate.from_tool_result({**out, "ok": True}).describe()
    assert "ECB reference rate" not in ExchangeRate.from_tool_result({**out, "ok": True}).describe()


def test_a_published_currency_stays_on_the_ecb_rate_with_enough_digits(rates):
    out = td.convert(1, "KRW", "USD")
    assert out["method"] == "ecb_reference_rate" and out["rate"] == round(1.1 / 1500, 9)


def test_a_currency_no_source_knows_is_an_error_never_a_guess(rates):
    with pytest.raises(td.DataError) as e:
        td.convert(1, "XXX", "USD")
    assert e.value.code == "unknown_currency"


def test_without_the_second_source_the_rate_is_unknown(rates, monkeypatch):
    def down():
        raise td.DataError(503, "upstream_unavailable", "down")
    monkeypatch.setattr(td, "open_fx_rates", down)
    with pytest.raises(td.DataError) as e:
        td.convert(1, "EGP", "USD")
    assert e.value.code == "unknown_currency"


def test_the_usual_spelling_finds_the_city():
    assert destinations.SPELLINGS["marrakech"] == "Marrakesh" and td.CITY_SPELLINGS["marrakech"] == "Marrakesh"


# ------------------------------------------------------------------ what five new cities showed about selection
def listing(name, kind="see", description="", **more):
    return {"id": name, "name": name, "kind": kind, "description": description, "lat": 1.0, "tags": [], **more}


def test_a_cinema_is_not_history_and_a_roman_theatre_is():
    assert "history" not in td.tags_for("The movie theater is easily accessible", "do", "Lotte Cinema")
    assert "history" in td.tags_for("A Roman theatre of the 2nd century", "see", "Roman Theatre")
    assert "history" in td.tags_for("A palace with a theatre", "see", "Royal Palace")


@pytest.mark.parametrize("text", ["One of the defining landmarks of the city", "One of the best known monuments in Budapest",
                                  "This is Seoul's grandest palace", "A UNESCO World Heritage Site",
                                  "The largest in Europe"])
def test_a_guide_singles_out_its_great_sights_in_its_own_words(text):
    assert td.is_landmark(listing("Sight", description=text))


def test_only_a_sight_is_a_landmark_and_plain_text_is_not():
    assert not td.is_landmark(listing("Mall", "buy", "An iconic mall"))
    assert not td.is_landmark(listing("Museum", description="A small local museum"))


def test_landmarks_lead_but_only_up_to_the_cap_and_nothing_is_lost():
    items = [listing(f"Mall {n}", "buy") for n in range(3)] + [listing(f"Sight {n}", description="A landmark") for n in range(15)]
    out = td.landmarks_first(items)
    assert len(out) == len(items) and {i["id"] for i in out} == {i["id"] for i in items}
    assert all(i["kind"] == "see" for i in out[:td.MAX_LANDMARKS_FIRST]) and out[td.MAX_LANDMARKS_FIRST]["id"] == "Mall 0"


@pytest.mark.parametrize("text, day, expected", [
    ("8AM-noon, 1-4PM", "Mon", ["08:00-12:00"]),
    ("daily noon-midnight", "Tue", ["12:00-24:00"]),
    ("9AM-Midnight", "Wed", ["09:00-24:00"]),
])
def test_noon_and_midnight_are_times(text, day, expected):
    hours = td.parse_hours(text)
    assert hours["parsed"] is True and hours["weekly"][day][:1] == expected
    assert hours["raw"] == text  # the listing's words stay the authority


@pytest.mark.parametrize("text, expected", [
    ("1-4PM", "13:00-16:00"), ("6-11PM", "18:00-23:00"), ("9-5pm", "09:00-17:00"), ("11-2pm", "11:00-14:00"),
    ("12-4pm", "12:00-16:00"), ("9AM-5PM", "09:00-17:00"), ("10:00-18:00", "10:00-18:00"),
])
def test_the_end_of_a_range_says_whether_its_start_is_in_the_afternoon(text, expected):
    assert td.parse_hours(text)["weekly"]["Mon"] == [expected]


def test_a_guides_landmark_counts_as_history_and_culture():
    assert "history" in td.tags_for("One of the defining landmarks of the city. The cable car", "see", "Sugar Loaf")
    assert "history" not in td.tags_for("An iconic mall", "buy", "Mall")


@pytest.mark.parametrize("text", ["Not open to the public", "Closed to visitors"])
def test_a_place_not_open_to_the_public_is_closed(text):
    assert td.parse_hours(text)["temporarily_closed"] is True


def test_closed_to_the_public_on_some_days_is_not_closed_for_good():
    assert td.parse_hours("Tue-Sun 09:00-17:00, closed to the public on Mon")["temporarily_closed"] is False


def test_a_lookup_in_flight_cannot_outlast_the_time_limit():
    import time
    from planning.schedule import Stop, TravelTimes
    from planning.verification import FreshLookups

    def slow(*args):
        time.sleep(1.5)
        return {"ok": True, "duration_min": 5}

    started = time.monotonic()
    fresh = FreshLookups(None, slow, seconds=0.2)
    assert fresh.travel("A", "B") is None and fresh.skipped == 1  # not answered in time: unknown, never assumed
    a, b = (Stop(place_id=i, name=i, category="attraction") for i in "ab")
    leg = TravelTimes(slow, seconds=0.2).leg(a, b)
    assert leg.status == "unavailable" and leg.minutes is None
    assert time.monotonic() - started < 1.0


def test_without_a_limit_a_slow_lookup_is_waited_for():
    from planning.verification import FreshLookups
    assert FreshLookups(None, lambda *a: {"ok": True, "duration_min": 5}).travel("A", "B")["duration_min"] == 5
