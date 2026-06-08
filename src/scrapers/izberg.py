"""Scraper pour la plateforme Izberg (lundimatin), qui sert deux sites
identiques : moto-axxe.fr et maxxess.fr.

SPA Svelte rendu côté serveur, sans anti-bot. On navigue en
``wait_until="domcontentloaded"`` + ``wait_for_timeout`` (le ``networkidle``
timeoute à cause des trackers tiers : Fitle, GA, Sentry, YouTube…).

Disponibilité par taille
-------------------------
Le site ne rend dans le DOM QUE les boutons de tailles réellement en stock
(``<input class="btn-check-produit">`` + ``<label class="btn-size-produit">``).
Une taille en rupture est simplement absente de la liste.

Le jeu COMPLET des tailles du modèle est exposé par le composant Fitle
``<fitle-size-recommender>`` (desktop) via son attribut ``availableSizes``
(ex. ``"XS,S,M,L,XL,2XL"``), peuplé après hydratation JS. On en déduit :

    dispo(taille) = taille présente dans les inputs rendus
    rupture(taille) = taille du jeu complet absente des inputs rendus

Faute de date de réappro exposée, ``restock`` reste ``None``.

Coloris
-------
Le ``<h1>`` ne contient pas le coloris ("SHOEI Casque GT-AIR 3"). En revanche
le ``<title>`` le porte : ``"Casque GT-AIR 3 SHOEI noir mat - MOTO-AXXE.FR,
Casque intégral"``. On extrait le texte entre ``SHOEI `` et `` - ``. Le coloris
du slug d'URL peut être erroné : le titre fait foi.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from playwright.async_api import Page

from ..config import ProductConfig
from ..models import ProductResult, SizeStatus
from .base import BaseScraper

SIZE_ORDER = ["3XS", "2XS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL"]
KNOWN_GAMMES = ["GT-AIR 3", "J-CRUISE 3"]

_SITE_BY_DOMAIN = {
    "moto-axxe.fr": "Moto Axxe",
    "maxxess.fr": "Maxxess",
}


def _normalize_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _sort_sizes(sizes: list[str]) -> list[str]:
    norm = {"XXL": "2XL"}

    def key(s: str):
        u = norm.get(s.upper(), s.upper())
        return (SIZE_ORDER.index(u) if u in SIZE_ORDER else 99, u)

    return sorted(sizes, key=key)


def _gamme_from_title(title: str) -> str | None:
    up = (title or "").upper()
    for g in KNOWN_GAMMES:
        if g in up:
            return g
    return None


def _color_from_title(title: str) -> str | None:
    """'Casque GT-AIR 3 SHOEI noir mat - MOTO-AXXE.FR, ...' -> 'Noir mat'."""
    if not title:
        return None
    m = re.search(r"SHOEI\s+(.+?)\s*[-–]\s*[A-Z0-9.\- ]+\.FR", title, re.IGNORECASE)
    if not m:
        # Repli : tout ce qui suit "SHOEI " jusqu'à un tiret éventuel.
        m = re.search(r"SHOEI\s+(.+?)(?:\s*[-–]|$)", title, re.IGNORECASE)
        if not m:
            return None
    color = m.group(1).strip(" -·,").strip()
    return color.title() if color else None


# Lit titre, prix, tailles rendues (= en stock) et jeu complet des tailles
# (attribut Fitle). availableSizes desktop est peuplé après hydratation JS.
_EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

  const h1 = document.querySelector("h1");
  const title = document.title || "";

  // Tailles rendues dans le DOM = tailles en stock (on évite les doublons
  // mobile : on ne prend que les inputs sans data-mobile="true").
  const rendered = [];
  const seen = new Set();
  for (const i of document.querySelectorAll("input.btn-check-produit")) {
    if (i.getAttribute("data-mobile") === "true") continue;
    const sz = (i.getAttribute("data-size") || "").trim().toUpperCase();
    if (sz && !seen.has(sz)) { seen.add(sz); rendered.push(sz); }
  }

  // Jeu complet des tailles du modèle, exposé par Fitle (desktop) après hydratation.
  let fullSet = "";
  for (const f of document.querySelectorAll("fitle-size-recommender")) {
    const a = (f.getAttribute("availableSizes") || "").trim();
    if (a) { fullSet = a; break; }
  }
  const full = fullSet
    ? fullSet.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean)
    : [];

  // Prix (premier montant en €).
  let price = null;
  const pm = (document.body.innerText || "").match(/(\d[\d  ]*[,\.]\d{2})\s*€/);
  if (pm) price = norm(pm[1]) + " €";

  // Image principale (JSON-LD Product si présent).
  let image = null;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const j = JSON.parse(s.textContent);
      const arr = Array.isArray(j) ? j : [j];
      for (const o of arr) {
        if (o && o["@type"] === "Product" && o.image) {
          image = Array.isArray(o.image) ? o.image[0] : o.image;
          break;
        }
      }
    } catch (e) { /* ignore */ }
    if (image) break;
  }

  return {
    name: h1 ? norm(h1.textContent) : null,
    title: norm(title),
    rendered,
    full,
    price,
    image,
  };
}
"""

# Listing : ne garder que les URLs produit GT-Air 3 / J-Cruise 3 du modèle suivi.
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
    if (!/\/produit\/shoei-casque-/.test(p)) continue;
    if (!(p.includes("gt-air-3") || p.includes("j-cruise-3"))) continue;
    const full = u.origin + u.pathname;
    if (seen.has(full)) continue;
    seen.add(full);
    out.push(full);
  }
  return out;
}
"""


class IzbergScraper(BaseScraper):
    domains = ("moto-axxe.fr", "maxxess.fr")
    # Affiché par défaut ; le vrai nom dépend du domaine de chaque produit.
    site_name = "Izberg"

    def site_for(self, url: str) -> str:
        """Nom lisible du site selon le domaine de l'URL produit."""
        return _SITE_BY_DOMAIN.get(_normalize_domain(url), self.site_name)

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
                await page.wait_for_timeout(2500)
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
        # Plusieurs <h1> peuvent exister (dont des variantes masquées) : on attend
        # juste qu'il soit attaché au DOM, pas qu'il soit "visible".
        await page.wait_for_selector("h1", state="attached", timeout=15000)
        # Laisse Fitle hydrater availableSizes (jeu complet des tailles).
        try:
            await page.wait_for_function(
                "() => { const f = document.querySelector('fitle-size-recommender:not([id$=\"mobile\"])');"
                " return f && (f.getAttribute('availableSizes') || '').trim().length > 0; }",
                timeout=6000,
            )
        except Exception:
            await page.wait_for_timeout(1500)
        data = await page.evaluate(_EXTRACT_JS)

        title = data.get("title") or ""
        gamme = _gamme_from_title(title) or _gamme_from_title(data.get("name") or "")
        color = _color_from_title(title)

        rendered = set(data.get("rendered") or [])
        full = list(data.get("full") or [])
        # Si Fitle n'a pas fourni le jeu complet, on se rabat sur les tailles rendues.
        all_sizes = full if full else sorted(rendered)

        sizes: list[SizeStatus] = []
        for sz in _sort_sizes(all_sizes):
            sizes.append(
                SizeStatus(
                    size=sz,
                    available=sz in rendered,
                    restock=None,  # Aucune date de réappro exposée par le site.
                )
            )

        sold_out = bool(sizes) and not any(s.available for s in sizes)

        return [
            ProductResult(
                url=product.url,
                name=product.label or data.get("name") or product.url,
                site=self.site_for(product.url),
                brand="Shoei",
                gamme=gamme,
                color=color,
                price=data.get("price"),
                image=data.get("image"),
                sizes=sizes,
                sold_out=sold_out,
                scraped_at=datetime.now(),
            )
        ]
