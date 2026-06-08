"""Scraper pour speedway.fr (PrestaShop, SSR).

Comme motoshopping, la disponibilité par taille est portée par le payload JS
``window.combinationsFromController`` (chargé avec la page) : chaque combinaison
porte la taille (``attributes_values``), la quantité en stock (``quantity``) et,
le cas échéant, une date de réappro (``available_date`` / ``date_formatted``).
Un coloris = une URL produit distincte (le coloris est dans le h1/slug).

Subtilité importante : sur speedway.fr le champ ``out_of_stock`` n'est PAS l'état
de rupture mais la *politique* PrestaShop de gestion du stock à zéro (0=refuser,
1=autoriser la commande). On a observé des tailles avec ``quantity > 0`` ET
``out_of_stock = 1``. La source fiable de disponibilité est donc ``quantity > 0``
(``quantity == -1`` = survente/backorder -> indisponible).
"""

from __future__ import annotations

import re
from datetime import datetime

from playwright.async_api import Page

from ..config import ProductConfig
from ..models import ProductResult, SizeStatus
from .base import BaseScraper

SIZE_ORDER = ["3XS", "2XS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL"]
KNOWN_GAMMES = ["GT-AIR 3", "J-CRUISE 3"]

# Bruit présent dans les slugs/h1 (déclinaisons graphiques, codes coloris Shoei,
# mentions marketing) à retirer pour ne garder que le coloris « parlant ».
_COLOR_NOISE = re.compile(
    r"(?i)\b(plain|promo|nouveaut[ée]s?|new|collection|grip|"
    r"tc-?\d+|mm93|m|xs|-?\d+%)\b"
)


def _sort_sizes(sizes: list[str]) -> list[str]:
    norm = {"XXL": "2XL"}

    def key(s):
        u = norm.get(s.upper(), s.upper())
        return (SIZE_ORDER.index(u) if u in SIZE_ORDER else 99, u)

    return sorted(sizes, key=key)


def _split_gamme_color(name: str) -> tuple[str | None, str | None]:
    """'Casque Shoei GT-Air 3 Candy Noir Mat' -> ('GT-AIR 3', 'Noir mat')."""
    up = name.upper()
    for g in KNOWN_GAMMES:
        i = up.find(g)
        if i >= 0:
            rest = name[i + len(g):].strip(" -·")
            rest = _COLOR_NOISE.sub("", rest)
            rest = re.sub(r"\s+", " ", rest).strip(" -·")
            color = rest.title() if rest else None
            return g, color
    return None, None


# Lit le payload PrestaShop : tailles + stock + date de réappro.
_EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const h1 = document.querySelector("h1");
  const cfc = window.combinationsFromController || {};
  const sizes = [];
  for (const k in cfc) {
    const c = cfc[k];
    const av = c.attributes_values || {};
    // Le groupe « taille » : 1re valeur courte (XS..3XL) trouvée.
    let size = null;
    for (const gid in av) {
      const v = (av[gid] || "").toString().trim();
      if (/^(3XS|2XS|XXS|XS|S|M|L|XL|XXL|2XL|3XL)$/i.test(v)) {
        size = v.toUpperCase();
        break;
      }
    }
    if (!size) continue;
    const qty = parseInt(c.quantity, 10);
    // Sur speedway, out_of_stock = politique PrestaShop, pas l'état de rupture.
    // La vérité dispo = quantity > 0 (qty -1 = survente -> indispo).
    const available = Number.isFinite(qty) && qty > 0;
    sizes.push({
      size,
      qty: Number.isFinite(qty) ? qty : 0,
      available,
      date: c.date_formatted || c.available_date || null,
    });
  }
  // Prix affiché (TTC), 1re occurrence « 599,00 € ».
  let price = null;
  const pm = (document.body.innerText || "").match(/(\d[\d  ]*[.,]\d{2})\s*€/);
  if (pm) price = norm(pm[1]).replace(".", ",") + " €";
  // Image principale.
  let image = null;
  const img = document.querySelector(
    ".product-cover img, #product-images-large img, .js-qv-product-cover img, img#bigpic"
  );
  if (img) image = img.getAttribute("src") || img.getAttribute("data-image-large-src");
  return { name: h1 ? norm(h1.textContent) : null, price, image, sizes };
}
"""

# Listing : tous les liens produit /{id}-...shoei...{gamme}.html de la catégorie.
_LISTING_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll("a[href]")) {
    const h = a.getAttribute("href");
    if (!h) continue;
    let u;
    try { u = new URL(h, location.origin); } catch (e) { continue; }
    const p = u.pathname.toLowerCase();
    if (!/^\/\d+-.+\.html$/.test(p)) continue;
    if (!p.includes("shoei")) continue;
    if (!(p.includes("gt-air-3") || p.includes("j-cruise-3"))) continue;
    const full = u.origin + u.pathname;
    if (seen.has(full)) continue;
    seen.add(full);
    out.push(full);
  }
  return out;
}
"""


def _iso_date(d: str | None) -> str | None:
    """Date de réappro PrestaShop -> ISO. Gère '09/06/2026' et '2026-06-09'."""
    if not d:
        return None
    d = d.strip()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", d)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}T00:00:00+02:00"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    if m and m.group(0) != "0000-00-00":
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00+02:00"
    return None


class SpeedwayScraper(BaseScraper):
    domains = ("speedway.fr",)
    site_name = "Speedway"

    async def list_product_urls(
        self, page: Page, category_url: str, max_pages: int = 10
    ) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for p in range(1, max_pages + 1):
            sep = "&" if "?" in category_url else "?"
            url = category_url if p == 1 else f"{category_url}{sep}page={p}"
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(600)
                # Déclenche le lazy-load des miniatures en bas de page.
                for _ in range(6):
                    await page.evaluate(
                        "() => window.scrollBy(0, document.body.scrollHeight)"
                    )
                    await page.wait_for_timeout(300)
            except Exception:
                break
            new = [u for u in await page.evaluate(_LISTING_JS) if u not in seen]
            if not new:
                break
            for u in new:
                seen.add(u)
                ordered.append(u)
        return ordered

    async def scrape(self, page: Page, product: ProductConfig) -> list[ProductResult]:
        await page.goto(product.url, wait_until="domcontentloaded")
        await page.wait_for_selector("h1", timeout=15000)
        data = await page.evaluate(_EXTRACT_JS)

        gamme, color = _split_gamme_color(data["name"] or "")
        seen: dict[str, dict] = {}
        for s in data["sizes"]:
            seen.setdefault(s["size"], s)
        sizes = []
        for sz in _sort_sizes(list(seen)):
            s = seen[sz]
            sizes.append(
                SizeStatus(
                    size=sz,
                    available=s["available"],
                    # Speedway accepte la commande même hors stock (backorder) mais
                    # sans communiquer de date -> "livrable plus tard" sans date.
                    deferred=not s["available"],
                    restock=None if s["available"] else _iso_date(s["date"]),
                )
            )
        sold_out = bool(sizes) and not any(s.available for s in sizes)
        return [
            ProductResult(
                url=product.url,
                name=product.label or data["name"] or product.url,
                site=self.site_name,
                brand="Shoei",
                gamme=gamme,
                color=color,
                price=data["price"],
                image=data.get("image"),
                sizes=sizes,
                sold_out=sold_out,
                scraped_at=datetime.now(),
            )
        ]
