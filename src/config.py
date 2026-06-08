"""Chargement de la configuration (env + liste de produits)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ProductConfig:
    url: str
    label: str | None = None
    sizes: list[str] = field(default_factory=list)


@dataclass
class Settings:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    concurrency: int
    page_timeout_ms: int
    # Délai (ms) avant chaque requête produit, pour rester poli (anti-blocage).
    request_delay_ms: int
    products: list[ProductConfig]
    categories: list[str]
    # Catégories de contrôle (Dafy) pour croiser la disponibilité.
    cross_check: list[str]
    # Filtre focus : gammes suivies, couleurs unies suivies, unis seulement.
    focus_gammes: list[str]
    focus_colors: list[str]
    focus_unis_only: bool
    # Plafond de sécurité du nombre de produits scrapés (0 = illimité).
    max_products: int


def _load_products(path: Path) -> list[ProductConfig]:
    if not path.exists():
        raise FileNotFoundError(f"Fichier de config produits introuvable : {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_products = data.get("products", [])
    products: list[ProductConfig] = []
    for entry in raw_products:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        products.append(
            ProductConfig(
                url=entry["url"].strip(),
                label=entry.get("label"),
                sizes=[str(s).strip() for s in entry.get("sizes", [])],
            )
        )
    return products


def _load_categories(data: dict) -> list[str]:
    raw = data.get("categories", []) or []
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
        elif isinstance(entry, dict) and entry.get("url"):
            out.append(entry["url"].strip())
    return out


def load_settings(products_path: Path | None = None) -> Settings:
    products_path = products_path or (ROOT / "config" / "products.yaml")
    data = yaml.safe_load(products_path.read_text(encoding="utf-8")) or {}
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        concurrency=int(os.getenv("SCRAPE_CONCURRENCY", "3")),
        page_timeout_ms=int(os.getenv("PAGE_TIMEOUT_MS", "30000")),
        request_delay_ms=int(os.getenv("REQUEST_DELAY_MS", "250")),
        products=_load_products(products_path),
        categories=_load_categories(data),
        cross_check=[
            e.strip()
            for e in (data.get("cross_check", []) or [])
            if isinstance(e, str) and e.strip()
        ],
        focus_gammes=[str(g).strip() for g in ((data.get("focus") or {}).get("gammes") or [])],
        focus_colors=[str(c).strip().lower() for c in ((data.get("focus") or {}).get("colors") or [])],
        focus_unis_only=bool((data.get("focus") or {}).get("unis_only", False)),
        max_products=int(os.getenv("MAX_PRODUCTS", "0")),
    )
