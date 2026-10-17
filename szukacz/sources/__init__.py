from .base import Source, SourceError
from .facebook_group import FacebookGroupSource
from .morizon import MorizonSource
from .olx import OlxSource


def build_sources() -> dict[str, Source]:
    return {"olx": OlxSource(), "morizon": MorizonSource(), "facebook_group": FacebookGroupSource()}


__all__ = ["Source", "SourceError", "build_sources"]
