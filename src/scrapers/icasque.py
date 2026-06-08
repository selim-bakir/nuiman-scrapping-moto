"""Scraper pour icasque.com (PrestaShop, SSR, pas d'anti-bot).

La disponibilité par taille est dans un payload JS embarqué dans la page :
plusieurs balises ``<script>`` déclarent ``window.opts["acN_<id>"]`` où chaque
entrée porte un objet ``p`` avec une map ``p.o`` indexée par la clé
combinaison (ex ``"6-15"``). Cette clé correspond aux ``<option value>`` du
``<select class="attrcomb">`` dont le libellé est la taille (XS..XXL).

Statuts observés :
- ``IN_STOCK2`` / ``IN_STOCK3`` (et tout ``IN_STOCK*``) -> en stock.
- ``OUT_OF_STOCK_RANGE`` -> rupture, ``text`` contient la date de réappro
  ("Expédition prévue entre le 13 et le 17 août").

Un coloris = une URL produit distincte (le coloris est dans le slug et le H1).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from playwright.async_api import Page

from ..config import ProductConfig
from ..models import ProductResult, SizeStatus
from .base import BaseScraper

SIZE_ORDER = ["3XS", "2XS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL"]
KNOWN_GAMMES = ["GT-AIR 3", "J-CRUISE 3"]

_FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}


def _sort_sizes(sizes: list[str]) -> list[str]:
    norm = {"XXL": "2XL"}

    def key(s: str):
        u = norm.get(s.upper(), s.upper())
        return (SIZE_ORDER.index(u) if u in SIZE_ORDER else 99, u)

    return sorted(sizes, key=key)


def _split_gamme_color(name: str) -> tuple[str | None, str | None]:
    """'Casque Shoei GT-AIR 3 Black' -> ('GT-AIR 3', 'Black').

    Le coloris brut est ensuite normalisé en français via ``_normalize_color``.
    """
    up = name.upper()
    for g in KNOWN_GAMMES:
        i = up.find(g)
        if i >= 0:
            rest = name[i + len(g):].strip(" -·")
            rest = re.sub(r"(?i)\b(promo|nouveaut[ée]|new|-?\d+%)\b", "", rest).strip()
            return g, _normalize_color(rest) if rest else None
    return None, None


# Coloris EN (issu du slug/H1) -> libellé FR attendu dans le rapport.
def _normalize_color(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw).strip().lower()
    # Suffixes graphiques type "TC-1", "TC1", "TC 5" : on conserve la déco + code.
    mat = s.startswith("matt ") or s.startswith("mat ")
    base = re.sub(r"^matt?\s+", "", s)
    color_map = {
        "black": "Noir",
        "white": "Blanc",
        "blue": "Bleu",
        "blue metal": "Bleu métal",
        "deep grey": "Gris",
        "grey": "Gris",
        "basalt grey": "Gris",
        "chalk grey": "Gris",
        "anthracite": "Anthracite",
        "red": "Rouge",
        "silver": "Argent",
    }
    fr = color_map.get(base)
    if fr is None:
        # Décos graphiques (discipline-tc1, realm-tc5, whizzy-tc-1...) : on garde tel quel, titré.
        return raw.replace("-", " ").strip().title()
    return f"{fr} mat" if mat else fr


def _parse_restock(text: str | None) -> str | None:
    """"Expédition prévue entre le 13 et le 17 août" -> ISO de la dernière date.

    On prend la borne haute (date la plus tardive) comme estimation de réappro.
    Le mois donne l'année (gestion du passage d'année si mois déjà passé).
    """
    if not text:
        return None
    low = text.lower()
    # On cible la dernière paire "<jour> <mois>" (borne haute de la fourchette).
    pairs = re.findall(r"(\d{1,2})\s+([a-zàâäéèêëîïôöùûüç]+)", low)
    for day_s, month_s in reversed(pairs):
        month = _FR_MONTHS.get(month_s)
        if month:
            day = int(day_s)
            today = date.today()
            year = today.year
            if (month, day) < (today.month, today.day):
                year += 1
            try:
                return f"{year:04d}-{month:02d}-{day:02d}T00:00:00+02:00"
            except ValueError:
                return None
    return None


# Lit le payload window.opts + le <select class=attrcomb> des tailles.
_EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const h1 = document.querySelector("h1");

  // 1) Le <select class=attrcomb> dont les options sont des tailles.
  const selects = [...document.querySelectorAll("select.attrcomb")];
  let sizeSelect = null;
  for (const s of selects) {
    const labels = [...s.options].map((o) => norm(o.textContent));
    if (labels.some((l) => /^(2?XS|XXS|XS|S|M|L|XL|XXL|2XL|3XL)$/i.test(l))) {
      sizeSelect = s;
      break;
    }
  }
  const sizeMap = [];
  if (sizeSelect) {
    for (const o of sizeSelect.options) {
      const key = o.value;
      const label = norm(o.textContent);
      if (key && key !== "0" && /^(2?XS|XXS|XS|S|M|L|XL|XXL|2XL|3XL)$/i.test(label)) {
        sizeMap.push({ key, label: label.toUpperCase() });
      }
    }
  }

  // 2) Agrège toutes les entrées p.o de window.opts (clé "g-a" -> statut/texte).
  const allO = {};
  try {
    for (const k in (window.opts || {})) {
      const p = window.opts[k];
      if (p && p.o) {
        for (const ok in p.o) {
          if (ok.indexOf("-") > 0) allO[ok] = p.o[ok];
        }
      }
    }
  } catch (e) {}

  const sizes = sizeMap.map(({ key, label }) => {
    const o = allO[key] || null;
    const status = o ? (o.status || "") : "";
    return {
      size: label,
      available: /^IN_STOCK/i.test(status),
      status,
      text: o ? o.text : null,
    };
  });

  let price = null;
  const pm = (document.body.innerText || "").match(/(\d[\d  ]*[.,]\d{2})\s*€/);
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
    let u;
    try { u = new URL(h, location.origin); } catch (e) { continue; }
    const p = u.pathname.toLowerCase();
    // /casque-{integral|jet|modulable}-shoei-{gt-air-3|j-cruise-3}-{couleur}.html
    if (!/^\/casque-(integral|jet|modulable)-shoei-(gt-air-3|j-cruise-3)-[a-z0-9-]+\.html$/.test(p)) {
      continue;
    }
    const full = u.origin + u.pathname;
    if (seen.has(full)) continue;
    seen.add(full);
    out.push(full);
  }
  return out;
}
"""


class IcasqueScraper(BaseScraper):
    domains = ("icasque.com",)
    site_name = "iCasque"

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
                    restock=None if s["available"] else _parse_restock(s.get("text")),
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
