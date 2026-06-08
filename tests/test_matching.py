"""Tests du croisement Motoblouz ↔ Dafy (matching strict, sans faux positifs)."""

from src.models import ProductResult, SizeStatus
from src.matching import _best_dafy_match, cross_check


def _dafy(name, sizes):
    return ProductResult(
        url="https://dafy/" + name, name=name, site="Dafy Moto",
        sizes=[SizeStatus(s, av) for s, av in sizes],
    )


DAFY = [
    _dafy("Shoei - Casque GT-Air 3 Noir Mat", [("M", True), ("L", False)]),
    _dafy("Shoei - Casque GT-Air 3 Blanc", [("M", True)]),
    _dafy("Shoei - Casque GT-Air 3 Agility TC-1 / Rouge / Noir", [("M", False)]),
    _dafy("Shoei - Casque GT-Air 3 Discipline TC-1 / Argent / Rouge", [("M", True)]),
    _dafy("Shoei - Casque NXR2 Noir Mat", [("S", True)]),
]


def _moto(gamme, color):
    return ProductResult(url="m", name=f"{gamme} {color}", gamme=gamme, color=color, site="Motoblouz")


def test_uni_matches_uni_not_graphic():
    # "GT-AIR 3 Noir" doit matcher l'uni "Noir Mat", PAS un graphique contenant "noir"
    m = _best_dafy_match(_moto("GT-AIR 3", "Noir"), DAFY)
    assert m is not None and "Noir Mat" in m.name


def test_graphic_matches_exact_series():
    m = _best_dafy_match(_moto("GT-AIR 3", "DISCIPLINE · Noir/Bleu"), DAFY)
    assert m is not None and "Discipline" in m.name


def test_graphic_without_dafy_equivalent_is_unmatched():
    # Série absente de Dafy → pas de match (pas de faux positif)
    assert _best_dafy_match(_moto("GT-AIR 3", "MARC MARQUEZ MOTEGI 5 · Noir/Rouge"), DAFY) is None


def test_plain_keyword_treated_as_uni():
    m = _best_dafy_match(_moto("NXR2", "PLAIN · Noir/Blanc"), DAFY)
    assert m is not None and "NXR2 Noir Mat" in m.name


def test_cross_check_annotates_sizes():
    moto = _moto("GT-AIR 3", "Noir")
    moto.sizes = [SizeStatus("M", False), SizeStatus("L", False)]
    stats = cross_check([moto], DAFY)
    by = {s.size: s.dafy_available for s in moto.sizes}
    assert by["M"] is True   # dispo sur Dafy (Noir Mat M dispo)
    assert by["L"] is False  # confirmé rupture (Noir Mat L indispo)
    assert stats["matched"] == 1
