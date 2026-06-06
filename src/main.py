"""Point d'entrée : scrape les produits, génère et envoie le rapport quotidien.

Usage :
    python -m src.main              # scrape + envoi Telegram
    python -m src.main --dry-run    # scrape + affiche le rapport (pas d'envoi)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from .config import ProductConfig, Settings, load_settings
from .models import ProductResult, Report
from .report import build_report_text, split_for_telegram
from .scrapers.registry import get_scraper
from .telegram import send_report

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


async def _scrape_one(context, product: ProductConfig, timeout_ms: int) -> ProductResult:
    scraper = get_scraper(product.url)
    if scraper is None:
        return ProductResult(
            url=product.url,
            name=product.label or product.url,
            error="Aucun scraper pour ce domaine",
            scraped_at=datetime.now(),
        )

    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    try:
        return await scraper.scrape(page, product)
    except Exception as exc:  # noqa: BLE001 — on isole l'échec d'un produit
        return ProductResult(
            url=product.url,
            name=product.label or product.url,
            error=f"{type(exc).__name__}: {exc}",
            scraped_at=datetime.now(),
        )
    finally:
        await page.close()


async def run_scraping(settings: Settings) -> Report:
    results: list[ProductResult] = []
    semaphore = asyncio.Semaphore(max(1, settings.concurrency))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, locale="fr-FR")

        async def _guarded(product: ProductConfig) -> ProductResult:
            async with semaphore:
                return await _scrape_one(context, product, settings.page_timeout_ms)

        results = await asyncio.gather(*(_guarded(p) for p in settings.products))

        await context.close()
        await browser.close()

    return Report(generated_at=datetime.now(), results=list(results))


def _save_report(report: Report) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = report.generated_at.strftime("%Y-%m-%d_%H%M%S")
    path = REPORTS_DIR / f"report_{stamp}.json"
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapport disponibilité accessoires moto")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche le rapport sans l'envoyer sur Telegram",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.products:
        print("Aucun produit configuré dans config/products.yaml", file=sys.stderr)
        return 1

    report = asyncio.run(run_scraping(settings))
    saved = _save_report(report)
    text = build_report_text(report)
    chunks = split_for_telegram(text)

    print(f"Rapport sauvegardé : {saved}")
    print("-" * 60)
    print(text)
    print("-" * 60)

    if args.dry_run:
        print("[dry-run] Envoi Telegram ignoré.")
        return 0

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants : envoi ignoré.",
            file=sys.stderr,
        )
        return 1

    send_report(settings.telegram_bot_token, settings.telegram_chat_id, chunks)
    print(f"Rapport envoyé sur Telegram ({len(chunks)} message(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
