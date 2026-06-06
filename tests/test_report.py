"""Tests de la génération de rapport (sans réseau)."""

from datetime import datetime

from src.models import ProductResult, Report, SizeStatus
from src.report import build_report_text, split_for_telegram


def _sample_report() -> Report:
    return Report(
        generated_at=datetime(2026, 6, 6, 9, 0),
        results=[
            ProductResult(
                url="https://www.dafy-moto.com/a.html",
                name="Casque A",
                price="419,16 €",
                sizes=[
                    SizeStatus("S", False),
                    SizeStatus("M", False),
                    SizeStatus("L", True),
                    SizeStatus("XL", True),
                ],
            ),
            ProductResult(
                url="https://www.dafy-moto.com/b.html",
                name="Casque B",
                price="529 €",
                sizes=[SizeStatus("S", False), SizeStatus("M", False)],
                sold_out=True,
            ),
            ProductResult(
                url="https://www.dafy-moto.com/c.html",
                name="Casque C",
                error="TimeoutError: navigation",
            ),
        ],
    )


def test_build_report_contains_counts_and_sizes():
    text = build_report_text(_sample_report())
    assert "3 modèle(s) surveillé(s)" in text
    assert "Casque A" in text
    assert "🟢 L" in text
    assert "🔴 S" in text
    assert "Rupture totale" in text
    assert "Erreur de scraping" in text


def test_split_for_telegram_respects_limit():
    long_text = "\n".join(f"ligne {i} " * 20 for i in range(500))
    chunks = split_for_telegram(long_text, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert len(chunks) > 1


def test_split_short_text_single_chunk():
    assert split_for_telegram("court") == ["court"]
