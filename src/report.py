"""Rapport Telegram — pastilles de dispo par taille, organisé par gamme.

Pour chaque casque : statut, coloris (lien cliquable), prix, et une ligne de
pastilles 🟢/🔴 par taille. Les casques sont regroupés par site puis par gamme.
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


def _status(r: ProductResult) -> str:
    if r.error:
        return "error"
    if r.sold_out or (r.sizes and not any(s.available for s in r.sizes)):
        return "rupture"
    if r.unavailable_sizes:
        return "partial"
    return "ok"


_STATUS_ICON = {"ok": "🟢", "partial": "🟡", "rupture": "🔴", "error": "⚠️"}


def _pastilles(r: ProductResult) -> str:
    return " ".join(
        f"{'🟢' if s.available else '🔴'}{html.escape(s.size)}" for s in r.sizes
    )


def _helmet_block(r: ProductResult) -> str:
    icon = _STATUS_ICON[_status(r)]
    label = r.color or r.gamme or r.name
    title = f'{icon} <a href="{html.escape(r.url)}">{html.escape(label)}</a>'
    if r.price:
        title += f" · {html.escape(r.price)}"
    elif _status(r) == "rupture":
        title += " · rupture"
    pastilles = _pastilles(r)
    return f"{title}\n{pastilles}" if pastilles else title


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


def build_header_text(report: Report) -> str:
    """En-tête du rapport (titre, date, compteurs, légende) — 1er message."""
    results = report.results
    ok, partial, rupture, failed = _counts(results)
    brands = [b for b in {r.brand for r in results if r.brand}]
    brand_label = " / ".join(sorted(brands)).upper() if brands else "CASQUES"
    lines = [
        f"🏍️ <b>DISPO CASQUES {html.escape(brand_label)}</b>",
        f"📅 {_fr_date(report.generated_at)}",
        "",
        f"📦 {len(results)} casque(s) surveillé(s)",
        f"🟢 {len(ok)} complets · 🟡 {len(partial)} partiels · 🔴 {len(rupture)} ruptures"
        + (f" · ⚠️ {len(failed)} erreurs" if failed else ""),
        "<i>🟢 taille dispo · 🔴 taille indispo</i>",
    ]
    return "\n".join(lines)


def photo_caption(r: ProductResult) -> str:
    """Légende d'une photo de casque : statut, nom, prix, pastilles, lien."""
    icon = _STATUS_ICON[_status(r)]
    title_label = r.color or r.gamme or r.name
    if r.gamme and r.color:
        title_label = f"{r.gamme} — {r.color}"
    line = f'{icon} <a href="{html.escape(r.url)}">{html.escape(title_label)}</a>'
    if r.price:
        line += f" · {html.escape(r.price)}"
    elif _status(r) == "rupture":
        line += " · rupture"
    pastilles = _pastilles(r)
    return f"{line}\n{pastilles}" if pastilles else line


def iter_sections(report: Report):
    """Itère (label_gamme, [casques]) groupés par site puis gamme (ordre conservé)."""
    by_site = _group_in_order(report.results, lambda r: r.site or "Site inconnu")
    multi = len(by_site) > 1
    for site, site_results in by_site.items():
        by_gamme = _group_in_order(site_results, lambda r: r.gamme or "Autres")
        for gamme, helmets in by_gamme.items():
            label = f"{site} · {gamme}" if multi else gamme
            yield label, helmets


def build_report_text(report: Report) -> str:
    results = report.results
    ok = [r for r in results if _status(r) == "ok"]
    partial = [r for r in results if _status(r) == "partial"]
    rupture = [r for r in results if _status(r) == "rupture"]
    failed = [r for r in results if _status(r) == "error"]

    brands = [b for b in {r.brand for r in results if r.brand}]
    brand_label = " / ".join(sorted(brands)).upper() if brands else "CASQUES"

    header = [
        f"🏍️ <b>DISPO CASQUES {html.escape(brand_label)}</b>",
        f"📅 {_fr_date(report.generated_at)}",
        "",
        f"📦 {len(results)} casque(s) surveillé(s)",
        f"🟢 {len(ok)} complets · 🟡 {len(partial)} partiels · 🔴 {len(rupture)} ruptures"
        + (f" · ⚠️ {len(failed)} erreurs" if failed else ""),
        "<i>🟢 taille dispo · 🔴 taille indispo</i>",
    ]

    blocks = ["\n".join(header)]

    # Regroupement par site puis par gamme (ordre d'apparition conservé).
    by_site = _group_in_order(results, lambda r: r.site or "Site inconnu")
    for site, site_results in by_site.items():
        if len(by_site) > 1:
            blocks.append(f"🌐 <b>{html.escape(site)}</b>")
        by_gamme = _group_in_order(site_results, lambda r: r.gamme or "Autres")
        for gamme, helmets in by_gamme.items():
            section = [f"<b>━━━ {html.escape(gamme)} ━━━</b>"]
            section += [_helmet_block(r) for r in helmets if r.error is None]
            errors = [r for r in helmets if r.error is not None]
            for r in errors:
                section.append(f"⚠️ <a href=\"{html.escape(r.url)}\">{html.escape(r.name)}</a> — erreur")
            blocks.append("\n\n".join(section))

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
