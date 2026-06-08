"""Scraper pour dafy-moto.com (plateforme Thelia, HTML server-rendered).

Logique de disponibilité observée sur le site :
  - Les tailles sont des <li> dans un sélecteur précédé de « Sélectionner une taille ».
  - Une taille indisponible contient le texte « Indisponible dans ce coloris »
    et un lien « M'alerter ».
  - Rupture totale du produit : texte « Victime de son succès » et absence du
    bouton « Ajouter au panier ».
"""

from __future__ import annotations

from datetime import datetime

from playwright.async_api import Page

from ..config import ProductConfig
from ..models import ProductResult, SizeStatus
from .base import BaseScraper

# Script exécuté dans la page pour extraire titre, prix et tailles.
# On reste tolérant aux variations de thème : on cible des textes plutôt que
# des classes CSS fragiles.
_EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

  // --- Titre ---
  const h1 = document.querySelector("h1");
  const name = h1 ? norm(h1.textContent) : null;

  // --- Prix (cherche un montant en €, en privilégiant "À partir de") ---
  let price = null;
  const priceEl = document.querySelector('[itemprop="price"], #pse-price, .price');
  if (priceEl) {
    price = norm(priceEl.getAttribute("content") || priceEl.textContent);
  }
  if (!price) {
    const m = (document.body.innerText || "").match(/A partir de\s*([\d\s.,]+€)/i);
    if (m) price = norm(m[1]);
  }

  // --- Rupture totale ---
  const bodyText = (document.body.innerText || "").toLowerCase();
  const soldOutSignal = bodyText.includes("victime de son succès");

  // --- Tailles ---
  // Regex de taille : lettres (S, M, L, XS, XL, 2XS..4XL) ou plage de tour de
  // tête (ex: 52-65). On exclut les nombres bruts (« 10 ») trop ambigus, sauf
  // s'ils ressemblent à un tour de tête (50-65).
  const sizeRe = /^\d?X*[SML]$|^\d{2}-\d{2}$|^(5\d|6[0-5])$/i;

  const firstToken = (txt) => txt.split(" ")[0];
  const isSize = (txt) => sizeRe.test(firstToken(txt));

  // On regroupe les <li> par <ul> parent et on retient la liste qui contient
  // le plus d'éléments de type taille : c'est le vrai sélecteur de taille.
  const lists = new Map();
  for (const li of document.querySelectorAll("li")) {
    const txt = norm(li.textContent);
    if (!txt || !isSize(txt)) continue;
    const parent = li.parentElement;
    if (!parent) continue;
    if (!lists.has(parent)) lists.set(parent, []);
    lists.get(parent).push(txt);
  }

  let bestList = null;
  let bestCount = 0;
  for (const [parent, items] of lists) {
    if (items.length > bestCount) {
      bestCount = items.length;
      bestList = items;
    }
  }

  const seen = new Set();
  const sizes = [];
  for (const txt of bestList || []) {
    const token = firstToken(txt).toUpperCase();
    if (seen.has(token)) continue;
    seen.add(token);
    const unavailable = /indisponible/i.test(txt) || /m'alerter|m’alerter|alerter/i.test(txt);
    sizes.push({ size: token, available: !unavailable });
  }

  return { name, price, soldOut: soldOutSignal, sizes };
}
"""


# Collecte les URLs produits d'une page listing.
# Un produit Dafy = /slug.html (un seul segment de path). On exclut les liens
# de navigation/footer (catégories, marques, pages CMS).
_LISTING_JS = r"""
() => {
  const out = new Set();
  for (const a of document.querySelectorAll("a[href]")) {
    if (a.closest("header, footer, nav")) continue;
    const raw = a.getAttribute("href");
    if (!raw) continue;
    let u;
    try { u = new URL(raw, location.origin); } catch (e) { continue; }
    if (u.origin !== location.origin) continue;
    const path = u.pathname;
    if (!path.endsWith(".html")) continue;
    if ((path.match(/\//g) || []).length !== 1) continue; // /xxx.html uniquement
    if (path === "/casques.html") continue;
    out.add(u.origin + path);
  }
  return [...out];
}
"""


def _drop_generic_urls(urls: list[str]) -> list[str]:
    """Retire les pages « génériques » Dafy (qui redirigent vers un coloris).

    Une page générique a un slug qui est le préfixe d'un slug de variante
    (ex: ``casque-atom-uni-all-one`` est préfixe de
    ``casque-atom-uni-all-one-noir-mat``). On conserve les variantes coloris.
    """
    def slug(u: str) -> str:
        return u.rsplit("/", 1)[-1].removesuffix(".html")

    pairs = [(slug(u), u) for u in urls]
    pairs.sort(key=lambda p: p[0])
    keep: list[str] = []
    n = len(pairs)
    for i, (s, u) in enumerate(pairs):
        # En ordre trié, une variante "s-..." suit immédiatement le générique "s".
        is_generic = i + 1 < n and pairs[i + 1][0].startswith(s + "-")
        if not is_generic:
            keep.append(u)
    return keep


class DafyScraper(BaseScraper):
    domains = ("dafy-moto.com",)
    site_name = "Dafy Moto"

    async def list_product_urls(
        self, page, category_url: str, max_pages: int = 300
    ) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for p in range(1, max_pages + 1):
            sep = "&" if "?" in category_url else "?"
            url = category_url if p == 1 else f"{category_url}{sep}p={p}"
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(700)
            except Exception:
                break
            links = await page.evaluate(_LISTING_JS)
            new = [u for u in links if u not in seen]
            if not new:
                # Page sans nouveau produit : fin de la pagination.
                break
            for u in new:
                seen.add(u)
                ordered.append(u)
        return _drop_generic_urls(ordered)

    async def scrape(self, page: Page, product: ProductConfig) -> list[ProductResult]:
        await page.goto(product.url, wait_until="domcontentloaded")
        # Le titre est server-rendered ; on l'attend pour confirmer le chargement.
        await page.wait_for_selector("h1", timeout=15000)

        data = await page.evaluate(_EXTRACT_JS)

        sizes = [SizeStatus(size=s["size"], available=s["available"]) for s in data["sizes"]]

        # Filtre éventuel sur les tailles surveillées.
        if product.sizes:
            wanted = {s.upper() for s in product.sizes}
            sizes = [s for s in sizes if s.size.upper() in wanted]

        sold_out = data["soldOut"] or (bool(sizes) and not any(s.available for s in sizes))

        return [
            ProductResult(
                url=product.url,
                name=product.label or data["name"] or product.url,
                site=self.site_name,
                price=data["price"],
                sizes=sizes,
                sold_out=sold_out,
                scraped_at=datetime.now(),
            )
        ]
