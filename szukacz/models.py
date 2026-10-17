from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Listing:
    source: str
    id: str
    title: str
    url: str
    price: float | None = None
    price_display: str | None = None
    area_m2: float | None = None
    price_per_m2: float | None = None
    rooms: str | None = None
    floor: str | None = None
    plot_type: str | None = None
    market: str | None = None
    market_key: str | None = None  # secondary | primary
    location: str | None = None
    created_at: datetime | None = None
    photo_url: str | None = None
    partner: str | None = None
    description: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str | None:
        """Cena + metraż – rozpoznaje to samo mieszkanie wystawione na kilku portalach.
        Dla postów z FB: skrót tekstu (ten sam post wrzucony ponownie)."""
        if self.extra.get("text_fp"):
            return self.extra["text_fp"]
        if self.price is None or self.area_m2 is None:
            return None
        return f"{round(self.price)}|{round(self.area_m2)}"
