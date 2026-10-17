from datetime import datetime, timedelta

from .config import MARKETS, Search
from .models import Listing


def rejection_reason(listing: Listing, search: Search, now: datetime | None = None) -> str | None:
    """Zwraca powód odrzucenia ogłoszenia albo None, gdy ogłoszenie pasuje."""
    if not search.include_otodom and listing.partner == "otodom_pl":
        return "otodom"

    if search.market and listing.market_key and listing.market_key != MARKETS[search.market]:
        return "rynek"

    if search.max_age_days is not None and listing.created_at is not None:
        now = now or datetime.now(listing.created_at.tzinfo)
        if now - listing.created_at > timedelta(days=search.max_age_days):
            return "stare ogłoszenie (podbite)"

    title = listing.title.lower()
    for kw in search.exclude_title_keywords:
        if kw.lower() in title:
            return f"słowo wykluczone w tytule: {kw}"

    text = f"{listing.title}\n{listing.description}".lower()
    if search.require_keywords and not any(kw.lower() in text for kw in search.require_keywords):
        return "brak wymaganego słowa"
    for kw in search.exclude_keywords:
        if kw.lower() in text:
            return f"słowo wykluczone: {kw}"

    if search.min_price is not None and listing.price is not None and listing.price < search.min_price:
        return "cena za niska (np. wynajem)"
    if search.max_price is not None and listing.price is not None and listing.price > search.max_price:
        return "cena"
    if listing.area_m2 is not None:
        if search.min_area_m2 is not None and listing.area_m2 < search.min_area_m2:
            return "metraż za mały"
        if search.max_area_m2 is not None and listing.area_m2 > search.max_area_m2:
            return "metraż za duży"
    if (
        search.max_price_per_m2 is not None
        and listing.price_per_m2 is not None
        and listing.price_per_m2 > search.max_price_per_m2
    ):
        return "cena za m²"
    return None
