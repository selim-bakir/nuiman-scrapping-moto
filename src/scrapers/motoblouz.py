"""Scraper pour motoblouz.com (Nuxt 3 SSR).

La disponibilité par taille n'est pas lisible dans le DOM (résolue en JS au clic),
mais TOUT est présent dans le payload SSR `#__NUXT_DATA__` (format devalue) :
nom, gamme, coloris, prix, et pour chaque SKU (coloris × taille) le flag
`forSale` + le détail des stocks par entrepôt.

On lit donc ce payload (présent dès le chargement, server-rendered) et on le
décode, ce qui est rapide et fiable (pas d'attente d'hydratation).
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from playwright.async_api import Page

from ..config import ProductConfig
from ..models import ProductResult, SizeStatus
from .base import BaseScraper

# Ordre canonique d'affichage des tailles casque.
SIZE_ORDER = ["3XS", "2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"]

# Base CDN des images produit (resize 580px).
IMG_CDN = "https://media-imgproxy.motoblouz.com/_/rs580"


def _devalue_resolver(data: list):
    """Construit un résolveur pour le format devalue de Nuxt.

    Dans ce format, le payload est un tableau plat : les valeurs d'un objet/liste
    sont des index pointant vers d'autres entrées du tableau. On déréférence
    récursivement (avec garde anti-cycle).
    """

    def resolve(i, depth=0, seen=None):
        if seen is None:
            seen = set()
        if not isinstance(i, int):
            return i
        if i < 0 or i >= len(data) or depth > 18:
            return None
        v = data[i]
        if isinstance(v, dict):
            if i in seen:
                return None
            seen = seen | {i}
            return {k: resolve(r, depth + 1, seen) for k, r in v.items()}
        if isinstance(v, list):
            if i in seen:
                return None
            seen = seen | {i}
            return [resolve(r, depth + 1, seen) for r in v]
        return v

    return resolve


def _sort_sizes(sizes: list[str]) -> list[str]:
    def key(s: str):
        u = s.upper()
        return (SIZE_ORDER.index(u) if u in SIZE_ORDER else 99, u)

    return sorted(sizes, key=key)


def _format_price(value) -> str | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f == int(f):
        return f"{int(f)} €"
    return f"{f:.2f} €".replace(".", ",")


def parse_product(nuxt_data_text: str, fallback_url: str) -> dict:
    """Extrait les infos produit depuis le texte du script `#__NUXT_DATA__`."""
    data = json.loads(nuxt_data_text)
    resolve = _devalue_resolver(data)

    # Le produit principal est le seul dict possédant à la fois `skus` et `displayName`.
    prod_idx = next(
        (
            i
            for i, v in enumerate(data)
            if isinstance(v, dict) and "skus" in v and "displayName" in v
        ),
        None,
    )
    if prod_idx is None:
        raise ValueError("Produit introuvable dans le payload Nuxt")

    prod = resolve(prod_idx)
    name = prod.get("name") or ""
    display = prod.get("displayName") or name
    reference = prod.get("reference") or ""

    brand_obj = prod.get("brand")
    if isinstance(brand_obj, dict):
        brand = brand_obj.get("name")
    elif isinstance(brand_obj, str):
        brand = brand_obj
    else:
        brand = None

    # Gamme = partie avant " - " ; finition/série = partie après.
    if " - " in name:
        gamme, finition = name.split(" - ", 1)
    else:
        gamme, finition = name, None
    gamme = (gamme or "").strip() or None
    finition = (finition or "").strip() if finition else None

    # Disponibilité agrégée par taille : dispo si au moins un SKU est `forSale`.
    # Pour les tailles indispo, on récupère la date de réappro la plus proche
    # (stocks.WEB.delay des SKU en attente de réassort).
    size_avail: dict[str, bool] = {}
    size_restock: dict[str, str] = {}
    colors_seen: list[str] = []
    for sku in prod.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        forsale = bool(sku.get("forSale"))
        for c in (sku.get("mappedAttributes") or {}).get("color") or []:
            if isinstance(c, str) and c not in colors_seen:
                colors_seen.append(c)
        # Date de réappro = la plus proche parmi TOUS les entrepôts (pas que WEB).
        sku_delays = [
            w["delay"]
            for w in (sku.get("stocks") or {}).values()
            if isinstance(w, dict) and isinstance(w.get("delay"), str) and w.get("delay")
        ]
        delay = min(sku_delays) if sku_delays else None
        ma = sku.get("mappedAttributes") or {}
        for sz in ma.get("size") or []:
            if not isinstance(sz, str):
                continue
            size_avail[sz] = size_avail.get(sz, False) or forsale
            # Date de réappro = delay d'un SKU indispo (la plus proche dans le temps).
            if not forsale and isinstance(delay, str) and delay:
                cur = size_restock.get(sz)
                size_restock[sz] = delay if cur is None else min(cur, delay)

    sizes = []
    for s in _sort_sizes(list(size_avail)):
        avail = size_avail[s]
        sizes.append(
            SizeStatus(
                size=s,
                available=avail,
                restock=None if avail else size_restock.get(s),
            )
        )
    sold_out = (not prod.get("inStock")) or (bool(sizes) and not any(s.available for s in sizes))

    # Prix de vente TTC : sellPrice.inclTax d'un SKU du produit (le plus bas).
    # Repli sur publicPrice (prix conseillé) si aucun prix de vente (ex: rupture).
    price_val = None
    public_val = None
    for i, v in enumerate(data):
        if isinstance(v, dict) and "sellPrice" in v and "publicPrice" in v:
            pr = resolve(i)
            ref = pr.get("reference")
            if isinstance(ref, str) and reference and ref.startswith(reference):
                sp = (pr.get("sellPrice") or {}).get("inclTax")
                if sp is not None:
                    price_val = sp if price_val is None else min(price_val, sp)
                pp = (pr.get("publicPrice") or {}).get("inclTax")
                if pp is not None:
                    public_val = pp if public_val is None else min(public_val, pp)

    # Photo principale + chemin brut (pour déduire la finition mat/brillant).
    image = None
    raw_fp = ""
    for pic in prod.get("pictures") or []:
        if isinstance(pic, dict) and isinstance(pic.get("url"), str):
            image = IMG_CDN + pic["url"]
            raw_fp = pic["url"].lower()
            break

    # Finition mat/brillant/satin déduite du nom de fichier image ET du slug URL
    # (Motoblouz ne distingue pas "Noir mat" de "Noir brillant" dans la couleur).
    hay = f"{raw_fp} {fallback_url.lower()}"
    finish = ""
    if re.search(r"\bmat\b|-mat|mat[._-]", hay):
        finish = "mat"
    elif "brillant" in hay or "brill" in hay:
        finish = "brillant"
    elif "satin" in hay:
        finish = "satin"

    # Coloris affiché = finition/série + couleurs réelles (+ mat/brillant pour les unis).
    colors_str = "/".join(colors_seen[:3]) if colors_seen else None
    if finition and colors_str:
        color = f"{finition} · {colors_str}"
    elif finition:
        color = finition
    elif colors_str:
        if finish:
            color = f"{colors_str} {finish}"
        elif colors_str.lower() == "noir":
            # Noir sans "matte" = Noir brillant (le verni est la finition par défaut Shoei).
            color = "Noir brillant"
        else:
            color = colors_str
    else:
        color = None

    return {
        "name": display,
        "brand": brand,
        "gamme": gamme,
        "color": color,
        "url": prod.get("url") or fallback_url,
        "price": _format_price(price_val if price_val is not None else public_val),
        "image": image,
        "sizes": sizes,
        "sold_out": sold_out,
    }


# Collecte des URLs produits sur une page listing (liens /vente-...-<id>.html).
_LISTING_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll("a[href]")) {
    if (a.closest("header, footer, nav")) continue;
    const raw = a.getAttribute("href");
    if (!raw) continue;
    let u;
    try { u = new URL(raw, location.origin); } catch (e) { continue; }
    if (u.origin !== location.origin) continue;
    // Uniquement les casques INTÉGRAUX et JET (exclut modulables + accessoires).
    if (!/^\/vente-casque-(integral|jet)-.*-\d+\.html$/.test(u.pathname)) continue;
    const full = u.origin + u.pathname;
    if (seen.has(full)) continue;
    seen.add(full);
    out.push(full);
  }
  return out;
}
"""

# Clique le bouton « Voir plus de produits » (chargement infini). Renvoie false
# quand il n'y a plus de bouton (toute la liste est chargée).
_CLICK_MORE_JS = r"""
() => {
  const b = [...document.querySelectorAll("button, a")].find(
    (e) => /voir plus de produit|afficher plus|charger plus|plus de produit/i.test(e.textContent || "")
  );
  if (!b) return false;
  b.scrollIntoView({ block: "center" });
  b.click();
  return true;
}
"""


class MotoblouzScraper(BaseScraper):
    domains = ("motoblouz.com",)
    site_name = "Motoblouz"

    async def list_product_urls(
        self, page: Page, category_url: str, max_pages: int = 60
    ) -> list[str]:
        # Le listing utilise un chargement infini (bouton « Voir plus de produits »)
        # et non une pagination d'URL : on clique le bouton jusqu'à épuisement.
        await page.goto(category_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)  # laisser l'hydratation se faire

        seen: set[str] = set()
        ordered: list[str] = []

        def collect(links: list[str]) -> None:
            for u in links:
                if u not in seen:
                    seen.add(u)
                    ordered.append(u)

        collect(await page.evaluate(_LISTING_JS))

        for _ in range(max_pages):
            try:
                clicked = await page.evaluate(_CLICK_MORE_JS)
            except Exception:
                break
            if not clicked:
                break
            await page.wait_for_timeout(1600)
            collect(await page.evaluate(_LISTING_JS))

        return ordered

    async def scrape(self, page: Page, product: ProductConfig) -> ProductResult:
        await page.goto(product.url, wait_until="domcontentloaded")
        # Le payload est un <script> (donc "hidden") : on attend son attachement.
        await page.wait_for_selector("#__NUXT_DATA__", state="attached", timeout=15000)
        nd = await page.evaluate("() => document.querySelector('#__NUXT_DATA__')?.textContent")
        if not nd:
            raise ValueError("Payload Nuxt absent de la page")

        info = parse_product(nd, product.url)

        sizes = info["sizes"]
        if product.sizes:
            wanted = {s.upper() for s in product.sizes}
            sizes = [s for s in sizes if s.size.upper() in wanted]

        return ProductResult(
            url=info["url"],
            name=product.label or info["name"],
            site=self.site_name,
            brand=info["brand"],
            gamme=info["gamme"],
            color=info["color"],
            price=info["price"],
            image=info["image"],
            sizes=sizes,
            sold_out=info["sold_out"],
            scraped_at=datetime.now(),
        )
