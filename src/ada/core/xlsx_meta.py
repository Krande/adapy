"""The engine-agnostic ``_ADA_META`` workbook sheet: key names + a stdlib-only
reader.

Every EXPORTED procedural workbook carries this sheet so an IMPORT can auto-
detect which engine owns the file's format. The writer lives with the engines
(``ada.topo_model.excel_io.write_ada_meta_sheet``, openpyxl); the reader here
uses only ``zipfile`` + ``ElementTree`` so the slim API (no openpyxl, no
modelling stack) can sniff an upload. Both sides share the constants below,
which is why they live in ``ada.core`` rather than with either consumer.
"""

from __future__ import annotations

# The dedicated, engine-agnostic metadata sheet every EXPORTED workbook carries,
# so an IMPORT auto-detects which engine owns the file's format (and can warn on
# a schema/package drift). A hand-made / legacy workbook has no such sheet — the
# frontend then PROMPTS the user to pick an engine. Vertical key/value layout
# (column A = key, column B = value), no header row.
ADA_META_SHEET = "_ADA_META"
# Version of the _ADA_META sheet FORMAT itself (independent of the doc schema /
# the engine package version). Bump on an incompatible layout change.
ADA_META_VERSION = "1"
# Stable, versioned key names (column A). Keep stable across releases.
ADA_META_KEY_META_VERSION = "ada_meta_version"
ADA_META_KEY_ENGINE = "engine"
ADA_META_KEY_PACKAGE = "package"
ADA_META_KEY_PACKAGE_VERSION = "package_version"
ADA_META_KEY_SCHEMA_VERSION = "schema_version"
ADA_META_KEY_EXPORTED_AT = "exported_at"


def _xlsx_cell_text(cell, shared: list[str], local) -> str | None:
    """Resolve one worksheet ``<c>`` element's text: an ``inlineStr``, a shared-
    string index (``t="s"``), or a bare ``<v>`` value."""
    t = cell.get("t")
    if t == "inlineStr":
        return "".join(x.text or "" for x in cell.iter() if local(x.tag) == "t") or None
    v = None
    for ch in cell:
        if local(ch.tag) == "v":
            v = ch.text
            break
    if v is None:
        return None
    if t == "s":
        try:
            return shared[int(v)]
        except (ValueError, IndexError):
            return None
    return v


def read_ada_meta_from_xlsx_bytes(data: bytes) -> dict | None:
    """Read the ``_ADA_META`` sheet from xlsx BYTES using ONLY the standard library
    (zipfile + ElementTree), so the slim API (which has no openpyxl / ada) can
    auto-detect the exporting engine on import.

    Returns a ``{key: value}`` map (str -> str) for the sheet's key/value rows, or
    ``None`` when the workbook has no ``_ADA_META`` sheet (a hand-made / legacy
    file — the caller then prompts for an engine). Never raises on a malformed
    upload: any parse error resolves to ``None``."""
    import io
    import posixpath
    import xml.etree.ElementTree as ET
    import zipfile

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "xl/workbook.xml" not in names:
                return None
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            rid = None
            for sheet in wb.iter():
                if _local(sheet.tag) != "sheet" or (sheet.get("name") or "").strip() != ADA_META_SHEET:
                    continue
                for k, v in sheet.attrib.items():
                    if _local(k) == "id":
                        rid = v
                        break
                break
            if rid is None:
                return None
            target = None
            if "xl/_rels/workbook.xml.rels" in names:
                rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                for rel in rels.iter():
                    if _local(rel.tag) == "Relationship" and rel.get("Id") == rid:
                        target = rel.get("Target")
                        break
            if not target:
                return None
            tpath = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            if tpath not in names:
                return None
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sst:
                    if _local(si.tag) != "si":
                        continue
                    shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
            ws = ET.fromstring(zf.read(tpath))
            out: dict[str, str] = {}
            for row in ws.iter():
                if _local(row.tag) != "row":
                    continue
                key = val = None
                for c in row:
                    if _local(c.tag) != "c":
                        continue
                    col = "".join(ch for ch in (c.get("r") or "") if ch.isalpha())
                    text = _xlsx_cell_text(c, shared, _local)
                    if col == "A":
                        key = text
                    elif col == "B":
                        val = text
                if key and str(key).strip():
                    out[str(key).strip()] = val.strip() if isinstance(val, str) else val
            return out or None
    except Exception:
        return None
