"""Tests de la génération de rapport (sans réseau)."""

from datetime import datetime

from src.models import ProductResult, Report, SizeStatus
from src.report import (
    build_report_text,
    fmt_restock,
    split_for_telegram,
    to_report,
)


def _sample_report() -> Report:
    return Report(
        generated_at=datetime(2026, 6, 8, 8, 0),
        results=[
            ProductResult(
                url="https://www.motoblouz.com/vente-casque-shoei-nxr2-plain-1.html",
                name="Casque intégral Shoei NXR2 - PLAIN",
                site="Motoblouz", brand="Shoei", gamme="NXR2", color="PLAIN",
                price="479 €",
                sizes=[
                    SizeStatus("2XS", False, restock="2026-07-20T00:00:00+02:00"),
                    SizeStatus("XS", True),
                    SizeStatus("S", True),
                    SizeStatus("M", True),
                ],
            ),
            ProductResult(
                url="https://www.motoblouz.com/vente-casque-shoei-nxr2-accolade-2.html",
                name="Casque intégral Shoei NXR2 - ACCOLADE",
                site="Motoblouz", brand="Shoei", gamme="NXR2", color="ACCOLADE",
                sizes=[SizeStatus("S", False), SizeStatus("M", False)],
                sold_out=True,
            ),
            ProductResult(
                url="https://www.motoblouz.com/vente-casque-shoei-gtair3-3.html",
                name="Casque intégral Shoei GT-Air 3",
                site="Motoblouz", brand="Shoei", gamme="GT-Air 3", color=None,
                price="599 €",
                sizes=[SizeStatus("M", True), SizeStatus("L", True)],  # 100% dispo
            ),
        ],
    )


def test_header_counts():
    text = build_report_text(_sample_report())
    assert "RUPTURES CASQUES SHOEI" in text
    assert "3 casque(s) surveillé(s) · 🚨 2 à signaler" in text
    assert "🟢 1 complets · 🟡 1 partiels · 🔴 1 ruptures totales" in text


def test_only_ruptures_shown():
    text = build_report_text(_sample_report())
    # NXR2 (partiel + rupture) présent, GT-Air 3 (100% dispo) absent
    assert "━━━ NXR2 ━━━" in text
    assert "GT-Air 3" not in text
    assert "PLAIN</a> · 479 €" in text


def test_only_unavailable_sizes_with_restock():
    text = build_report_text(_sample_report())
    # PLAIN : seule 2XS est indispo (avec date), les dispo XS/S/M n'apparaissent pas
    assert "🔴 <b>2XS</b> · réappro 20/07/26" in text
    assert "🟢XS" not in text and "🟢 <b>XS" not in text  # aucune taille dispo listée
    # ACCOLADE : tailles indispo sans date → "non communiquée"
    assert "🔴 <b>S</b> · réappro non communiquée" in text


def test_to_report_filters_ok():
    flagged = to_report(_sample_report().results)
    assert len(flagged) == 2  # PLAIN (partiel) + ACCOLADE (rupture)


def test_fmt_restock():
    assert fmt_restock("2026-06-17T00:00:00+02:00") == "17/06/26"
    assert fmt_restock(None) is None


def test_split_for_telegram_respects_limit():
    long_text = "\n".join(f"ligne {i} " * 20 for i in range(500))
    chunks = split_for_telegram(long_text, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert len(chunks) > 1
