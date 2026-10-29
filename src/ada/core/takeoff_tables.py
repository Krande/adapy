"""The take-off tables, as an xlsx workbook or per-tab CSV, from a stored ``stats`` dict.

Stdlib plus a lazy ``openpyxl``: the slim API image serves the take-off download from the stored
``.stats.json`` and ships this module, but not ``ada.topo_model`` (which builds the stats and needs the
modelling stack). ``ada.topo_model.takeoff`` re-exports these names."""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Exports: whole-model xlsx workbook + per-tab CSV                             #
# --------------------------------------------------------------------------- #
# The take-off tables are heterogeneous (a different column set per discipline,
# several tables on one discipline sheet), so the workbook is authored directly
# with openpyxl — the same library ``ada.serialize.xlsx`` builds on — rather than
# through the model-class-keyed WorkbookSerializer (one sheet per registered
# pydantic type), which does not fit multi-table sheets. The result matches the
# viewer's six tabs one-for-one: Overview, COGs, Structural, Piping, HVAC,
# Electrical.

# (label, stats-path, columns) table specs. ``path`` walks the stats dict.
_TAB_TABLES: dict[str, list[tuple[str, tuple[str, ...], list[tuple[str, str]]]]] = {
    "overview": [
        ("Mass by discipline", ("disciplines",), [("name", "Discipline"), ("mass", "Mass (t)"), ("count", "Objects")]),
    ],
    "cogs": [
        (
            "COG by discipline",
            ("disciplines",),
            [("name", "Discipline"), ("mass", "Mass (t)"), ("cog.0", "X (m)"), ("cog.1", "Y (m)"), ("cog.2", "Z (m)")],
        ),
        (
            "Major items",
            ("major_items",),
            [
                ("name", "Item"),
                ("discipline", "Discipline"),
                ("mass", "Mass (t)"),
                ("cog.0", "X (m)"),
                ("cog.1", "Y (m)"),
                ("cog.2", "Z (m)"),
            ],
        ),
    ],
    "structural": [
        (
            "Beams by section",
            ("structural", "beams"),
            [("section", "Section"), ("count", "Count"), ("length", "Length (m)"), ("mass", "Mass (t)")],
        ),
        (
            "Plates by thickness",
            ("structural", "plates"),
            [("label", "Plate"), ("count", "Count"), ("area", "Area (m²)"), ("mass", "Mass (t)")],
        ),
    ],
    "piping": [
        (
            "Segments by size",
            ("piping", "segments"),
            [("size", "Size"), ("segments", "Segments"), ("length", "Length (m)"), ("mass", "Mass (t)")],
        ),
        ("Fittings", ("piping", "fittings"), [("name", "Fitting"), ("count", "Count")]),
    ],
    "hvac": [
        (
            "Duct segments by size",
            ("hvac", "segments"),
            [
                ("size", "Size"),
                ("segments", "Segments"),
                ("length", "Length (m)"),
                ("area", "Area (m²)"),
                ("mass", "Mass (t)"),
            ],
        ),
        ("Fittings", ("hvac", "fittings"), [("name", "Fitting"), ("count", "Count")]),
    ],
    "electrical": [
        (
            "Cable tray by width",
            ("electrical", "trays"),
            [("size", "Width"), ("segments", "Segments"), ("length", "Length (m)"), ("mass", "Mass (t)")],
        ),
        ("Cables by type", ("electrical", "cables"), [("type", "Type"), ("length", "Length (m)")]),
    ],
    "joints": [
        (
            "Joints by type",
            ("joints", "by_type"),
            [("name", "Type"), ("count", "Count")],
        ),
        (
            "Joints",
            ("joints", "items"),
            [
                ("name", "Name"),
                ("type", "Type"),
                ("plates", "Plates"),
                ("welds", "Welds"),
                ("centre.0", "X (m)"),
                ("centre.1", "Y (m)"),
                ("centre.2", "Z (m)"),
            ],
        ),
    ],
}

_TAB_SHEET_NAMES: dict[str, str] = {
    "overview": "Overview",
    "cogs": "COGs",
    "structural": "Structural",
    "piping": "Piping",
    "hvac": "HVAC",
    "electrical": "Electrical",
    "joints": "Joints",
}
_EXPORT_TABS: tuple[str, ...] = ("overview", "cogs", "structural", "piping", "hvac", "electrical", "joints")


def _dig(row: dict, path: str):
    """Resolve a dotted column key against a row (``"cog.0"`` -> ``row['cog'][0]``)."""
    cur = row
    for part in path.split("."):
        if isinstance(cur, (list, tuple)):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _rows_at(stats: dict, path: tuple[str, ...]) -> list[dict]:
    cur = stats
    for p in path:
        cur = cur.get(p, {}) if isinstance(cur, dict) else {}
    return cur if isinstance(cur, list) else []


def takeoff_to_xlsx_bytes(stats: dict) -> bytes:
    """Serialize a take-off ``stats`` dict to a whole-model Excel workbook (bytes):
    one sheet per discipline plus COGs and Overview, matching the viewer tabs."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)
    bold = Font(bold=True)

    for tab in _EXPORT_TABS:
        ws = wb.create_sheet(_TAB_SHEET_NAMES[tab])
        r = 1
        if tab == "overview":
            ws.cell(r, 1, "Metric").font = bold
            ws.cell(r, 2, "Value").font = bold
            r += 1
            for label, val in (
                ("Total mass (t)", stats.get("total_mass")),
                ("Objects", stats.get("objects")),
                ("Disciplines", len([d for d in stats.get("disciplines", []) if d.get("count")])),
                ("Bounding box (m)", " × ".join(str(v) for v in stats.get("bbox", []))),
                ("COG X (m)", (stats.get("total_cog") or [None])[0]),
                ("COG Y (m)", (stats.get("total_cog") or [None, None])[1]),
                ("COG Z (m)", (stats.get("total_cog") or [None, None, None])[2]),
            ):
                ws.cell(r, 1, label)
                ws.cell(r, 2, val)
                r += 1
            r += 1
        for title, path, cols in _TAB_TABLES[tab]:
            ws.cell(r, 1, title).font = bold
            r += 1
            for ci, (_key, header) in enumerate(cols, start=1):
                ws.cell(r, ci, header).font = bold
            r += 1
            for row in _rows_at(stats, path):
                for ci, (key, _header) in enumerate(cols, start=1):
                    ws.cell(r, ci, _dig(row, key))
                r += 1
            r += 1  # blank spacer between tables

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def takeoff_to_csv(stats: dict, tab: str) -> str:
    """Serialize the tables of one viewer tab to CSV text (the active-tab export)."""
    import csv
    import io

    tab = tab if tab in _TAB_TABLES else "overview"
    buf = io.StringIO()
    writer = csv.writer(buf)
    for title, path, cols in _TAB_TABLES[tab]:
        writer.writerow([title])
        writer.writerow([header for _key, header in cols])
        for row in _rows_at(stats, path):
            writer.writerow([_dig(row, key) for key, _header in cols])
        writer.writerow([])
    return buf.getvalue()
