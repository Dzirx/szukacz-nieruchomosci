from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

TYPES = ("mieszkanie", "dzialka")
SOURCES = ("olx", "morizon", "facebook_group")
MARKETS = {"wtorny": "secondary", "pierwotny": "primary"}


@dataclass
class Search:
    name: str
    type: str
    url: str
    source: str = "olx"
    max_age_days: int | None = 14
    include_otodom: bool = True
    exclude_keywords: list[str] = field(default_factory=list)
    min_area_m2: float | None = None
    max_area_m2: float | None = None
    max_price: float | None = None
    max_price_per_m2: float | None = None
    market: str | None = None  # "wtorny" | "pierwotny" (tylko mieszkania)
    min_price: float | None = None
    require_keywords: list[str] = field(default_factory=list)  # wystarczy jedno słowo
    exclude_title_keywords: list[str] = field(default_factory=list)  # sprawdzane tylko w tytule / 1. linii
    extra_urls: list[str] = field(default_factory=list)  # facebook_group: kolejne grupy w tym samym uruchomieniu
    interval_minutes: float | None = None  # nadpisuje globalny interwał (np. rzadziej dla FB)
    max_results: int = 30  # limit wyników na grupę na uruchomienie (koszt Apify)
    run_at: list[str] = field(default_factory=list)  # godziny uruchomień, np. ["10:00", "18:00"]


def previous_slot(run_at: list[str], now: datetime) -> datetime:
    """Ostatnia zaplanowana godzina uruchomienia <= now."""
    candidates = []
    for t in run_at:
        hh, mm = map(int, t.split(":"))
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        candidates.append(slot if slot <= now else slot - timedelta(days=1))
    return max(candidates)


def slot_before(run_at: list[str], slot: datetime) -> datetime:
    """Zaplanowana godzina poprzedzająca dany slot."""
    return previous_slot(run_at, slot - timedelta(minutes=1))


@dataclass
class Config:
    searches: list[Search]
    interval_minutes: float = 10
    jitter_seconds: int = 60
    first_run_silent: bool = True
    notify_price_drops: bool = True
    send_photos: bool = True


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    searches_raw = raw.pop("searches", None) or []
    if not searches_raw:
        raise ValueError("config: brak sekcji 'searches'")

    searches = []
    names = set()
    for i, s in enumerate(searches_raw):
        try:
            search = Search(**s)
        except TypeError as e:
            raise ValueError(f"config: wyszukiwanie #{i + 1}: {e}") from e
        if search.type not in TYPES:
            raise ValueError(f"config: '{search.name}': type musi być jednym z {TYPES}")
        if search.source not in SOURCES:
            raise ValueError(f"config: '{search.name}': source musi być jednym z {SOURCES}")
        if search.market not in (None, *MARKETS):
            raise ValueError(f"config: '{search.name}': market musi być jednym z {tuple(MARKETS)}")
        for t in search.run_at:
            try:
                datetime.strptime(t, "%H:%M")
            except ValueError as e:
                raise ValueError(f"config: '{search.name}': run_at '{t}' – oczekiwany format HH:MM") from e
        if search.name in names:
            raise ValueError(f"config: zduplikowana nazwa wyszukiwania '{search.name}'")
        names.add(search.name)
        searches.append(search)

    try:
        return Config(searches=searches, **raw)
    except TypeError as e:
        raise ValueError(f"config: {e}") from e
