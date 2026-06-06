"""Interface commune à tous les scrapers de sites."""

from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import Page

from ..config import ProductConfig
from ..models import ProductResult


class BaseScraper(ABC):
    """Contrat qu'un scraper de site doit respecter.

    Pour ajouter un nouveau site : créer une sous-classe, implémenter
    ``domains`` et ``scrape``, puis l'enregistrer dans ``registry.py``.
    """

    #: Domaines (sans www.) pris en charge par ce scraper.
    domains: tuple[str, ...] = ()

    @abstractmethod
    async def scrape(self, page: Page, product: ProductConfig) -> ProductResult:
        """Scrape une page produit et renvoie un ``ProductResult``."""
        raise NotImplementedError
