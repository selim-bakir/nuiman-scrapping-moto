"""Scraper pour teamaxe.com (PrestaShop 1.7 + thème Atomic, derrière Cloudflare).

⚠️ CLOUDFLARE — point central de ce scraper
--------------------------------------------
teamaxe.com est protégé par Cloudflare Bot Management. Le blocage se fait au
niveau de l'empreinte TLS/HTTP2 (JA3), AVANT tout challenge JS :

- Un simple ``requests.get`` renvoie ``403 Forbidden`` ("Request forbidden by
  administrative rules") — corps Cloudflare avec ``CF-RAY`` / ``Server: cloudflare``.
- **Playwright Chromium en headless est lui aussi bloqué (403)**, même avec un
  user-agent réaliste, headers complets, stealth (navigator.webdriver, etc.) et
  cookie ``cf_clearance`` posé : l'empreinte TLS du Chromium bundlé est rejetée.
- **Playwright Firefox en headless PASSE (HTTP 200)** : sa pile TLS/HTTP2 n'est
  pas dans la liste de blocage. C'est la solution retenue.

Comme le runner (``main.py``) partage un contexte **Chromium** entre tous les
scrapers, ce scraper IGNORE la ``page`` Chromium reçue et ouvre son PROPRE
navigateur **Firefox** (mis en cache sur l'instance, fermé à l'``atexit``).

Disponibilité par taille
-------------------------
PrestaShop 1.7 : ``window.combinations`` est vide au chargement (chargé en AJAX).
On interroge directement l'endpoint de rafraîchissement produit
(``?ajax=1&action=refresh``) pour chaque taille du groupe Taille (``group[1]``).
La réponse JSON contient le fragment HTML ``product_details`` dont le texte donne
l'état du stock :
  - "En stock, expédié sous 24h."          -> disponible
  - "Expédition prévue le JJ/MM/AAAA"      -> indisponible, restock = cette date
  - "Rupture" / "épuisé" / "indisponible"  -> indisponible, sans date

Un coloris = une URL produit (couleur dans le slug). Le prix est dans le
fragment ``product_prices``.
"""

from __future__ import annotations

import asyncio
import atexit
import re
from datetime import datetime

from playwright.async_api import Page, async_playwright

from ..config import ProductConfig
from ..models import ProductResult, SizeStatus
from .base import BaseScraper

SIZE_ORDER = ["3XS", "2XS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL"]
KNOWN_GAMMES = ["GT-AIR 3", "J-CRUISE 3"]

# Texte indiquant que la taille est en stock dans le fragment product_details.
_IN_STOCK_RE = re.compile(r"en stock|expédié sous|disponible imm", re.I)
# "Expédition prévue le 06/07/2026" -> date de réappro.
_RESTOCK_RE = re.compile(r"expédition\s+pr[ée]vue\s+le\s+(\d{2})/(\d{2})/(\d{4})", re.I)


def _sort_sizes(sizes: list[str]) -> list[str]:
    norm = {"XXL": "2XL"}

    def key(s):
        u = norm.get(s.upper(), s.upper())
        return (SIZE_ORDER.index(u) if u in SIZE_ORDER else 99, u)

    return sorted(sizes, key=key)


def _split_gamme_color(name: str) -> tuple[str | None, str | None]:
    """'Casque Shoei GT-AIR 3 Noir mat' -> ('GT-AIR 3', 'Noir mat')."""
    up = name.upper()
    for g in KNOWN_GAMMES:
        i = up.find(g)
        if i >= 0:
            rest = name[i + len(g):].strip(" -·")
            rest = re.sub(r"(?i)\b(promo|nouveaut[ée]|new|-?\d+%)\b", "", rest).strip()
            color = rest.title() if rest else None
            return g, color
    return None, None


def _color_from_url(url: str) -> str | None:
    """Extrait le coloris du slug.

    Intégral : '...-casque-shoei-gt-air-3-noir-mat.html'
    Jet       : '...-casque-jet-shoei-j-cruise-3-noir-mat.html' (le 'jet-' en plus)
    -> 'Noir Mat'.
    """
    m = re.search(
        r"casque-(?:jet-)?shoei-(?:gt-air-3|j-cruise-3)-(.+?)\.html", url, re.I
    )
    if not m:
        return None
    return m.group(1).replace("-", " ").strip().title() or None


# Récupère, pour chaque taille du groupe Taille, l'état de stock via l'AJAX
# PrestaShop ?action=refresh. Renvoyé : nom produit, prix, et par taille le
# texte product_details (qui contient "En stock" ou "Expédition prévue le ...").
_EXTRACT_JS = r"""
async () => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const strip = (h) => { const d = document.createElement("div"); d.innerHTML = h || ""; return norm(d.textContent); };

  const h1 = document.querySelector("h1");
  const idProduct = (document.querySelector("input[name=id_product]") || {}).value;
  // Coloris courant (group de couleur = data-product-attribute != "1").
  const colorInput =
    document.querySelector('input[data-product-attribute="0"]:checked') ||
    document.querySelector('input[data-product-attribute="0"]');

  // Inputs du groupe Taille (data-product-attribute="1").
  const sizeInputs = Array.from(document.querySelectorAll('input[data-product-attribute="1"]'));

  // Prix affiché sur la page (fallback si l'AJAX ne le renvoie pas).
  let price = null;
  const cp = document.querySelector(".current-price, [itemprop=price]");
  if (cp) price = norm(cp.getAttribute("content") ? null : cp.textContent) || null;
  if (!price) {
    const pm = (document.body.innerText || "").match(/(\d[\d  ]*,\d{2})\s*€/);
    if (pm) price = norm(pm[1]) + " €";
  }

  const sizes = [];
  for (const si of sizeInputs) {
    const label = norm(document.querySelector(`label[for="${si.id}"]`)?.textContent);
    if (!label) continue;
    const params = new URLSearchParams();
    params.set("quantity_wanted", "1");
    params.set("id_product", idProduct);
    if (colorInput) params.set(colorInput.name, colorInput.value);
    params.set("group[1]", si.value);
    params.set("id_product_attribute", "");
    params.set("ajax", "1");
    params.set("action", "refresh");
    let detailsTxt = "", priceTxt = null;
    try {
      const resp = await fetch(location.pathname + "?" + params.toString(), {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded" },
        body: params.toString(),
      });
      const j = await resp.json();
      detailsTxt = strip(j.product_details);
      const pd = document.createElement("div");
      pd.innerHTML = j.product_prices || "";
      const pc = pd.querySelector(".current-price, [itemprop=price]");
      priceTxt = pc ? norm(pc.textContent) : null;
    } catch (e) {
      detailsTxt = "";
    }
    sizes.push({ size: label.toUpperCase(), details: detailsTxt });
    if (priceTxt && /\d/.test(priceTxt)) price = priceTxt;
  }
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
    // Intégral : /fr/{cat}/{idprod}[-{idcombi}]-casque-shoei-{modele}[-{couleur}].html
    // Jet       : /fr/{cat}/{idprod}[-{idcombi}]-casque-jet-shoei-{modele}[...].html
    if (!/\/\d+-(?:\d+-)?casque-(?:jet-)?shoei-/.test(p)) continue;
    if (!(p.includes("gt-air-3") || p.includes("j-cruise-3"))) continue;
    // Canonicalise : retire l'éventuel id de combinaison pour dédupliquer par produit.
    const canon = p.replace(/\/(\d+)-\d+-casque-(jet-)?shoei-/, "/$1-casque-$2shoei-");
    const full = u.origin + canon;
    if (seen.has(full)) continue;
    seen.add(full);
    out.push(full);
  }
  return out;
}
"""


def _restock_iso(details: str) -> str | None:
    m = _RESTOCK_RE.search(details or "")
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}T00:00:00+02:00"


class TeamAxeScraper(BaseScraper):
    domains = ("teamaxe.com",)
    site_name = "Team Axe"

    def __init__(self) -> None:
        # Navigateur Firefox dédié (Chromium est bloqué par Cloudflare ici).
        self._pw = None
        self._browser = None
        self._context = None
        self._lock = asyncio.Lock()
        atexit.register(self._sync_close)

    async def _ff_page(self) -> Page:
        """Renvoie une page Firefox neuve (browser/context mis en cache)."""
        async with self._lock:
            if self._context is None:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.firefox.launch(headless=True)
                self._context = await self._browser.new_context(
                    locale="fr-FR",
                    timezone_id="Europe/Paris",
                    viewport={"width": 1366, "height": 900},
                    extra_http_headers={
                        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                    },
                )
        return await self._context.new_page()

    def _sync_close(self) -> None:
        if self._pw is None:
            return
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._aclose())
            loop.close()
        except Exception:
            pass

    async def _aclose(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        finally:
            self._pw = self._browser = self._context = None

    async def list_product_urls(
        self, page: Page, category_url: str, max_pages: int = 10
    ) -> list[str]:
        # ``page`` (Chromium partagé) est ignorée : on passe par Firefox.
        ff = await self._ff_page()
        seen: set[str] = set()
        ordered: list[str] = []
        try:
            for p in range(1, max_pages + 1):
                sep = "&" if "?" in category_url else "?"
                url = category_url if p == 1 else f"{category_url}{sep}p={p}"
                try:
                    await ff.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await ff.wait_for_timeout(800)
                except Exception:
                    break
                new = [u for u in await ff.evaluate(_LISTING_JS) if u not in seen]
                if not new:
                    break
                for u in new:
                    seen.add(u)
                    ordered.append(u)
        finally:
            await ff.close()
        return ordered

    async def scrape(self, page: Page, product: ProductConfig) -> list[ProductResult]:
        # ``page`` (Chromium partagé) est ignorée : on passe par Firefox.
        ff = await self._ff_page()
        try:
            await ff.goto(product.url, wait_until="domcontentloaded", timeout=45000)
            await ff.wait_for_selector("h1", timeout=15000)
            await ff.wait_for_timeout(600)
            data = await ff.evaluate(_EXTRACT_JS)
        finally:
            await ff.close()

        name = data.get("name") or product.url
        gamme, color = _split_gamme_color(name)
        if not color:
            color = _color_from_url(product.url)

        seen: dict[str, dict] = {}
        for s in data.get("sizes", []):
            seen.setdefault(s["size"], s)

        sizes: list[SizeStatus] = []
        for sz in _sort_sizes(list(seen)):
            details = seen[sz].get("details", "")
            available = bool(_IN_STOCK_RE.search(details)) and not _RESTOCK_RE.search(details)
            sizes.append(
                SizeStatus(
                    size=sz,
                    available=available,
                    restock=None if available else _restock_iso(details),
                )
            )

        sold_out = bool(sizes) and not any(s.available for s in sizes)
        return [
            ProductResult(
                url=product.url,
                name=product.label or name,
                site=self.site_name,
                brand="Shoei",
                gamme=gamme,
                color=color,
                price=data.get("price"),
                sizes=sizes,
                sold_out=sold_out,
                scraped_at=datetime.now(),
            )
        ]
