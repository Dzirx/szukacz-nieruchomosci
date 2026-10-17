import sqlite3
from datetime import datetime
from pathlib import Path


class Storage:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                source TEXT NOT NULL,
                listing_id TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                price REAL,
                PRIMARY KEY (source, listing_id)
            );
            CREATE TABLE IF NOT EXISTS last_slots (
                name TEXT PRIMARY KEY,
                slot TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seeded_searches (
                name TEXT PRIMARY KEY,
                seeded_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(seen)")}
        if "fingerprint" not in columns:
            self.db.execute("ALTER TABLE seen ADD COLUMN fingerprint TEXT")
        self.db.execute("CREATE INDEX IF NOT EXISTS seen_fingerprint ON seen (fingerprint)")
        self.db.commit()

    def get_price(self, source: str, listing_id: str) -> tuple[bool, float | None]:
        """Zwraca (czy_widziane, zapisana_cena)."""
        row = self.db.execute(
            "SELECT price FROM seen WHERE source = ? AND listing_id = ?", (source, listing_id)
        ).fetchone()
        return (row is not None, row[0] if row else None)

    def is_duplicate(self, source: str, listing_id: str, fingerprint: str | None) -> bool:
        """Cena+metraż: duplikat tylko z innego portalu. Skrót tekstu (txt:): także w tym samym źródle."""
        if fingerprint is None:
            return False
        if fingerprint.startswith("txt:"):
            sql, args = "SELECT 1 FROM seen WHERE fingerprint = ? AND listing_id != ? LIMIT 1", (fingerprint, listing_id)
        else:
            sql, args = "SELECT 1 FROM seen WHERE fingerprint = ? AND source != ? LIMIT 1", (fingerprint, source)
        return self.db.execute(sql, args).fetchone() is not None

    def mark_seen(
        self, source: str, listing_id: str, price: float | None, fingerprint: str | None = None
    ) -> None:
        self.db.execute(
            """
            INSERT INTO seen (source, listing_id, first_seen, price, fingerprint) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (source, listing_id) DO UPDATE SET price = excluded.price, fingerprint = excluded.fingerprint
            """,
            (source, listing_id, datetime.now().isoformat(timespec="seconds"), price, fingerprint),
        )
        self.db.commit()

    def is_seeded(self, search_name: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM seeded_searches WHERE name = ?", (search_name,)).fetchone()
            is not None
        )

    def mark_seeded(self, search_name: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO seeded_searches (name, seeded_at) VALUES (?, ?)",
            (search_name, datetime.now().isoformat(timespec="seconds")),
        )
        self.db.commit()

    def get_last_slot(self, search_name: str) -> str | None:
        row = self.db.execute("SELECT slot FROM last_slots WHERE name = ?", (search_name,)).fetchone()
        return row[0] if row else None

    def set_last_slot(self, search_name: str, slot: str) -> None:
        self.db.execute(
            "INSERT INTO last_slots (name, slot) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET slot = excluded.slot",
            (search_name, slot),
        )
        self.db.commit()
