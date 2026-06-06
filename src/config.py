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
    products: list[ProductConfig]


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


def load_settings(products_path: Path | None = None) -> Settings:
    products_path = products_path or (ROOT / "config" / "products.yaml")
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        concurrency=int(os.getenv("SCRAPE_CONCURRENCY", "3")),
        page_timeout_ms=int(os.getenv("PAGE_TIMEOUT_MS", "30000")),
        products=_load_products(products_path),
    )
