"""Morizon: pobiera stronę wyników i czyta karty ogłoszeń po atrybutach data-cy.

Morizon nie ma wyszukiwania w promieniu – zamiast tego używaj linków do miasta / powiatu,
np. https://www.morizon.pl/mieszkania/najnowsze/lublin/ albo .../najnowsze/lubelskie/swidnicki/.
Uwaga: /swidnicki/ bez województwa to powiat świdnicki na Dolnym Śląsku.
"""

import html
import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests

from ..config import Search
from ..models import Listing
from .base import SourceError

log = logging.getLogger(__name__)

BASE = "https://www.morizon.pl"
CARD_SPLIT_RE = re.compile(r'(?=<div[^>]*data-property-id=")')
TAG_RE = re.compile(r"<[^>]+>")
SVG_RE = re.compile(r"<svg.*?</svg>", re.S)


def with_newest_first(url: str) -> str:
    """Morizon sortuje po dacie, gdy ścieżka zawiera /najnowsze/."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) >= 2 and "najnowsze" not in segments:
        segments.insert(1, "najnowsze")
        url = parts._replace(path="/" + "/".join(segments) + "/").geturl()
    return url


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", fragment))).strip()


def _cy(card: str, name: str) -> str | None:
    """Tekst elementu z atrybutem data-cy=name (do pierwszego tagu zamykającego, po którym jest tekst)."""
    m = re.search(rf'data-cy="{name}"[^>]*>', card)
    if not m:
        return None
    for end in re.finditer(r"</(?:div|span|p|h\d|a|li)>", card[m.end() : m.end() + 2000]):
        text = _text(card[m.end() : m.end() + end.start()])
        if text:
            return text
    return None


def _number(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\d[\d\s ]*(?:[.,]\d+)?", text)
    if not m:
        return None
    try:
        return float(re.sub(r"[\s ]", "", m.group(0)).replace(",", "."))
    except ValueError:
        return None


def parse_card(card: str) -> Listing | None:
    card = SVG_RE.sub("", card)
    m = re.search(r'data-property-id="(\d+)"', card)
    href = re.search(r'href="(/oferta/[^"]+)"', card)
    if not m or not href:
        return None

    price_text = _cy(card, "cardPropertyOfferPrice")
    location = _cy(card, "propertyCardLocation")
    if location:
        # "ulica, dzielnica, miasto, województwo" -> bez województwa
        location = ", ".join(location.split(", ")[:-1]) or location

    added = _cy(card, "descriptionAddedAtDate")
    created_at = None
    if added and (d := re.search(r"\d{4}\.\d{2}\.\d{2}", added)):
        created_at = datetime.strptime(d.group(0), "%Y.%m.%d").astimezone()

    photo = re.search(r'src="(https://img\d\.staticmorizon\.com\.pl/thumb/[^"]+)"', card)
    photo_url = photo.group(1).replace("3x2_xs:", "3x2_l:") if photo else None

    floor = _cy(card, "cardPropertyInfoFloor")
    description = re.search(r'description__advertisement-text[^>]*>[^<]*</span><div[^>]*>([^<]*)', card)

    return Listing(
        source="morizon",
        id=m.group(1),
        title=_cy(card, "propertyCardTitle") or "",
        url=urljoin(BASE, href.group(1)),
        price=_number(price_text),
        price_display=price_text,
        area_m2=_number(_cy(card, "cardPropertyInfoArea")),
        price_per_m2=_number(_cy(card, "offerPricePerM2")),
        rooms=_cy(card, "cardPropertyInfoRooms"),
        floor=floor.split("/")[0] if floor else None,
        location=location,
        created_at=created_at,
        photo_url=photo_url,
        description=_text(description.group(1)) if description else "",
    )


def extract_listings(page: str) -> list[Listing]:
    cards = CARD_SPLIT_RE.split(page)[1:]
    listings = []
    for card in cards:
        try:
            listing = parse_card(card)
        except Exception:
            log.exception("Morizon: nie udało się sparsować karty")
            continue
        if listing:
            listings.append(listing)
    return listings


class MorizonSource:
    name = "morizon"

    def __init__(self, timeout: int = 30):
        self.session = requests.Session(impersonate="chrome")
        self.timeout = timeout

    def fetch(self, search: Search) -> list[Listing]:
        url = with_newest_first(search.url)
        try:
            r = self.session.get(url, timeout=self.timeout, headers={"Accept-Language": "pl-PL,pl;q=0.9"})
        except Exception as e:
            raise SourceError(f"Morizon: błąd sieci: {e}") from e
        if r.status_code != 200:
            raise SourceError(f"Morizon: HTTP {r.status_code} dla {url}")

        listings = extract_listings(r.text)
        if not listings and "data-property-id" not in r.text and "Brak ogłoszeń" not in r.text:
            raise SourceError("Morizon: nie znaleziono kart ogłoszeń (zmiana strony albo blokada)")
        return listings
