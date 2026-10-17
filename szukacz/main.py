import argparse
from datetime import datetime
import logging
import os
import random
import signal
import sys
import time

from .config import Config, Search, load_config, previous_slot
from .filters import rejection_reason
from .notifier import ConsoleNotifier, TelegramNotifier
from .sources import SourceError, build_sources
from .storage import Storage

log = logging.getLogger("szukacz")

FAILURES_BEFORE_ALERT = 3


class Runner:
    def __init__(self, config: Config, storage: Storage, notifier, sources):
        self.config = config
        self.storage = storage
        self.notifier = notifier
        self.sources = sources
        self.failures: dict[str, int] = {}
        self.last_run: dict[str, float] = {}

    def is_due(self, search: Search) -> bool:
        if search.run_at:
            # stan w bazie, żeby restart kontenera nie powodował dodatkowego (płatnego) uruchomienia
            slot = previous_slot(search.run_at, datetime.now()).isoformat(timespec="minutes")
            last = self.storage.get_last_slot(search.name)
            if last is None:
                # pierwszy start: nie nadrabiaj zaległego slotu, czekaj na następny
                self.storage.set_last_slot(search.name, slot)
                return False
            if slot == last:
                return False
            self.storage.set_last_slot(search.name, slot)
            return True
        if search.interval_minutes is None:
            return True
        last = self.last_run.get(search.name)
        # tolerancja na jitter głównej pętli
        return last is None or time.monotonic() - last >= search.interval_minutes * 60 - self.config.jitter_seconds - 30

    def run_search(self, search: Search) -> int:
        listings = self.sources[search.source].fetch(search)
        silent = self.config.first_run_silent and not self.storage.is_seeded(search.name)
        sent = 0

        # od najstarszych, żeby na Telegramie najnowsze były na dole
        for listing in reversed(listings):
            reason = rejection_reason(listing, search)
            if reason:
                log.debug("[%s] pomijam %s: %s", search.name, listing.id, reason)
                continue

            seen, old_price = self.storage.get_price(listing.source, listing.id)
            if not seen and self.storage.is_duplicate(listing.source, listing.id, listing.fingerprint):
                log.debug("[%s] pomijam %s: duplikat", search.name, listing.id)
            elif not seen:
                if not silent and not self.notifier.send_listing(listing, search):
                    continue  # nie zapisuj, spróbujemy w następnym cyklu
                if not silent:
                    sent += 1
            elif (
                self.config.notify_price_drops
                and not silent
                and old_price is not None
                and listing.price is not None
                and listing.price < old_price
            ):
                if not self.notifier.send_listing(listing, search, old_price=old_price):
                    continue
                sent += 1
            self.storage.mark_seen(listing.source, listing.id, listing.price, listing.fingerprint)

        if silent:
            self.storage.mark_seeded(search.name)
            log.info("[%s] pierwsze uruchomienie: zapamiętano %d ogłoszeń bez wysyłki", search.name, len(listings))
        else:
            log.info("[%s] pobrano %d, wysłano %d", search.name, len(listings), sent)
        return sent

    def run_cycle(self) -> None:
        for search in self.config.searches:
            if not self.is_due(search):
                continue
            self.last_run[search.name] = time.monotonic()
            try:
                self.run_search(search)
            except SourceError as e:
                self._on_failure(search, str(e))
            except Exception as e:
                log.exception("[%s] nieoczekiwany błąd", search.name)
                self._on_failure(search, repr(e))
            else:
                if self.failures.get(search.name, 0) >= FAILURES_BEFORE_ALERT:
                    self.notifier.send_text(f"✅ <i>{search.name}</i>: wyszukiwanie znowu działa.")
                self.failures[search.name] = 0
            time.sleep(random.uniform(2, 6))  # nie zasypuj portalu zapytaniami

    def _on_failure(self, search: Search, message: str) -> None:
        count = self.failures.get(search.name, 0) + 1
        self.failures[search.name] = count
        log.warning("[%s] błąd (%d z rzędu): %s", search.name, count, message)
        if count == FAILURES_BEFORE_ALERT:
            self.notifier.send_text(
                f"⚠️ <i>{search.name}</i>: {count} nieudane próby z rzędu.\n<code>{message[:500]}</code>"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Szukacz nieruchomości → Telegram")
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", "config.yaml"))
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "data/szukacz.db"))
    parser.add_argument("--once", action="store_true", help="jeden cykl i koniec")
    parser.add_argument("--dry-run", action="store_true", help="wypisz w konsoli, nie wysyłaj i nie zapisuj stanu")
    parser.add_argument("--test-telegram", action="store_true", help="wyślij wiadomość testową i zakończ")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)

    if args.dry_run:
        notifier = ConsoleNotifier()
        storage = Storage(":memory:")
        config.first_run_silent = False
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            sys.exit("Brak TELEGRAM_BOT_TOKEN lub TELEGRAM_CHAT_ID (plik .env)")
        notifier = TelegramNotifier(token, chat_id, send_photos=config.send_photos)
        storage = Storage(args.db)

    if args.test_telegram:
        ok = notifier.send_text("✅ Szukacz nieruchomości: połączenie z Telegramem działa.")
        sys.exit(0 if ok else 1)

    runner = Runner(config, storage, notifier, build_sources())

    if args.once or args.dry_run:
        runner.run_cycle()
        return

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log.info("Start: %d wyszukiwań, co %s min", len(config.searches), config.interval_minutes)
    while True:
        runner.run_cycle()
        delay = config.interval_minutes * 60 + random.uniform(-config.jitter_seconds, config.jitter_seconds)
        time.sleep(max(60, delay))


if __name__ == "__main__":
    main()
