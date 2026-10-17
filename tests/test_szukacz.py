import json
from datetime import datetime
from pathlib import Path

import pytest

from szukacz.config import Config, Search, load_config
from szukacz.filters import rejection_reason
from szukacz.main import Runner
from szukacz.notifier import format_listing
from szukacz.sources.olx import extract_ads, parse_ad, with_newest_first
from szukacz.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"
ADS = json.loads((FIXTURES / "olx_ads.json").read_text(encoding="utf-8"))
FLAT = Search(name="Mieszkania", type="mieszkanie", url="https://www.olx.pl/x/", max_age_days=None)
PLOT = Search(name="Działki", type="dzialka", url="https://www.olx.pl/y/", max_age_days=None)


def test_parse_flat():
    l = parse_ad(ADS[0])
    assert l.source == "olx" and l.id == str(ADS[0]["id"])
    assert l.price and l.area_m2 and l.price_per_m2
    assert l.rooms and "Kraków" in l.location
    assert l.url.startswith("https://")


def test_parse_plot():
    l = parse_ad(ADS[2])
    assert l.plot_type == "Działki rolne"
    assert l.area_m2 == 1742


def test_extract_ads_from_html():
    state = json.dumps({"listing": {"listing": {"ads": ADS}}})
    html = f"<script>window.__PRERENDERED_STATE__= {json.dumps(state)};</script>"
    assert [a["id"] for a in extract_ads(html)] == [a["id"] for a in ADS]


def test_newest_first_added_once():
    url = with_newest_first("https://www.olx.pl/a/?search%5Bdist%5D=15")
    assert "search[order]=created_at:desc" in url and "search[dist]=15" in url
    assert with_newest_first(url) == url


def test_filters():
    l = parse_ad(ADS[0])
    assert rejection_reason(l, FLAT) is None
    assert rejection_reason(l, Search(**{**FLAT.__dict__, "max_price": 1})) == "cena"
    kw = l.title.split()[0]
    assert rejection_reason(l, Search(**{**FLAT.__dict__, "exclude_keywords": [kw.upper()]}))
    other = "pierwotny" if l.market_key == "secondary" else "wtorny"
    assert rejection_reason(l, Search(**{**FLAT.__dict__, "market": other})) == "rynek"
    old = Search(**{**FLAT.__dict__, "max_age_days": 1})
    assert rejection_reason(l, old, now=datetime.fromisoformat("2030-01-01T00:00:00+01:00"))


def test_format_messages():
    flat = format_listing(parse_ad(ADS[0]), FLAT)
    assert "m²" in flat and "zł/m²" in flat and "href=" in flat
    plot = format_listing(parse_ad(ADS[2]), PLOT, old_price=200000)
    assert "OBNIŻKA" in plot and "Działki rolne" in plot


class FakeSource:
    def __init__(self, listings):
        self.listings = listings

    def fetch(self, search):
        return self.listings


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_listing(self, listing, search, old_price=None):
        self.sent.append((listing.id, old_price))
        return True

    def send_text(self, text):
        self.sent.append(("text", text))
        return True


def test_runner_dedup_seed_and_price_drop():
    listings = [parse_ad(a) for a in ADS[:2]]
    src = FakeSource(listings)
    notifier = FakeNotifier()
    runner = Runner(Config(searches=[FLAT]), Storage(":memory:"), notifier, {"olx": src})

    runner.run_search(FLAT)  # pierwszy raz: cicho
    assert notifier.sent == []

    new = parse_ad({**ADS[2], "id": 1})
    src.listings = [new, *listings]
    runner.run_search(FLAT)
    assert notifier.sent == [("1", None)]

    runner.run_search(FLAT)  # nic nowego
    assert len(notifier.sent) == 1

    listings[0].price -= 10000
    runner.run_search(FLAT)
    assert notifier.sent[-1] == (listings[0].id, listings[0].price + 10000)


def test_example_config_loads():
    cfg = load_config(Path(__file__).parent.parent / "config.example.yaml")
    assert cfg.searches and all(s.type == "mieszkanie" for s in cfg.searches)


def test_config_rejects_bad_type(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("searches:\n  - {name: a, type: dom, url: x}\n")
    with pytest.raises(ValueError):
        load_config(p)


def test_morizon_parse():
    from szukacz.sources.morizon import extract_listings

    ls = extract_listings((FIXTURES / "morizon_cards.html").read_text(encoding="utf-8"))
    assert len(ls) == 2
    for l in ls:
        assert l.source == "morizon" and l.id.isdigit()
        assert l.title and l.price and l.area_m2 and l.price_per_m2 and l.rooms
        assert "Lublin" in l.location and l.url.startswith("https://www.morizon.pl/oferta/")
        assert l.created_at is not None and l.photo_url


def test_morizon_newest_path():
    from szukacz.sources.morizon import with_newest_first

    assert with_newest_first("https://www.morizon.pl/mieszkania/lublin/?a=1") == (
        "https://www.morizon.pl/mieszkania/najnowsze/lublin/?a=1"
    )
    url = "https://www.morizon.pl/mieszkania/najnowsze/lubelskie/swidnicki/"
    assert with_newest_first(url) == url


def test_cross_portal_duplicate_skipped():
    olx = parse_ad(ADS[0])
    twin = parse_ad({**ADS[0], "id": 999})
    twin.source = "morizon"
    src = FakeSource([olx])
    notifier = FakeNotifier()
    cfg = Config(searches=[FLAT], first_run_silent=False)
    runner = Runner(cfg, Storage(":memory:"), notifier, {"olx": src})
    runner.run_search(FLAT)
    src.listings = [twin]
    runner.run_search(FLAT)
    assert notifier.sent == [(olx.id, None)]


def test_facebook_post_parsing():
    from szukacz.sources.facebook_group import parse_post

    # posty syntetyczne (bez prawdziwych danych osobowych)
    l = parse_post({"legacyId": "1", "url": "https://fb/1", "time": "2026-10-16T18:34:16.000Z",
                    "text": "Sprzedam 47m2 do remontu. Lublin, cena 7,4 tys/m2", "groupTitle": "G"})
    assert (l.area_m2, l.price, l.price_per_m2) == (47, 347800, 7400)
    l = parse_post({"legacyId": "2", "url": "u", "title": "Mieszkanie+2+pokoje", "price": "PLN449,000",
                    "location": "Niemce, Powiat Lubelski", "text": "Na sprzedaż 48,4 mkw.", "user": {"name": "X"}})
    assert l.price == 449000 and l.area_m2 == 48.4 and l.title == "Mieszkanie 2 pokoje"
    assert "X" not in format_listing(l, FLAT)
    l = parse_post({"legacyId": "3", "url": "u", "text": "Sprzedam kawalerkę 28 m², cena 299 000 zł"})
    assert (l.price, l.area_m2) == (299000, 28)
    l = parse_post({"legacyId": "4", "url": "u", "text": "Mieszkanie 50 m2 za 380 tys., 300 m od przystanku"})
    assert (l.price, l.area_m2) == (380000, 50)
    assert parse_post({"legacyId": "5", "url": "u", "text": ""}) is None


def test_facebook_repost_is_duplicate():
    from szukacz.sources.facebook_group import parse_post

    post = {"url": "u", "text": "Sprzedam mieszkanie 40 m2, 300 000 zł"}
    a, b = parse_post({**post, "legacyId": "a"}), parse_post({**post, "legacyId": "b"})
    st = Storage(":memory:")
    st.mark_seen(a.source, a.id, a.price, a.fingerprint)
    assert st.is_duplicate(b.source, b.id, b.fingerprint)
    assert not st.is_duplicate(a.source, a.id, a.fingerprint)


def test_search_interval():
    s = Search(name="x", type="mieszkanie", url="u", interval_minutes=60)
    runner = Runner(Config(searches=[s]), Storage(":memory:"), FakeNotifier(), {})
    assert runner.is_due(s)
    runner.last_run["x"] = __import__("time").monotonic()
    assert not runner.is_due(s)


def test_title_exclusions():
    from szukacz.sources.facebook_group import parse_post

    s = Search(name="fb", type="mieszkanie", url="u", max_age_days=None,
               exclude_title_keywords=["działka", "garaż", "poszukuję"])
    plot = parse_post({"legacyId": "1", "url": "u", "text": "Do sprzedania działka budowlana, blisko mieszkania"})
    flat = parse_post({"legacyId": "2", "url": "u", "text": "Sprzedam mieszkanie 40 m2\nW cenie garaż i działka"})
    assert rejection_reason(plot, s) and rejection_reason(flat, s) is None


def test_schedule_slots():
    from szukacz.config import previous_slot, slot_before

    run_at = ["10:00", "18:00"]
    assert previous_slot(run_at, datetime(2026, 10, 17, 9, 0)) == datetime(2026, 10, 16, 18, 0)
    assert previous_slot(run_at, datetime(2026, 10, 17, 10, 5)) == datetime(2026, 10, 17, 10, 0)
    assert previous_slot(run_at, datetime(2026, 10, 17, 23, 0)) == datetime(2026, 10, 17, 18, 0)
    assert slot_before(run_at, datetime(2026, 10, 17, 10, 0)) == datetime(2026, 10, 16, 18, 0)
    assert slot_before(run_at, datetime(2026, 10, 17, 18, 0)) == datetime(2026, 10, 17, 10, 0)


def test_run_at_due_once_per_slot():
    s = Search(name="fb", type="mieszkanie", url="u", run_at=["10:00", "18:00"])
    st = Storage(":memory:")
    runner = Runner(Config(searches=[s]), st, FakeNotifier(), {})
    assert not runner.is_due(s)  # pierwszy start nie nadrabia slotu
    st.set_last_slot("fb", "2000-01-01T10:00")  # symulacja: minął nowy slot
    assert runner.is_due(s)
    assert not runner.is_due(s)  # drugi raz w tym samym slocie – nie
