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

    #: Nom lisible du site, affiché dans le rapport.
    site_name: str = ""

    @abstractmethod
    async def scrape(self, page: Page, product: ProductConfig) -> list[ProductResult]:
        """Scrape une page produit et renvoie un ``ProductResult`` par coloris."""
        raise NotImplementedError

    async def list_product_urls(
        self, page: Page, category_url: str, max_pages: int = 300
    ) -> list[str]:
        """Liste les URLs produits d'une page catégorie (avec pagination).

        Implémentation optionnelle : un scraper qui ne gère que des produits
        unitaires peut laisser cette méthode renvoyer une liste vide.
        """
        return []
