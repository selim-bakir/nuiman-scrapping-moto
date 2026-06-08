"""Export du rapport quotidien au format Excel (.xlsx)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import Report, ProductResult
from .report import _status, fmt_restock

SIZE_ORDER = ["3XS", "2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"]

_FILL_GREEN = PatternFill("solid", fgColor="C6EFCE")
_FILL_RED = PatternFill("solid", fgColor="FFC7CE")
_FILL_ORANGE = PatternFill("solid", fgColor="FFEB9C")
_FILL_GREY = PatternFill("solid", fgColor="F2F2F2")
_FILL_HEADER = PatternFill("solid", fgColor="305496")
_STATUS_LABEL = {"ok": "Complet", "partial": "Partiel", "rupture": "Rupture", "error": "Erreur"}
_STATUS_RANK = {"rupture": 0, "partial": 1, "ok": 2, "error": 3}


def _ordered_sizes(results) -> list[str]:
    seen = {s.size for r in results for s in r.sizes}
    known = [s for s in SIZE_ORDER if s in seen]
    extra = sorted(seen - set(known))
    return known + extra


def build_xlsx(report: Report, path: Path) -> Path:
    """Génère un classeur : 1 ligne par casque, 1 colonne par taille (ruptures en tête)."""
    results = sorted(report.results, key=lambda r: (_STATUS_RANK.get(_status(r), 9), r.gamme or "", r.color or ""))
    sizes = _ordered_sizes(report.results)

    wb = Workbook()
    ws = wb.active
    ws.title = "Dispo casques"

    headers = ["Site", "Marque", "Gamme", "Coloris", "Prix", "Statut", *sizes, "Lien"]
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=col)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _FILL_HEADER
        c.alignment = Alignment(horizontal="center", vertical="center")

    for r in results:
        st = _status(r)
        size_map = {s.size: s for s in r.sizes}
        row = [
            r.site or "",
            r.brand or "",
            r.gamme or "",
            r.color or "",
            r.price or "",
            _STATUS_LABEL.get(st, st),
        ]
        for sz in sizes:
            s = size_map.get(sz)
            if s is None:
                row.append("—")
            elif s.available:
                row.append("Dispo")
            elif s.restock:
                row.append(f"Réappro {fmt_restock(s.restock)}")
            else:
                row.append("Rupture")
        row.append(r.url)
        ws.append(row)

        # Coloration des cellules de taille.
        r_idx = ws.max_row
        base = 7  # 1re colonne de taille (après les 6 premières)
        for i, sz in enumerate(sizes):
            cell = ws.cell(row=r_idx, column=base + i)
            s = size_map.get(sz)
            if s is None:
                cell.fill = _FILL_GREY
            elif s.available:
                cell.fill = _FILL_GREEN
            elif s.restock:
                cell.fill = _FILL_ORANGE
            else:
                cell.fill = _FILL_RED
            cell.alignment = Alignment(horizontal="center")

    # Largeurs de colonnes.
    widths = [12, 10, 16, 22, 10, 9] + [13] * len(sizes) + [60]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
