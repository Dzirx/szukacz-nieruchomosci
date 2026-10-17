import html
import logging
import time

from curl_cffi import requests

from .config import Search
from .models import Listing

log = logging.getLogger(__name__)

TYPE_LABELS = {"mieszkanie": "🏠", "dzialka": "🌳"}


def _num(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def format_listing(listing: Listing, search: Search, old_price: float | None = None) -> str:
    e = html.escape
    lines = [f"{TYPE_LABELS.get(search.type, '🏷')} <i>{e(search.name)}</i>"]
    if old_price is not None and listing.price is not None:
        drop = old_price - listing.price
        lines.append(f"📉 <b>OBNIŻKA CENY</b> o {_num(drop)} zł (było {_num(old_price)} zł)")
    lines.append(f"<b>{e(listing.title)}</b>")

    main = []
    if listing.price_display:
        main.append(f"💰 {e(listing.price_display)}")
    elif listing.price is not None:
        main.append(f"💰 {_num(listing.price)} zł")
    if listing.area_m2 is not None:
        main.append(f"📐 {_num(listing.area_m2, 0 if listing.area_m2.is_integer() else 2)} m²")
    if listing.price_per_m2 is not None:
        main.append(f"💵 {_num(listing.price_per_m2)} zł/m²")
    if main:
        lines.append("  |  ".join(main))

    details = []
    if search.type == "mieszkanie":
        if listing.rooms:
            details.append(f"🚪 {e(listing.rooms)}")
        if listing.floor:
            floor = listing.floor
            details.append(f"🏢 piętro {e(floor)}" if floor[:1].isdigit() else f"🏢 {e(floor.lower())}")
        if listing.market:
            details.append(f"🏷 rynek {e(listing.market.lower())}")
    elif listing.plot_type:
        details.append(f"🏷 {e(listing.plot_type)}")
    if details:
        lines.append("  |  ".join(details))

    if listing.location:
        lines.append(f"📍 {e(listing.location)}")
    if listing.created_at:
        lines.append(f"🕒 dodano {listing.created_at.strftime('%d.%m.%Y %H:%M')}")
    if listing.source == "facebook":
        lines.append(f"👥 grupa FB: {e(listing.extra.get('group') or '?')}")
        snippet = listing.description[:350] + ("…" if len(listing.description) > 350 else "")
        if snippet and snippet != listing.title:
            lines.append(f"<blockquote>{e(snippet)}</blockquote>")
    elif listing.source != "olx":
        lines.append(f"🌐 {listing.source.capitalize()}")
    if listing.partner == "otodom_pl":
        lines.append("ℹ️ ogłoszenie z Otodom")

    lines.append(f'🔗 <a href="{e(listing.url, quote=True)}">Otwórz ogłoszenie</a>')
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, send_photos: bool = True, timeout: int = 30):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.send_photos = send_photos
        self.timeout = timeout
        self.session = requests.Session()

    def _call(self, method: str, payload: dict, attempts: int = 3) -> bool:
        for _ in range(attempts):
            try:
                r = self.session.post(f"{self.base}/{method}", json=payload, timeout=self.timeout)
                data = r.json()
            except Exception as e:
                log.warning("Telegram %s: błąd sieci: %s", method, e)
                time.sleep(5)
                continue
            if data.get("ok"):
                return True
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if r.status_code == 429 and retry_after:
                log.info("Telegram: limit wysyłki, czekam %s s", retry_after)
                time.sleep(retry_after + 1)
                continue
            log.warning("Telegram %s: %s", method, data.get("description"))
            return False
        return False

    def send_text(self, text: str) -> bool:
        ok = self._call(
            "sendMessage",
            {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        )
        time.sleep(1)
        return ok

    def send_listing(self, listing: Listing, search: Search, old_price: float | None = None) -> bool:
        text = format_listing(listing, search, old_price)
        if self.send_photos and listing.photo_url and len(text) <= 1024:
            ok = self._call(
                "sendPhoto",
                {"chat_id": self.chat_id, "photo": listing.photo_url, "caption": text, "parse_mode": "HTML"},
            )
            if ok:
                time.sleep(1)
                return True
            log.info("Telegram: zdjęcie nie przeszło, wysyłam sam tekst")
        return self.send_text(text)


class ConsoleNotifier:
    """Tryb --dry-run: wypisuje wiadomości zamiast wysyłać."""

    def send_text(self, text: str) -> bool:
        print(text, end="\n\n")
        return True

    def send_listing(self, listing: Listing, search: Search, old_price: float | None = None) -> bool:
        return self.send_text(format_listing(listing, search, old_price))
