# Szukacz nieruchomości → Telegram

Co 10 minut sprawdza wyszukiwania na OLX (mieszkania, działki). Gdy pojawi się nowe ogłoszenie
(albo spadnie cena już widzianego), wysyła na Telegram podsumowanie i link.

## Konfiguracja

1. `cp .env.example .env` i wpisz:
   - `TELEGRAM_BOT_TOKEN` – token od @BotFather,
   - `TELEGRAM_CHAT_ID` – napisz do bota dowolną wiadomość, potem otwórz
     `https://api.telegram.org/bot<TOKEN>/getUpdates` i skopiuj `message.chat.id`.
2. `cp config.example.yaml config.yaml` i ustaw wyszukiwania.
   Najprościej: ustaw filtry na olx.pl, skopiuj link z przeglądarki do pola `url`.
   Sortowanie „najnowsze” program dodaje sam.

## Uruchomienie na VPS

```bash
mkdir -p data
docker compose up -d --build
docker compose logs -f
```

Pierwszy cykl zapamiętuje obecne ogłoszenia **bez wysyłki** (`first_run_silent`).
Potem przychodzą tylko nowe. Stan jest w `data/szukacz.db` (SQLite).
Po zmianie `config.yaml`: `docker compose restart`.

## Narzędzia

```bash
docker compose run --rm szukacz python -m szukacz.main --config /app/config.yaml --test-telegram  # test bota
docker compose run --rm szukacz python -m szukacz.main --config /app/config.yaml --dry-run        # podgląd bez wysyłki
```

Lokalnie bez Dockera: `pip install -r requirements-dev.txt`, `pytest`, `python -m szukacz.main --dry-run`.

## Źródła

- **OLX** – link z filtrami z olx.pl, co 10 min.
- **Morizon** – link do miasta lub powiatu (Morizon nie ma promienia). Uwaga: `/swidnicki/` to Dolny Śląsk, użyj `/lubelskie/swidnicki/`.
- **Grupy Facebook** (tylko publiczne) – przez Apify (`apify/facebook-groups-scraper`), wymaga `APIFY_TOKEN` w `.env`.
  Godziny w `run_at` (np. 10:00 i 18:00), kolejne grupy w `extra_urls` (jedno uruchomienie = jedna opłata startowa).
  Koszt ok. 0,007 USD za post – zależy od liczby postów w grupach, nie od liczby uruchomień.
  Dane autorów postów nie są zapisywane ani wysyłane.

Duplikaty: ta sama oferta (cena + metraż) z innego portalu i ten sam post FB w kilku grupach nie przychodzą drugi raz.

## Uwagi

- OLX blokuje zwykłe klienty HTTP – używamy `curl_cffi` (odcisk przeglądarki Chrome).
  Gdy OLX zmieni stronę, po 3 nieudanych próbach przyjdzie alert na Telegram.
- Automatyczne pobieranie danych podlega regulaminowi OLX. Nie skracaj interwału poniżej kilku minut.
