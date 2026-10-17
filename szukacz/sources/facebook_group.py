"""Publiczne grupy na Facebooku przez Apify (actor apify/facebook-groups-scraper).

Nie wymaga logowania do Facebooka. Koszt (plan FREE, 10.2026): 0,005 USD za post,
0,001 USD za uruchomienie, filtr daty +0,002 USD za post. Filtr daty sprawia,
że płacimy tylko za nowe posty, a nie za całą grupę przy każdym uruchomieniu.

Dane autorów (imię, ID, komentarze) są pomijane – nie trafiają do bazy ani na Telegram.
"""

import hashlib
import logging
import os
import re
from datetime import datetime

from curl_cffi import requests

from ..config import Search, previous_slot, slot_before
from ..models import Listing
from .base import SourceError

log = logging.getLogger(__name__)

ACTOR = "apify~facebook-groups-scraper"
API = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

NUM = r"\d{1,3}(?:[  .]\d{3})+|\d+(?:[.,]\d+)?"
AREA_RE = re.compile(rf"({NUM})(?:\s*(?:m2|m²|mkw|m\.\s?kw|metr[óo]w)|m\b)", re.I)
PRICE_PLN_RE = re.compile(rf"({NUM})\s*(?:zł|zl|pln)\b", re.I)
PRICE_K_RE = re.compile(rf"({NUM})\s*(?:tys\.?|tyś\.?|tysięcy|k)(?![a-z])\s*(/\s*m|za\s*m|zł\s*/\s*m)?", re.I)
PER_M2_RE = re.compile(rf"({NUM})\s*(?:zł|zl|pln)?\s*(?:/|za)\s*(?:m2|m²|mkw|m\b|metr)", re.I)


def _to_float(raw: str) -> float | None:
    s = raw.replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:[ .]\d{3})+", s):
        s = re.sub(r"[ .]", "", s)
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def parse_area(text: str) -> float | None:
    for m in AREA_RE.finditer(text):
        v = _to_float(m.group(1))
        if v and 10 <= v <= 300:
            return v
    return None


def parse_price(text: str, area: float | None) -> tuple[float | None, float | None]:
    """Zwraca (cena, cena_za_m2) odczytane z tekstu posta."""
    per_m2 = None
    for m in PRICE_K_RE.finditer(text):
        v = _to_float(m.group(1))
        if not v:
            continue
        if m.group(2):  # "7,4 tys/m2"
            per_m2 = per_m2 or v * 1000
        elif 30 <= v <= 5000:  # "380 tys."
            return v * 1000, None
    for m in PER_M2_RE.finditer(text):
        v = _to_float(m.group(1))
        if v and 2000 <= v <= 40000:
            per_m2 = per_m2 or v
    for m in PRICE_PLN_RE.finditer(text):
        v = _to_float(m.group(1))
        if v and 30_000 <= v <= 5_000_000:
            return v, None
    if per_m2 and area:
        return round(per_m2 * area), per_m2
    return None, per_m2


def _structured_price(value) -> float | None:
    """Pole price z Apify ma postać 'PLN449,000'."""
    if not value:
        return None
    digits = re.sub(r"[^\d.]", "", str(value).replace(",", ""))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def parse_post(post: dict, group_name: str | None = None) -> Listing | None:
    shared = post.get("sharedPost") or {}
    text = (post.get("text") or shared.get("text") or "").replace("+", " ").strip()
    title = (post.get("title") or "").replace("+", " ").strip()
    if not text and not title:
        return None

    full = f"{title}\n{text}"
    area = parse_area(full)
    price = _structured_price(post.get("price"))
    per_m2 = None
    if price is None:
        price, per_m2 = parse_price(full, area)
    if per_m2 is None and price and area:
        per_m2 = round(price / area)

    if not title:
        first_line = text.split("\n", 1)[0]
        title = first_line[:100] + ("…" if len(first_line) > 100 else "")

    photo = None
    for att in post.get("attachments") or []:
        if isinstance(att, dict) and att.get("thumbnail"):
            photo = att["thumbnail"]
            break

    created = None
    if post.get("time"):
        try:
            created = datetime.fromisoformat(post["time"].replace("Z", "+00:00")).astimezone()
        except ValueError:
            pass

    normalized = re.sub(r"\W+", " ", text.lower())[:300]
    return Listing(
        source="facebook",
        id=str(post.get("legacyId") or post.get("id")),
        title=title,
        url=post.get("url") or "",
        price=price,
        price_display=f"{price:,.0f} zł".replace(",", " ") if price else None,
        area_m2=area,
        price_per_m2=per_m2,
        location=post.get("location") or group_name or post.get("groupTitle"),
        created_at=created,
        photo_url=photo,
        description=text,
        extra={
            "group": post.get("groupTitle") or group_name,
            "text_fp": "txt:" + hashlib.sha1(normalized.encode()).hexdigest()[:16] if normalized.strip() else None,
        },
    )


class FacebookGroupSource:
    name = "facebook"

    def __init__(self, token: str | None = None, timeout: int = 280):
        self.token = token or os.environ.get("APIFY_TOKEN")
        self.timeout = timeout
        self.session = requests.Session()

    def fetch(self, search: Search) -> list[Listing]:
        if not self.token:
            raise SourceError("Facebook: brak APIFY_TOKEN w .env")
        # okno czasu trochę dłuższe niż odstęp między uruchomieniami, żeby nic nie wypadło
        if search.run_at:
            now = datetime.now()
            window = int((now - slot_before(search.run_at, previous_slot(search.run_at, now))).total_seconds() // 60) + 30
        else:
            window = int(search.interval_minutes or 60) + 15
        payload = {
            "startUrls": [{"url": u} for u in [search.url, *search.extra_urls]],
            "viewOption": "CHRONOLOGICAL",
            "onlyPostsNewerThan": f"{window} minutes",
            "resultsLimit": search.max_results,
        }
        try:
            r = self.session.post(
                API,
                # twardy limit kosztu jednego uruchomienia (0,007 USD/post + start)
                params={"timeout": self.timeout, "maxTotalChargeUsd": round(search.max_results * (1 + len(search.extra_urls)) * 0.007 + 0.01, 3)},
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload,
                timeout=self.timeout + 30,
            )
        except Exception as e:
            raise SourceError(f"Facebook/Apify: błąd sieci: {e}") from e
        if r.status_code not in (200, 201):
            raise SourceError(f"Facebook/Apify: HTTP {r.status_code}: {r.text[:200]}")

        items = r.json()
        if isinstance(items, dict):  # błąd w formie obiektu
            raise SourceError(f"Facebook/Apify: {items.get('error', items)}")

        listings = []
        for post in items:
            try:
                listing = parse_post(post)
            except Exception:
                log.exception("Facebook: nie udało się sparsować posta")
                continue
            if listing:
                listings.append(listing)
        listings.sort(key=lambda l: l.created_at or datetime.min.astimezone(), reverse=True)
        return listings
