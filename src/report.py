"""Génération du rapport journalier Telegram — ultra lisible, groupé par site.

Principe : on met en avant les ALERTES (ruptures totales et tailles manquantes),
groupées par site. Les modèles 100 % disponibles ne sont comptés que dans le
résumé. Format HTML Telegram (gras, liens cliquables).
"""

from __future__ import annotations

import html

from .models import Report, ProductResult

# Limite Telegram d'un message : 4096 caractères. On découpe au besoin.
TELEGRAM_MAX_LEN = 4096

_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _fr_date(dt) -> str:
    return f"{dt.day} {_MONTHS[dt.month - 1]} {dt.year} à {dt:%Hh%M}"


def _clean_name(name: str) -> str:
    # Allège le titre Dafy « Marque - Casque Modèle » → « Marque Modèle ».
    return name.replace(" - Casque ", " ").replace("Casque ", "").strip()


def _name_link(result: ProductResult) -> str:
    name = html.escape(_clean_name(result.name))
    return f'<a href="{html.escape(result.url)}">{name}</a>'


def _sizes_line(result: ProductResult) -> str:
    missing = " · ".join(html.escape(s) for s in result.unavailable_sizes)
    return f"   ❌ <b>{missing}</b>"


def _site_section(site: str, results: list[ProductResult]) -> str:
    sold_out = [r for r in results if r.error is None and r.sold_out and not r.available_sizes]
    partial = [r for r in results if r.error is None and r.available_sizes and r.unavailable_sizes]
    ok = [r for r in results if r.error is None and not r.unavailable_sizes and not r.sold_out]
    failed = [r for r in results if r.error is not None]
    alerts = len(sold_out) + len(partial)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"🌐 <b>{html.escape(site or 'Site inconnu')}</b>",
        f"📦 {len(results)} réf.   ✅ {len(ok)} OK   🔴 {alerts} alerte(s)"
        + (f"   ⚠️ {len(failed)} erreur(s)" if failed else ""),
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if sold_out:
        lines.append("")
        lines.append(f"⛔ <b>RUPTURES TOTALES</b> · {len(sold_out)}")
        for r in sold_out:
            lines.append(f"• {_name_link(r)}")

    if partial:
        lines.append("")
        lines.append(f"🔴 <b>TAILLES MANQUANTES</b> · {len(partial)}")
        for r in partial:
            lines.append("")
            lines.append(_name_link(r))
            lines.append(_sizes_line(r))

    if not sold_out and not partial:
        lines.append("")
        lines.append("✅ Tout est disponible dans toutes les tailles.")

    return "\n".join(lines)


def build_report_text(report: Report) -> str:
    """Construit le rapport ultra lisible, groupé par site."""
    # Regroupement par site en conservant l'ordre d'apparition.
    sites: dict[str, list[ProductResult]] = {}
    for r in report.results:
        sites.setdefault(r.site or "Site inconnu", []).append(r)

    total = len(report.results)
    total_alerts = sum(
        1
        for r in report.results
        if r.error is None and (r.unavailable_sizes or (r.sold_out and not r.available_sizes))
    )

    header = [
        "🏍️ <b>RAPPORT DISPO MOTO</b>",
        f"📅 {_fr_date(report.generated_at)}",
        f"🌐 {len(sites)} site(s)   📦 {total} réf.   🔴 {total_alerts} alerte(s)",
    ]

    blocks = ["\n".join(header)]
    for site, results in sites.items():
        blocks.append(_site_section(site, results))

    return "\n\n".join(blocks)


def split_for_telegram(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    """Découpe le texte en morceaux <= limit, sans couper au milieu d'une ligne."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
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
