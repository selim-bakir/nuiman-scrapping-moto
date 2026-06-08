"""Tests de la génération de rapport (sans réseau)."""

from datetime import datetime

from src.models import ProductResult, Report, SizeStatus
from src.report import build_report_text, split_for_telegram


def _sample_report() -> Report:
    return Report(
        generated_at=datetime(2026, 6, 8, 8, 0),
        results=[
            ProductResult(
                url="https://www.motoblouz.com/vente-casque-shoei-nxr2-plain-1.html",
                name="Casque intégral Shoei NXR2 - PLAIN",
                site="Motoblouz",
                brand="Shoei",
                gamme="NXR2",
                color="PLAIN",
                price="479 €",
                sizes=[
                    SizeStatus("2XS", False),
                    SizeStatus("XS", True),
                    SizeStatus("S", True),
                    SizeStatus("M", True),
                ],
            ),
            ProductResult(
                url="https://www.motoblouz.com/vente-casque-shoei-nxr2-accolade-2.html",
                name="Casque intégral Shoei NXR2 - ACCOLADE",
                site="Motoblouz",
                brand="Shoei",
                gamme="NXR2",
                color="ACCOLADE",
                sizes=[SizeStatus("S", False), SizeStatus("M", False)],
                sold_out=True,
            ),
            ProductResult(
                url="https://www.motoblouz.com/vente-casque-shoei-gtair3-3.html",
                name="Casque intégral Shoei GT-Air 3",
                site="Motoblouz",
                brand="Shoei",
                gamme="GT-Air 3",
                color=None,
                price="599 €",
                sizes=[SizeStatus("M", True), SizeStatus("L", True)],
            ),
        ],
    )


def test_header_has_brand_and_counts():
    text = build_report_text(_sample_report())
    assert "DISPO CASQUES SHOEI" in text
    assert "3 casque(s) surveillé(s)" in text
    assert "🟢 1 complets · 🟡 1 partiels · 🔴 1 ruptures" in text


def test_pastilles_per_size():
    text = build_report_text(_sample_report())
    assert "🔴2XS 🟢XS 🟢S 🟢M" in text  # NXR2 PLAIN
    assert "🔴S 🔴M" in text  # ACCOLADE rupture


def test_grouped_by_gamme_with_links_and_price():
    text = build_report_text(_sample_report())
    assert "━━━ NXR2 ━━━" in text
    assert "━━━ GT-Air 3 ━━━" in text
    assert "PLAIN</a> · 479 €" in text
    assert 'href="https://www.motoblouz.com/vente-casque-shoei-nxr2-plain-1.html"' in text


def test_split_for_telegram_respects_limit():
    long_text = "\n".join(f"ligne {i} " * 20 for i in range(500))
    chunks = split_for_telegram(long_text, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert len(chunks) > 1


def test_split_short_text_single_chunk():
    assert split_for_telegram("court") == ["court"]
