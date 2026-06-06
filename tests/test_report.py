"""Tests de la génération de rapport (sans réseau)."""

from datetime import datetime

from src.models import ProductResult, Report, SizeStatus
from src.report import build_report_text, split_for_telegram


def _sample_report() -> Report:
    return Report(
        generated_at=datetime(2026, 6, 6, 8, 0),
        results=[
            ProductResult(
                url="https://www.dafy-moto.com/a.html",
                name="Shoei - Casque RF Noir",
                site="Dafy Moto",
                sizes=[
                    SizeStatus("S", False),
                    SizeStatus("M", False),
                    SizeStatus("L", True),
                    SizeStatus("XL", True),
                ],
            ),
            ProductResult(
                url="https://www.dafy-moto.com/b.html",
                name="Arai - Casque Quantic Blanc",
                site="Dafy Moto",
                sizes=[SizeStatus("S", False), SizeStatus("M", False)],
                sold_out=True,
            ),
            ProductResult(
                url="https://www.dafy-moto.com/c.html",
                name="Casque C",
                site="Dafy Moto",
                error="TimeoutError: navigation",
            ),
        ],
    )


def test_report_groups_by_site_with_alerts():
    text = build_report_text(_sample_report())
    assert "RAPPORT DISPO MOTO" in text
    # Nom du site présent
    assert "Dafy Moto" in text
    # Sections d'alerte
    assert "RUPTURES TOTALES" in text
    assert "TAILLES MANQUANTES" in text
    # Tailles manquantes du modèle partiel
    assert "❌ <b>S · M</b>" in text
    # Comptage des erreurs
    assert "1 erreur(s)" in text


def test_name_is_cleaned():
    text = build_report_text(_sample_report())
    # « Shoei - Casque RF Noir » → « Shoei RF Noir »
    assert "Shoei RF Noir" in text
    assert "Casque RF Noir" not in text


def test_full_available_not_detailed():
    report = Report(
        generated_at=datetime(2026, 6, 6, 8, 0),
        results=[
            ProductResult(
                url="https://www.dafy-moto.com/ok.html",
                name="Tout dispo",
                site="Dafy Moto",
                sizes=[SizeStatus("S", True), SizeStatus("M", True)],
            )
        ],
    )
    text = build_report_text(report)
    assert "Tout est disponible" in text


def test_split_for_telegram_respects_limit():
    long_text = "\n".join(f"ligne {i} " * 20 for i in range(500))
    chunks = split_for_telegram(long_text, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert len(chunks) > 1


def test_split_short_text_single_chunk():
    assert split_for_telegram("court") == ["court"]
