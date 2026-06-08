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

import os
import time

from .config import ProductConfig, Settings, load_settings
from .models import ProductResult, Report
from .report import (
    build_header_text,
    build_report_text,
    iter_sections,
    photo_caption,
    split_for_telegram,
)
from .scrapers.registry import get_scraper
from .telegram import send_message, send_photo, send_report

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


async def _scrape_one(
    context, product: ProductConfig, timeout_ms: int, delay_ms: int
) -> ProductResult:
    scraper = get_scraper(product.url)
    if scraper is None:
        return ProductResult(
            url=product.url,
            name=product.label or product.url,
            error="Aucun scraper pour ce domaine",
            scraped_at=datetime.now(),
        )

    # Rate-limiting : petit délai avant chaque requête pour rester poli.
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)

    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    try:
        return await scraper.scrape(page, product)
    except Exception as exc:  # noqa: BLE001 — on isole l'échec d'un produit
        return ProductResult(
            url=product.url,
            name=product.label or product.url,
            site=scraper.site_name or None,
            error=f"{type(exc).__name__}: {exc}",
            scraped_at=datetime.now(),
        )
    finally:
        await page.close()


async def _expand_categories(context, settings: Settings) -> list[ProductConfig]:
    """Combine les produits explicites et l'expansion des catégories en liste dédupliquée."""
    product_configs: list[ProductConfig] = list(settings.products)

    if settings.categories:
        page = await context.new_page()
        page.set_default_timeout(settings.page_timeout_ms)
        try:
            for cat in settings.categories:
                scraper = get_scraper(cat)
                if scraper is None:
                    print(f"  ! aucun scraper pour la catégorie : {cat}", file=sys.stderr)
                    continue
                urls = await scraper.list_product_urls(page, cat)
                print(f"  • {len(urls)} produit(s) trouvé(s) dans {cat}")
                product_configs.extend(ProductConfig(url=u) for u in urls)
        finally:
            await page.close()

    # Déduplication par URL en conservant l'ordre.
    seen: set[str] = set()
    deduped: list[ProductConfig] = []
    for pc in product_configs:
        if pc.url in seen:
            continue
        seen.add(pc.url)
        deduped.append(pc)

    if settings.max_products > 0:
        deduped = deduped[: settings.max_products]

    return deduped


async def run_scraping(settings: Settings) -> Report:
    semaphore = asyncio.Semaphore(max(1, settings.concurrency))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, locale="fr-FR")

        print("Expansion des catégories...")
        products = await _expand_categories(context, settings)
        print(f"→ {len(products)} produit(s) à scraper")

        async def _guarded(product: ProductConfig) -> ProductResult:
            async with semaphore:
                return await _scrape_one(
                    context,
                    product,
                    settings.page_timeout_ms,
                    settings.request_delay_ms,
                )

        results = await asyncio.gather(*(_guarded(p) for p in products))

        await context.close()
        await browser.close()

    return Report(generated_at=datetime.now(), results=_dedupe_results(list(results)))


def _dedupe_results(results: list[ProductResult]) -> list[ProductResult]:
    """Fusionne les doublons (page générique + variante) par (site, nom).

    Les coloris distincts ont des noms différents et restent séparés. En cas de
    doublon, on conserve le résultat le plus complet (sans erreur, plus de tailles).
    """
    best: dict[tuple[str, str], ProductResult] = {}
    order: list[tuple[str, str]] = []
    for r in results:
        key = ((r.site or ""), (r.name or r.url).strip().lower())
        if key not in best:
            best[key] = r
            order.append(key)
            continue
        cur = best[key]
        better = (cur.error and not r.error) or (len(r.sizes) > len(cur.sizes))
        if better:
            best[key] = r
    return [best[k] for k in order]


def _save_report(report: Report) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = report.generated_at.strftime("%Y-%m-%d_%H%M%S")
    path = REPORTS_DIR / f"report_{stamp}.json"
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def _send_photo_report(token: str, chat_id: str, report: Report) -> int:
    """Envoie un message photo par casque (légende = dispo), groupé par gamme.

    Throttlé pour respecter le rate-limit Telegram. Repli en message texte si
    un casque n'a pas de photo ou si l'envoi de la photo échoue.
    """
    delay = int(os.getenv("TELEGRAM_SEND_DELAY_MS", "1500")) / 1000
    sent = 0

    send_message(token, chat_id, build_header_text(report))
    sent += 1

    for label, helmets in iter_sections(report):
        send_message(token, chat_id, f"<b>━━━ {label} ━━━</b>")
        sent += 1
        time.sleep(delay)
        for r in helmets:
            caption = photo_caption(r)
            try:
                if r.image and not r.error:
                    send_photo(token, chat_id, r.image, caption)
                else:
                    send_message(token, chat_id, caption)
            except Exception as exc:  # noqa: BLE001 — repli texte si la photo échoue
                print(f"  ! photo KO ({r.name}): {exc} → repli texte", file=sys.stderr)
                send_message(token, chat_id, caption)
            sent += 1
            time.sleep(delay)
    return sent


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapport disponibilité casques moto")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche le rapport sans l'envoyer sur Telegram",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Envoie le rapport en texte au lieu du mode photo",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.products and not settings.categories:
        print(
            "Aucun produit ni catégorie configuré dans config/products.yaml",
            file=sys.stderr,
        )
        return 1

    report = asyncio.run(run_scraping(settings))
    saved = _save_report(report)
    text = build_report_text(report)

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

    if args.text:
        chunks = split_for_telegram(text)
        send_report(settings.telegram_bot_token, settings.telegram_chat_id, chunks)
        print(f"Rapport texte envoyé sur Telegram ({len(chunks)} message(s)).")
    else:
        n = _send_photo_report(
            settings.telegram_bot_token, settings.telegram_chat_id, report
        )
        print(f"Rapport photo envoyé sur Telegram ({n} message(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
