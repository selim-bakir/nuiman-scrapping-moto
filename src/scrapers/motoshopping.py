"""Scraper pour motoshopping.com (PrestaShop, SSR).

La disponibilité par taille est dans le payload JS `window.combinationsFromController`
(chargé avec la page) : chaque combinaison porte la taille, la quantité en stock
et la date de dispo. Un coloris = une URL produit distincte.
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


def _sort_sizes(sizes: list[str]) -> list[str]:
    norm = {"XXL": "2XL"}
    def key(s):
        u = norm.get(s.upper(), s.upper())
        return (SIZE_ORDER.index(u) if u in SIZE_ORDER else 99, u)
    return sorted(sizes, key=key)


def _split_gamme_color(name: str) -> tuple[str | None, str | None]:
    """'... SHOEI GT-AIR 3 NOIR MAT' -> ('GT-AIR 3', 'Noir mat')."""
    up = name.upper()
    for g in KNOWN_GAMMES:
        i = up.find(g)
        if i >= 0:
            rest = name[i + len(g):].strip(" -·")
            rest = re.sub(r"(?i)\b(promo|nouveaut[ée]|new|-?\d+%)\b", "", rest).strip()
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
    // Le groupe "taille" : on prend la 1re valeur courte (XS..XXL) trouvée.
    let size = null;
    for (const gid in av) {
      const v = (av[gid] || "").toString().trim();
      if (/^(2?X?S|M|X?L|XXL|2XL|XS|XL)$/i.test(v)) { size = v.toUpperCase(); break; }
    }
    if (!size) continue;
    const qty = parseInt(c.quantity, 10) || 0;
    const rupture = c.flag_rupture || c.rupture;
    sizes.push({ size, qty, available: qty > 0 && !rupture, date: c.date_formatted || null });
  }
  // Prix affiché.
  let price = null;
  const pm = (document.body.innerText || "").match(/(\d[\d  ]*,\d{2})\s*€/);
  if (pm) price = norm(pm[1]) + " €";
  return { name: h1 ? norm(h1.textContent) : null, price, sizes };
}
"""

_LISTING_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll("a[href]")) {
    const h = a.getAttribute("href");
    if (!h) continue;
    let u; try { u = new URL(h, location.origin); } catch (e) { continue; }
    const p = u.pathname.toLowerCase();
    if (!/\/casque-moto-(integral|jet)\/\d+-shoei-/.test(p)) continue;
    if (!(p.includes("gt-air-3") || p.includes("j-cruise-3"))) continue;
    const full = u.origin + u.pathname;
    if (seen.has(full)) continue;
    seen.add(full);
    out.push(full);
  }
  return out;
}
"""


def _fr_date(d: str | None) -> str | None:
    # '09/06/2026' -> ISO '2026-06-09' (pour cohérence avec le rapport).
    if not d:
        return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", d)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}T00:00:00+02:00" if m else None


class MotoshoppingScraper(BaseScraper):
    domains = ("motoshopping.com",)
    site_name = "Motoshopping"

    async def list_product_urls(self, page: Page, category_url: str, max_pages: int = 10) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for p in range(1, max_pages + 1):
            sep = "&" if "?" in category_url else "?"
            url = category_url if p == 1 else f"{category_url}{sep}p={p}"
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(500)
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
                    deferred=(not s["available"]) and bool(s["date"]),
                    restock=None if s["available"] else _fr_date(s["date"]),
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
                sizes=sizes,
                sold_out=sold_out,
                scraped_at=datetime.now(),
            )
        ]
