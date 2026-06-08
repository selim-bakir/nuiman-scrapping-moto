"""Croisement de la disponibilité entre Motoblouz (primaire) et Dafy (contrôle).

Pas de code-barres commun entre les deux sites : on apparie les casques par
gamme canonique + série/coloris, en DEUX passes :

  Passe 1 (haute confiance) — gamme identique ET :
      • graphique  : tous les termes de la série figurent dans le nom Dafy ;
      • uni        : coloris simple Dafy partageant une couleur.
  Passe 2 (recoupement) — pour les casques non appariés, meilleur candidat de
      même gamme avec ≥2 termes de série distinctifs communs, VALIDÉ par le
      recoupement des tailles (les deux casques doivent proposer ~les mêmes).

Le résultat annote chaque ``SizeStatus`` indispo des casques Motoblouz via
``dafy_available`` (True=dispo Dafy, False=confirmé rupture, None=non vérifié).
"""

from __future__ import annotations

import re
import unicodedata

from .models import ProductResult

# Gammes Shoei canoniques (les plus spécifiques d'abord) pour rattacher chaque
# casque à une racine commune entre les deux sites.
KNOWN_GAMMES = [
    "GT-AIR 3", "NEOTEC 3", "X-SPR PRO", "GLAMSTER 06", "J-CRUISE 3",
    "HORNET ADV 06", "HORNET ADV", "X-SPIRIT 3", "VFX-WR", "NXR2",
    "GLAMSTER", "J-CRUISE", "J.O2", "J-O2", "RYD",
]

_COLORS = {
    "noir", "blanc", "gris", "rouge", "bleu", "vert", "verte", "jaune", "orange",
    "argent", "anthracite", "rose", "violet", "beige", "marron", "or", "carbone",
    "chrome", "titane", "mat", "matt", "brillant", "satin", "fluo", "bronze",
    "sable", "kaki", "bordeaux", "turquoise", "ivoire", "creme", "gold", "silver",
    "metal", "cuivre", "fonce", "clair",
}


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _key(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", _strip_accents(s or "").lower())


def _tokens(s: str | None) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", _strip_accents(s or "").lower()).split())


def _is_noise(tok: str) -> bool:
    # "plain"/"uni" = mot-clé de coloris uni, pas une série graphique.
    return tok in {"shoei", "casque", "plain", "uni"} or bool(re.fullmatch(r"tc\d*|\d+", tok))


def _canon_gamme(text: str | None) -> str | None:
    """Rattache un nom à une gamme Shoei connue (ex: 'X-SPR Pro 02 FIM2' -> 'X-SPR PRO')."""
    k = _key(text)
    for g in KNOWN_GAMMES:
        if _key(g) in k:
            return g
    return None


def _series_tokens(name: str, gamme: str | None) -> set[str]:
    """Termes de série/graphisme (hors gamme, couleurs, codes TC, bruit, mots courts)."""
    gtoks = _tokens(gamme)
    return {
        t for t in _tokens(name)
        if t not in _COLORS and t not in gtoks and not _is_noise(t) and len(t) >= 3
    }


def _color_tokens(name: str) -> set[str]:
    return {t for t in _tokens(name) if t in _COLORS}


def _sizes_of(r: ProductResult) -> set[str]:
    return {s.size.upper() for s in r.sizes}


def _sizes_compatible(moto: ProductResult, dafy: ProductResult) -> bool:
    """Les deux casques proposent ~le même jeu de tailles (garde-fou anti faux positif)."""
    a, b = _sizes_of(moto), _sizes_of(dafy)
    if not a or not b:
        return True
    return len(a & b) / len(a | b) >= 0.5


def _split_finition(color: str | None):
    color = color or ""
    if " · " in color:
        fin, cols = color.split(" · ", 1)
    else:
        fin, cols = "", color
    fin = fin.strip()
    if fin.upper() == "PLAIN":
        fin = ""
    return fin, cols


def _annotate(moto: ProductResult, dafy: ProductResult) -> tuple[int, int]:
    """Reporte la dispo Dafy sur les tailles indispo Motoblouz. Renvoie (confirmées, ailleurs)."""
    dafy_sizes = {s.size.upper(): s.available for s in dafy.sizes}
    confirmed = elsewhere = 0
    for s in moto.sizes:
        if s.available:
            continue
        dispo = dafy_sizes.get(s.size.upper())
        s.dafy_available = dispo
        if dispo is True:
            elsewhere += 1
        elif dispo is False:
            confirmed += 1
    return confirmed, elsewhere


def cross_check(moto_results: list[ProductResult], dafy_results: list[ProductResult]) -> dict:
    """Croisement en deux passes. Annote les tailles et renvoie des statistiques."""
    dafy = [d for d in dafy_results if d.error is None]
    # Pré-calcul des attributs Dafy.
    d_info = [
        {
            "r": d,
            "gamme": _canon_gamme(d.name),
            "series": _series_tokens(d.name, _canon_gamme(d.name)),
            "colors": _color_tokens(d.name),
        }
        for d in dafy
    ]

    matched = confirmed = elsewhere = 0
    pending: list[ProductResult] = []

    # --- Passe 1 : haute confiance ---
    for moto in moto_results:
        if moto.error is not None:
            continue
        gam = _canon_gamme(moto.gamme or moto.name)
        if not gam:
            pending.append(moto)
            continue
        cands = [d for d in d_info if d["gamme"] == gam and _sizes_compatible(moto, d["r"])]
        if not cands:
            pending.append(moto)
            continue

        finition, couleurs = _split_finition(moto.color)
        fin_toks = {t for t in _tokens(finition) if t not in _COLORS and not _is_noise(t) and len(t) >= 3}
        # Série aussi dérivée du nom complet (cas sans séparateur " - ").
        fin_toks |= _series_tokens(moto.name, gam)

        chosen = None
        if fin_toks:
            for d in cands:
                if fin_toks <= _tokens(d["r"].name):
                    chosen = d["r"]
                    break
        else:
            moto_colors = _color_tokens(couleurs)
            best, best_score = None, 0
            for d in cands:
                if d["series"]:
                    continue  # Dafy graphique → pas un uni
                score = len(moto_colors & d["colors"])
                if score > best_score:
                    best, best_score = d["r"], score
            chosen = best if best_score >= 1 else None

        if chosen is not None:
            matched += 1
            c, e = _annotate(moto, chosen)
            confirmed += c
            elsewhere += e
        else:
            pending.append(moto)

    # --- Passe 2 : recoupement fuzzy validé par les tailles ---
    fuzzy = 0
    for moto in pending:
        gam = _canon_gamme(moto.gamme or moto.name)
        if not gam:
            continue
        moto_series = _series_tokens(moto.name, gam) | {
            t for t in _tokens(_split_finition(moto.color)[0]) if len(t) >= 3 and t not in _COLORS
        }
        if not moto_series:
            continue
        best, best_score = None, 0
        for d in d_info:
            if d["gamme"] != gam or not _sizes_compatible(moto, d["r"]):
                continue
            score = len(moto_series & d["series"])
            if score > best_score:
                best, best_score = d["r"], score
        # Validation : au moins 2 termes de série distinctifs communs.
        if best is not None and best_score >= 2:
            matched += 1
            fuzzy += 1
            c, e = _annotate(moto, best)
            confirmed += c
            elsewhere += e

    return {
        "matched": matched,
        "fuzzy_pass2": fuzzy,
        "confirmed_ruptures": confirmed,
        "available_on_dafy": elsewhere,
        "unmatched": len([m for m in moto_results if m.error is None]) - matched,
    }


# Conservé pour les tests unitaires (appariement unitaire d'un casque).
def _best_dafy_match(moto: ProductResult, dafy: list[ProductResult]) -> ProductResult | None:
    gam = _canon_gamme(moto.gamme or moto.name)
    if not gam:
        return None
    d_info = [(d, _canon_gamme(d.name), _series_tokens(d.name, _canon_gamme(d.name)), _color_tokens(d.name)) for d in dafy if d.error is None]
    cands = [(d, ser, col) for (d, dg, ser, col) in d_info if dg == gam]
    if not cands:
        return None
    finition, couleurs = _split_finition(moto.color)
    fin_toks = {t for t in _tokens(finition) if t not in _COLORS and not _is_noise(t) and len(t) >= 3}
    fin_toks |= _series_tokens(moto.name, gam)
    if fin_toks:
        for d, ser, col in cands:
            if fin_toks <= _tokens(d.name):
                return d
        return None
    moto_colors = _color_tokens(couleurs)
    best, best_score = None, 0
    for d, ser, col in cands:
        if ser:
            continue
        score = len(moto_colors & col)
        if score > best_score:
            best, best_score = d, score
    return best if best_score >= 1 else None
