"""Croisement de la disponibilité entre Motoblouz (primaire) et Dafy (contrôle).

Pas de code-barres commun entre les deux sites : on matche les casques par
gamme (obligatoire) + recouvrement des termes de coloris/série, puis on compare
la disponibilité taille par taille. Le résultat annote chaque ``SizeStatus``
des casques Motoblouz via ``dafy_available``.
"""

from __future__ import annotations

import re
import unicodedata

from .models import ProductResult


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _key(s: str | None) -> str:
    """Clé normalisée sans accents ni séparateurs (ex: 'GT-Air 3' -> 'gtair3')."""
    return re.sub(r"[^a-z0-9]", "", _strip_accents(s or "").lower())


def _tokens(s: str | None) -> set[str]:
    """Ensemble de mots normalisés (ex: 'DISCIPLINE · Noir/Bleu' -> {discipline,noir,bleu})."""
    return set(re.sub(r"[^a-z0-9 ]", " ", _strip_accents(s or "").lower()).split())


# Vocabulaire de couleurs FR (pour distinguer un coloris uni d'une série graphique).
_COLORS = {
    "noir", "blanc", "gris", "rouge", "bleu", "verte", "vert", "jaune", "orange",
    "argent", "anthracite", "rose", "violet", "beige", "marron", "or", "carbone",
    "chrome", "titane", "mat", "matt", "brillant", "satin", "fluo", "bronze", "sable",
    "kaki", "bordeaux", "turquoise", "ivoire", "creme", "gold", "silver", "metal",
}


def _is_noise(tok: str) -> bool:
    return tok in {"shoei", "casque"} or bool(re.fullmatch(r"tc\d*|\d+", tok))


def _series_tokens(name: str, gamme: str | None) -> set[str]:
    """Tokens de série/graphisme d'un nom (hors gamme, couleurs, codes TC, bruit)."""
    gtoks = _tokens(gamme)
    return {
        t for t in _tokens(name)
        if t not in _COLORS and t not in gtoks and not _is_noise(t)
    }


def _split_finition(color: str | None):
    """Sépare la finition/série des couleurs (champ Motoblouz 'FINITION · couleurs')."""
    color = color or ""
    if " · " in color:
        fin, cols = color.split(" · ", 1)
    else:
        fin, cols = "", color
    fin = fin.strip()
    # "PLAIN" = coloris uni chez Motoblouz : pas une série graphique.
    if fin.upper() == "PLAIN":
        fin = ""
    return fin, cols


def _best_dafy_match(moto: ProductResult, dafy: list[ProductResult]) -> ProductResult | None:
    """Match strict : gamme obligatoire + série exacte (graphique) ou uni↔uni."""
    gamme_key = _key(moto.gamme)
    if not gamme_key:
        return None
    candidates = [d for d in dafy if d.error is None and gamme_key in _key(d.name)]
    if not candidates:
        return None

    finition, couleurs = _split_finition(moto.color)
    fin_toks = {t for t in _tokens(finition) if t not in _COLORS and not _is_noise(t)}

    if fin_toks:
        # Casque graphique : la série Dafy doit contenir TOUS les tokens de finition.
        for d in candidates:
            if fin_toks <= _tokens(d.name):
                return d
        return None

    # Coloris uni : ne matcher qu'un uni Dafy (sans série) partageant une couleur.
    moto_colors = {t for t in _tokens(couleurs) if t in _COLORS}
    if not moto_colors:
        return None
    best, best_score = None, 0
    for d in candidates:
        if _series_tokens(d.name, moto.gamme):
            continue  # Dafy graphique → pas un uni
        d_colors = {t for t in _tokens(d.name) if t in _COLORS}
        score = len(moto_colors & d_colors)
        if score > best_score:
            best, best_score = d, score
    return best if best_score >= 1 else None


def cross_check(moto_results: list[ProductResult], dafy_results: list[ProductResult]) -> dict:
    """Annote chaque taille indispo Motoblouz avec la dispo Dafy. Renvoie des stats."""
    matched = 0
    confirmed = 0  # tailles indispo sur les DEUX
    available_elsewhere = 0  # indispo Motoblouz mais dispo Dafy
    for moto in moto_results:
        if moto.error is not None:
            continue
        dmatch = _best_dafy_match(moto, dafy_results)
        if dmatch is None:
            continue
        matched += 1
        dafy_sizes = {s.size.upper(): s.available for s in dmatch.sizes}
        for s in moto.sizes:
            if s.available:
                continue
            dispo = dafy_sizes.get(s.size.upper())
            s.dafy_available = dispo
            if dispo is True:
                available_elsewhere += 1
            elif dispo is False:
                confirmed += 1
    return {
        "matched": matched,
        "confirmed_ruptures": confirmed,
        "available_on_dafy": available_elsewhere,
    }
