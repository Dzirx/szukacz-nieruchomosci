from typing import Protocol

from ..config import Search
from ..models import Listing


class SourceError(Exception):
    pass


class Source(Protocol):
    name: str

    def fetch(self, search: Search) -> list[Listing]: ...
