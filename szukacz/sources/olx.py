"""OLX: pobiera stronę wyników wyszukiwania i czyta JSON osadzony w window.__PRERENDERED_STATE__.

OLX (CloudFront) blokuje zwykłych klientów HTTP po odcisku TLS, dlatego używamy curl_cffi,
który podszywa się pod przeglądarkę Chrome.
"""

import json
import logging
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from curl_cffi import requests

from ..config import Search
from ..models import Listing
from .base import SourceError

log = logging.getLogger(__name__)

STATE_RE = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")')
ORDER_KEY = "search[order]"


def with_newest_first(url: str) -> str:
    """Wymusza sortowanie od najnowszych, jeżeli link go nie ma."""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(k == ORDER_KEY for k, _ in query):
        query.append((ORDER_KEY, "created_at:desc"))
    return urlunsplit(parts._replace(query=urlencode(query, safe=":[]")))


def extract_ads(html: str) -> list[dict]:
    m = STATE_RE.search(html)
    if not m:
        raise SourceError("OLX: nie znaleziono __PRERENDERED_STATE__ (zmiana strony albo blokada)")
    state = json.loads(json.loads(m.group(1)))
    try:
        return state["listing"]["listing"]["ads"]
    except (KeyError, TypeError) as e:
        raise SourceError(f"OLX: nieoczekiwana struktura danych ({e})") from e


def _float(value) -> float | None:
    try:
        return float(str(value).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _dt(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def parse_ad(ad: dict) -> Listing:
    params = {p["key"]: p for p in ad.get("params") or []}

    def label(key):
        return params[key]["value"] if key in params else None

    def number(key):
        return _float(params[key].get("normalizedValue")) if key in params else None

    price = ad.get("price") or {}
    regular = price.get("regularPrice") or {}
    loc = ad.get("location") or {}
    location = ", ".join(x for x in (loc.get("cityName"), loc.get("districtName")) if x) or loc.get("pathName")
    partner = (ad.get("partner") or {}).get("code") or None
    photos = ad.get("photos") or []

    return Listing(
        source="olx",
        id=str(ad["id"]),
        title=ad.get("title", "").strip(),
        url=ad.get("url") or "",
        price=_float(regular.get("value")),
        price_display=price.get("displayValue"),
        area_m2=number("m"),
        price_per_m2=number("price_per_m"),
        rooms=label("rooms"),
        floor=label("floor_select"),
        plot_type=label("type"),
        market=label("market"),
        market_key=params["market"].get("normalizedValue") if "market" in params else None,
        location=location,
        created_at=_dt(ad.get("createdTime")),
        photo_url=photos[0] if photos else None,
        partner=partner,
        description=re.sub(r"<[^>]+>", " ", ad.get("description") or ""),
        extra={"external_url": ad.get("externalUrl"), "promoted": bool(ad.get("isPromoted"))},
    )


class OlxSource:
    name = "olx"

    def __init__(self, timeout: int = 30):
        self.session = requests.Session(impersonate="chrome")
        self.timeout = timeout

    def fetch(self, search: Search) -> list[Listing]:
        url = with_newest_first(search.url)
        try:
            r = self.session.get(url, timeout=self.timeout, headers={"Accept-Language": "pl-PL,pl;q=0.9"})
        except Exception as e:  # curl_cffi zgłasza własne typy błędów sieci
            raise SourceError(f"OLX: błąd sieci: {e}") from e
        if r.status_code != 200:
            raise SourceError(f"OLX: HTTP {r.status_code} dla {url}")

        listings = []
        for ad in extract_ads(r.text):
            try:
                listings.append(parse_ad(ad))
            except Exception:
                log.exception("OLX: nie udało się sparsować ogłoszenia %s", ad.get("id"))
        return listings
