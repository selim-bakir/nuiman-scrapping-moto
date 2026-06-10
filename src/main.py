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
import re
import time

from .config import ProductConfig, Settings, load_settings
from .excel import build_xlsx
from .matching import cross_check
from .models import ProductResult, Report
from .report import (
    build_header_text,
    build_report_text,
    iter_by_site,
    photo_caption,
    site_banner,
    split_for_telegram,
    to_report,
)
from .scrapers.registry import get_scraper
from .telegram import send_document, send_message, send_photo, send_report

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


async def _scrape_one(
    context, product: ProductConfig, timeout_ms: int, delay_ms: int
) -> list[ProductResult]:
    scraper = get_scraper(product.url)
    if scraper is None:
        return [ProductResult(
            url=product.url,
            name=product.label or product.url,
            error="Aucun scraper pour ce domaine",
            scraped_at=datetime.now(),
        )]

    # Rate-limiting : petit délai avant chaque requête pour rester poli.
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)

    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    try:
        return await scraper.scrape(page, product)
    except Exception as exc:  # noqa: BLE001 — on isole l'échec d'un produit
        return [ProductResult(
            url=product.url,
            name=product.label or product.url,
            site=scraper.site_name or None,
            error=f"{type(exc).__name__}: {exc}",
            scraped_at=datetime.now(),
        )]
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
                # Robustesse : un site en panne ne doit pas bloquer les autres.
                try:
                    urls = await scraper.list_product_urls(page, cat)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! échec listing {cat}: {exc}", file=sys.stderr)
                    continue
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


async def _scrape_category_urls(context, cats: list[str], settings: Settings):
    """Expanse une liste de catégories en URLs et scrape chacune."""
    semaphore = asyncio.Semaphore(max(1, settings.concurrency))
    urls: list[str] = []
    page = await context.new_page()
    page.set_default_timeout(settings.page_timeout_ms)
    try:
        for cat in cats:
            scraper = get_scraper(cat)
            if scraper is None:
                continue
            found = await scraper.list_product_urls(page, cat)
            urls.extend(found)
    finally:
        await page.close()
    # Dédup + (cross-check Shoei) on ne garde que les URLs de la marque suivie.
    seen, ordered = set(), []
    for u in urls:
        if u not in seen and "shoei" in u.lower():
            seen.add(u)
            ordered.append(u)

    async def _guarded(u: str) -> ProductResult:
        async with semaphore:
            return await _scrape_one(
                context, ProductConfig(url=u), settings.page_timeout_ms, settings.request_delay_ms
            )

    nested = await asyncio.gather(*(_guarded(u) for u in ordered))
    return [r for sub in nested for r in sub]


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

        nested = await asyncio.gather(*(_guarded(p) for p in products))
        results = _dedupe_results([r for sub in nested for r in sub])
        results = _apply_focus(results, settings)
        print(f"→ {len(results)} casque(s) après filtre focus")

        # Double-check : scraping des sources de contrôle (Dafy) + croisement.
        if settings.cross_check:
            print("Croisement avec Dafy...")
            dafy_results = await _scrape_category_urls(context, settings.cross_check, settings)
            stats = cross_check(results, dafy_results)
            print(
                f"→ Dafy: {len(dafy_results)} casques · {stats['matched']} appariés · "
                f"{stats['confirmed_ruptures']} ruptures confirmées · "
                f"{stats['available_on_dafy']} dispo sur Dafy"
            )

        await context.close()
        await browser.close()

    return Report(generated_at=datetime.now(), results=_share_images(results))


_BASE_COLORS = ("noir", "blanc", "gris", "bleu", "rouge", "vert", "jaune", "orange")


def _image_key(gamme: str | None, color: str | None):
    """Clé (gamme, couleur de base, mat?) pour partager une photo entre sites."""
    g = re.sub(r"[^a-z0-9]", "", (gamme or "").lower())
    cl = (color or "").lower()
    base = next((c for c in _BASE_COLORS if c in cl), None)
    if not g or not base:
        return None
    return (g, base, "mat" in cl)


def _share_images(results: list[ProductResult]) -> list[ProductResult]:
    """Réutilise les photos Motoblouz pour les casques équivalents des autres sites."""
    bank: dict = {}
    for r in results:
        if r.image:
            k = _image_key(r.gamme, r.color)
            if k and k not in bank:
                bank[k] = r.image
    for r in results:
        if not r.image:
            k = _image_key(r.gamme, r.color)
            if k and k in bank:
                r.image = bank[k]
    return results


def _apply_focus(results: list[ProductResult], settings: Settings) -> list[ProductResult]:
    """Ne garde que les gammes + coloris unis suivis (filtre 'focus' de la config)."""
    if not settings.focus_gammes:
        return results

    def norm(s: str | None) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    base_colors = {
        "noir", "blanc", "gris", "bleu", "rouge", "vert", "jaune", "orange",
        "argent", "anthracite", "rose", "violet", "beige", "marron", "carbone",
        "chrome", "titane", "bronze", "kaki", "bordeaux", "turquoise",
    }
    gset = {norm(g) for g in settings.focus_gammes}
    cset = set(settings.focus_colors)
    out = []
    for r in results:
        if norm(r.gamme) not in gset:
            continue
        if settings.focus_unis_only and " · " in (r.color or ""):
            continue  # exclut les graphiques/séries
        if cset:
            ctoks = set(re.findall(r"[a-zàâéèêëîïôûùç]+", (r.color or "").lower()))
            base = ctoks & base_colors
            # Uni = exactement une couleur de base, et elle doit être suivie.
            if len(base) != 1 or not (base <= cset):
                continue
        out.append(r)
    return out


def _dedupe_results(results: list[ProductResult]) -> list[ProductResult]:
    """Déduplique par URL uniquement.

    Sur Motoblouz, chaque URL (ID produit) est un coloris/déclinaison distinct :
    dédupliquer par nom fusionnerait des coloris différents partageant le même
    nom court. On déduplique donc strictement par URL (et l'expansion des
    catégories garantit déjà l'unicité des URLs).
    """
    best: dict[tuple, ProductResult] = {}
    order: list[tuple] = []
    for r in results:
        key = (r.url, (r.color or "").lower())  # 1 URL peut avoir plusieurs coloris
        if key not in best:
            best[key] = r
            order.append(key)
        elif best[key].error and not r.error:
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

    if not to_report(report.results):
        send_message(
            token, chat_id, "✅ Aucune rupture aujourd'hui : tous les casques sont dispo."
        )
        return sent + 1

    for site, helmets in iter_by_site(report):
        send_message(token, chat_id, site_banner(site, len(helmets)))
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

    # Garde-fou horaire : deux crons UTC sont déclarés (un pour l'heure d'été, un
    # pour l'hiver) pour viser 7h Paris toute l'année. On n'exécute que celui qui
    # tombe bien — sauf lancement manuel (workflow_dispatch / local / --dry-run).
    if os.getenv("GITHUB_EVENT_NAME") == "schedule" and not args.dry_run:
        try:
            from zoneinfo import ZoneInfo

            paris_hour = datetime.now(ZoneInfo("Europe/Paris")).hour
        except Exception:
            paris_hour = 7  # en cas de souci, on n'empêche pas l'envoi
        if paris_hour != 7:
            print(f"[skip] créneau cron hors 7h Paris (il est {paris_hour}h) — pas d'envoi.")
            return 0

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
    xlsx_path = REPORTS_DIR / f"rapport_{report.generated_at:%Y-%m-%d}.xlsx"

    print(f"Rapport sauvegardé : {saved}")
    print("-" * 60)
    print(text)
    print("-" * 60)

    if args.dry_run:
        try:
            build_xlsx(report, xlsx_path)
            print(f"Export Excel : {xlsx_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"! Excel non généré : {exc}", file=sys.stderr)
        print("[dry-run] Envoi Telegram ignoré.")
        return 0

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants : envoi ignoré.",
            file=sys.stderr,
        )
        return 1

    token, chat = settings.telegram_bot_token, settings.telegram_chat_id

    # 1) Le rapport Telegram est PRIORITAIRE — il ne doit jamais être bloqué par l'Excel.
    if args.text:
        chunks = split_for_telegram(text)
        send_report(token, chat, chunks)
        print(f"Rapport texte envoyé sur Telegram ({len(chunks)} message(s)).")
    else:
        n = _send_photo_report(token, chat, report)
        print(f"Rapport photo envoyé sur Telegram ({n} message(s)).")

    # 2) Excel en pièce jointe (best-effort : un échec n'annule pas le rapport déjà envoyé).
    try:
        build_xlsx(report, xlsx_path)
        send_document(
            token, chat, str(xlsx_path),
            caption=f"📊 Export Excel — {report.generated_at:%d/%m/%Y}",
        )
        print(f"Export Excel envoyé sur Telegram : {xlsx_path.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"! Export Excel non envoyé : {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
