"""Sélection du scraper adapté en fonction du domaine de l'URL."""

from __future__ import annotations

from urllib.parse import urlparse

from .base import BaseScraper
from .dafy import DafyScraper
from .motoblouz import MotoblouzScraper

# Pour ajouter un site : instancier le scraper ici.
_SCRAPERS: list[BaseScraper] = [
    MotoblouzScraper(),
    DafyScraper(),
]


def _normalize_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def get_scraper(url: str) -> BaseScraper | None:
    """Renvoie le scraper gérant ce domaine, ou ``None`` si non supporté."""
    domain = _normalize_domain(url)
    for scraper in _SCRAPERS:
        if any(domain == d or domain.endswith("." + d) for d in scraper.domains):
            return scraper
    return None
