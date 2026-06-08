"""Rapport Telegram — casques en rupture, pastilles par taille + date de réappro.

On ne liste QUE les casques ayant au moins une taille indisponible. Pour chaque
casque : statut, coloris (lien), prix, pastilles 🟢/🔴 par taille, et la date de
réapprovisionnement prévue des tailles indispo (si connue). Groupé par gamme.
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


def fmt_restock(iso: str | None) -> str | None:
    """'2026-06-17T00:00:00+02:00' -> '17/06/26'."""
    if not iso or len(iso) < 10:
        return None
    y, m, d = iso[:4], iso[5:7], iso[8:10]
    return f"{d}/{m}/{y[2:]}"


def _status(r: ProductResult) -> str:
    if r.error:
        return "error"
    if r.sold_out or (r.sizes and not any(s.available for s in r.sizes)):
        return "rupture"
    if r.unavailable_sizes:
        return "partial"
    return "ok"


_STATUS_ICON = {"ok": "🟢", "partial": "🟡", "rupture": "🔴", "error": "⚠️"}


def _unavail_lines(r: ProductResult) -> list[str]:
    """Une ligne par taille INDISPO : taille + réappro + croisement Dafy."""
    lines = []
    for s in r.sizes:
        if s.available:
            continue
        d = fmt_restock(s.restock)
        date = f"réappro {d}" if d else "réappro non communiquée"
        line = f"🔴 <b>{html.escape(s.size)}</b> · {date}"
        if s.dafy_available is True:
            line += " · ✅ dispo sur Dafy"
        elif s.dafy_available is False:
            line += " · 🟥 confirmé Dafy"
        lines.append(line)
    return lines


def _group_in_order(items, key):
    groups: dict = {}
    for it in items:
        groups.setdefault(key(it), []).append(it)
    return groups


def _counts(results):
    ok = [r for r in results if _status(r) == "ok"]
    partial = [r for r in results if _status(r) == "partial"]
    rupture = [r for r in results if _status(r) == "rupture"]
    failed = [r for r in results if _status(r) == "error"]
    return ok, partial, rupture, failed


def to_report(results) -> list[ProductResult]:
    """Casques à signaler = au moins une taille indispo (partiel ou rupture totale)."""
    return [r for r in results if _status(r) in ("partial", "rupture")]


def build_header_text(report: Report) -> str:
    """En-tête du rapport (titre, date, compteurs) — 1er message."""
    results = report.results
    ok, partial, rupture, failed = _counts(results)
    brands = [b for b in {r.brand for r in results if r.brand}]
    brand_label = " / ".join(sorted(brands)).upper() if brands else "CASQUES"
    lines = [
        f"🏍️ <b>RUPTURES CASQUES {html.escape(brand_label)}</b>",
        f"📅 {_fr_date(report.generated_at)}",
        "",
        f"📦 {len(results)} casque(s) surveillé(s) · 🚨 {len(partial) + len(rupture)} à signaler",
        f"🟢 {len(ok)} complets · 🟡 {len(partial)} partiels · 🔴 {len(rupture)} ruptures totales"
        + (f" · ⚠️ {len(failed)} erreurs" if failed else ""),
    ]
    confirmed = sum(
        1 for r in results for s in r.sizes if not s.available and s.dafy_available is False
    )
    elsewhere = sum(
        1 for r in results for s in r.sizes if not s.available and s.dafy_available is True
    )
    if confirmed or elsewhere:
        lines.append(f"🔁 Dafy : 🟥 {confirmed} ruptures confirmées · ✅ {elsewhere} dispo ailleurs")
    lines.append("<i>🔴 indispo · réappro = réassort prévu · ✅/🟥 = croisement Dafy</i>")
    return "\n".join(lines)


def photo_caption(r: ProductResult) -> str:
    """Légende d'une photo : statut, nom, prix, puis les tailles indispo + réappro."""
    icon = _STATUS_ICON[_status(r)]
    title_label = f"{r.gamme} — {r.color}" if (r.gamme and r.color) else (r.color or r.gamme or r.name)
    line = f'{icon} <a href="{html.escape(r.url)}">{html.escape(title_label)}</a>'
    if r.price:
        line += f" · {html.escape(r.price)}"
    return "\n".join([line, *_unavail_lines(r)])


def iter_sections(report: Report):
    """Itère (label_gamme, [casques à signaler]) groupés par site puis gamme."""
    flagged = to_report(report.results)
    by_site = _group_in_order(flagged, lambda r: r.site or "Site inconnu")
    multi = len(by_site) > 1
    for site, site_results in by_site.items():
        by_gamme = _group_in_order(site_results, lambda r: r.gamme or "Autres")
        for gamme, helmets in by_gamme.items():
            label = f"{site} · {gamme}" if multi else gamme
            yield label, helmets


def build_report_text(report: Report) -> str:
    """Rapport texte complet (en-tête + casques à signaler par gamme)."""
    blocks = [build_header_text(report)]
    has_any = False
    for label, helmets in iter_sections(report):
        has_any = True
        section = [f"<b>━━━ {html.escape(label)} ━━━</b>"]
        section += [photo_caption(r) for r in helmets]
        blocks.append("\n\n".join(section))
    if not has_any:
        blocks.append("✅ Aucune rupture : tous les casques sont dispo dans toutes les tailles.")
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
