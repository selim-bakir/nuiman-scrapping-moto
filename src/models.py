"""Modèles de données partagés entre scrapers, rapport et envoi Telegram."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SizeStatus:
    """Disponibilité d'une taille donnée pour un produit."""

    size: str
    available: bool


@dataclass
class ProductResult:
    """Résultat du scraping d'un produit."""

    url: str
    name: str
    site: str | None = None
    # Marque (ex: "Shoei").
    brand: str | None = None
    # Gamme/modèle (ex: "NXR2", "GT-Air 3") pour regrouper le rapport.
    gamme: str | None = None
    # Coloris / déclinaison (ex: "PLAIN", "ACCOLADE").
    color: str | None = None
    price: str | None = None
    sizes: list[SizeStatus] = field(default_factory=list)
    # True quand le produit entier est en rupture (aucune taille disponible).
    sold_out: bool = False
    # Message d'erreur si le scraping a échoué pour ce produit.
    error: str | None = None
    scraped_at: datetime | None = None

    @property
    def available_sizes(self) -> list[str]:
        return [s.size for s in self.sizes if s.available]

    @property
    def unavailable_sizes(self) -> list[str]:
        return [s.size for s in self.sizes if not s.available]

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "name": self.name,
            "site": self.site,
            "brand": self.brand,
            "gamme": self.gamme,
            "color": self.color,
            "price": self.price,
            "sizes": [{"size": s.size, "available": s.available} for s in self.sizes],
            "sold_out": self.sold_out,
            "error": self.error,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
        }


@dataclass
class Report:
    """Rapport journalier agrégeant tous les produits surveillés."""

    generated_at: datetime
    results: list[ProductResult] = field(default_factory=list)

    @property
    def ok_results(self) -> list[ProductResult]:
        return [r for r in self.results if r.error is None]

    @property
    def failed_results(self) -> list[ProductResult]:
        return [r for r in self.results if r.error is not None]

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "results": [r.to_dict() for r in self.results],
        }
