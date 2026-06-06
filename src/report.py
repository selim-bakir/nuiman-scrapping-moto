"""Génération du rapport journalier au format texte (HTML Telegram)."""

from __future__ import annotations

import html

from .models import Report, ProductResult

# Limite Telegram d'un message : 4096 caractères. On découpe au besoin.
TELEGRAM_MAX_LEN = 4096


def _format_product(result: ProductResult) -> str:
    if result.error:
        return (
            f"⚠️ <b>{html.escape(result.name)}</b>\n"
            f"   Erreur de scraping : {html.escape(result.error)}"
        )

    lines = [f"<b>{html.escape(result.name)}</b>"]
    if result.price:
        lines.append(f"   💶 {html.escape(result.price)}")

    if result.sold_out and not result.available_sizes:
        lines.append("   🔴 Rupture totale (aucune taille disponible)")

    if not result.sizes:
        lines.append("   <i>Aucune taille détectée sur la page</i>")
    else:
        size_bits = []
        for s in result.sizes:
            icon = "🟢" if s.available else "🔴"
            size_bits.append(f"{icon} {html.escape(s.size)}")
        lines.append("   " + "  ".join(size_bits))

    return "\n".join(lines)


def build_report_text(report: Report) -> str:
    """Construit le texte complet du rapport (dispo + indispo, par taille)."""
    date_str = report.generated_at.strftime("%d/%m/%Y %H:%M")
    total = len(report.results)
    ok = report.ok_results
    failed = report.failed_results

    nb_sold_out = sum(1 for r in ok if r.sold_out and not r.available_sizes)
    nb_partial = sum(
        1 for r in ok if r.unavailable_sizes and r.available_sizes
    )

    header = [
        f"🏍️ <b>Rapport disponibilité moto</b> — {date_str}",
        "",
        f"📦 {total} modèle(s) surveillé(s)",
        f"🔴 {nb_sold_out} en rupture totale · ⚖️ {nb_partial} partiellement indispo",
    ]
    if failed:
        header.append(f"⚠️ {len(failed)} en erreur")
    header.append("")

    body = [_format_product(r) for r in report.results]

    return "\n".join(header) + "\n" + "\n\n".join(body)


def split_for_telegram(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    """Découpe le texte en morceaux <= limit, sans couper au milieu d'une ligne."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # Ligne unique trop longue : on la coupe brutalement.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks
