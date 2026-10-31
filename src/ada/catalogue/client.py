"""The sections and materials a scope may build with, read from ada studio's API.

    from ada.catalogue import scope_catalogue

    cat = scope_catalogue("project:<id>")     # ADA_VIEWER_API_URL + ADA_VIEWER_TOKEN, like the worker
    sec = cat.section("/SPEC/HEA200")          # ada.Section, metres, metadata["catalogue"]["ref"]
    mat = cat.material("/STEEL-A")             # ada.Material, override-merged properties
    pl = cat.plate("/PLATES/PL10")             # thickness + metadata for ada.Plate
    beam = ada.Beam("b1", n1, n2, sec=sec, mat=mat)

The catalogue is the scope's (``GET /api/scopes/{scope}/catalogue/sections`` and ``.../materials``):
what the reference data readable in that scope offers, merged across its collections, whoever
provided it. Read once, on first use, and kept on the object; ``refresh()`` reads it again.

A ref listed in two collections is ambiguous on its own: pass ``collection=`` to choose.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Optional
from urllib.parse import quote, urlencode

from .build import (
    CatalogueError,
    CataloguePlate,
    material_from_row,
    plate_from_row,
    section_from_row,
)

#: ``(url) -> parsed JSON``: how the client reads the API. Injectable, for tests and other transports.
Transport = Callable[[str], dict]

_PAGE = 5000
_TIMEOUT_S = 60.0
_ATTEMPTS = 3


def _urllib_transport(token: str) -> Transport:
    """Plain ``urllib``, bearer token, retried on a server or transport failure (a 4xx is not
    retried: it will be as wrong next time)."""
    import urllib.error
    import urllib.request

    def get(url: str) -> dict:
        last: Optional[Exception] = None
        for attempt in range(_ATTEMPTS):
            req = urllib.request.Request(url, method="GET")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Accept", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                    return json.loads(resp.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:500]
                except Exception:  # noqa: BLE001 - the status says enough
                    pass
                if exc.code < 500:
                    raise CatalogueError(f"the catalogue API refused the request ({exc.code}): {detail}") from exc
                last = CatalogueError(f"the catalogue API failed ({exc.code}): {detail}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = CatalogueError(f"the catalogue API is unreachable: {type(exc).__name__}: {exc}")
            if attempt + 1 < _ATTEMPTS:
                time.sleep(2**attempt)
        raise last if last is not None else CatalogueError("the catalogue API call failed")

    return get


class ScopeCatalogue:
    """One scope's catalogue. Build it with :func:`scope_catalogue`."""

    def __init__(self, scope: str, *, base_url: str, transport: Transport):
        self.scope = scope
        self._base = base_url.rstrip("/")
        self._get = transport
        self._sections: Optional[list[dict]] = None
        self._materials: Optional[list[dict]] = None

    # --- reading -------------------------------------------------------------------------------

    def _url(self, what: str, params: dict) -> str:
        return f"{self._base}/api/scopes/{quote(self.scope, safe=':')}/catalogue/{what}?{urlencode(params)}"

    def _read(self, what: str) -> list[dict]:
        rows: list[dict] = []
        offset: Optional[int] = 0
        while offset is not None:
            page = self._get(self._url(what, {"limit": _PAGE, "offset": offset}))
            rows.extend(page.get("items") or [])
            offset = page.get("next_offset")
        return rows

    def refresh(self) -> "ScopeCatalogue":
        """Read the catalogue again (an admin changed an override, a provider re-exported)."""
        self._sections = self._read("sections")
        self._materials = self._read("materials")
        return self

    def section_rows(
        self, *, type: str | None = None, use: str | None = None, collection: str | None = None
    ) -> list[dict]:
        """The section rows as the API sent them, filtered: ``type`` an adapy section type (or
        ``PLATE``), ``use`` ``section`` / ``plate``, ``collection``."""
        if self._sections is None:
            self._sections = self._read("sections")
        out = self._sections
        if type:
            out = [r for r in out if str((r.get("section") or {}).get("type") or "").upper() == type.upper()]
        if use:
            out = [r for r in out if r.get("use") == use]
        if collection:
            out = [r for r in out if r.get("collection") == collection.lower()]
        return list(out)

    def material_rows(self, *, collection: str | None = None) -> list[dict]:
        if self._materials is None:
            self._materials = self._read("materials")
        out = self._materials
        if collection:
            out = [r for r in out if r.get("collection") == collection.lower()]
        return list(out)

    # --- one entry -----------------------------------------------------------------------------

    @staticmethod
    def _one(rows: list[dict], ref: str, collection: str | None, what: str) -> dict:
        hits = [
            r for r in rows if r["ref"] == ref and (collection is None or r.get("collection") == collection.lower())
        ]
        if not hits:
            raise CatalogueError(f"{what} {ref!r} is not in this scope's catalogue")
        if len(hits) > 1:
            names = ", ".join(sorted(r.get("collection") or "?" for r in hits))
            raise CatalogueError(f"{what} {ref!r} is in several collections ({names}); pass collection=")
        return hits[0]

    def has_section(self, ref: str, collection: str | None = None) -> bool:
        return any(r["ref"] == ref and (collection is None or r.get("collection") == collection.lower())
                   for r in self.section_rows())  # fmt: skip

    def has_material(self, ref: str, collection: str | None = None) -> bool:
        return any(r["ref"] == ref and (collection is None or r.get("collection") == collection.lower())
                   for r in self.material_rows())  # fmt: skip

    def section(self, ref: str, *, collection: str | None = None, name: str | None = None):
        """The ``ada.Section`` of ``ref`` (a beam profile), dimensions in metres."""
        return section_from_row(self._one(self.section_rows(use="section"), ref, collection, "section"), name=name)

    def plate(self, ref: str, *, collection: str | None = None) -> CataloguePlate:
        """The plate spec ``ref``: its thickness (metres) and the metadata a ``Plate`` should carry."""
        return plate_from_row(self._one(self.section_rows(use="plate"), ref, collection, "plate spec"))

    def material(self, ref: str, *, collection: str | None = None, name: str | None = None, strict: bool = False):
        """The ``ada.Material`` of ``ref``, with an admin's overrides merged over the provider's values
        (see :func:`ada.catalogue.build.material_from_row` for defaults and ``strict``)."""
        return material_from_row(self._one(self.material_rows(), ref, collection, "material"), name=name, strict=strict)

    # --- listings ------------------------------------------------------------------------------

    def sections(self, *, type: str | None = None, collection: str | None = None) -> list:
        """Every beam section that builds (rows without dimensions are left out; see
        :meth:`section_rows` for all of them)."""
        out = []
        for row in self.section_rows(type=type, use="section", collection=collection):
            try:
                out.append(section_from_row(row))
            except CatalogueError:
                continue
        return out

    def materials(self, *, collection: str | None = None, strict: bool = False) -> list:
        out = []
        for row in self.material_rows(collection=collection):
            try:
                out.append(material_from_row(row, strict=strict))
            except CatalogueError:
                continue
        return out

    def tag_plate(self, plate: Any, ref: str, *, collection: str | None = None, tol: float = 1e-6) -> Any:
        """Mark an existing ``ada.Plate`` as made of plate spec ``ref`` (its metadata), refusing a plate
        whose thickness is not the spec's."""
        from ada.base.units import Units

        spec = self.plate(ref, collection=collection)
        t = float(plate.t) * (0.001 if getattr(plate, "units", Units.M) == Units.MM else 1.0)
        if abs(t - spec.t) > tol:
            raise CatalogueError(f"plate {plate.name} is {t} m thick; {ref} is {spec.t} m")
        plate.metadata.update(spec.metadata)
        return plate


def scope_catalogue(
    scope: str,
    *,
    url: str | None = None,
    token: str | None = None,
    transport: Transport | None = None,
) -> ScopeCatalogue:
    """The catalogue of ``scope`` (its wire name: ``shared``, ``project:<id>``).

    ``url`` / ``token`` default to ``ADA_VIEWER_API_URL`` / ``ADA_VIEWER_TOKEN`` -- the names the
    worker and every out-of-cluster client already read. ``transport`` replaces the HTTP call."""
    base = (url or os.environ.get("ADA_VIEWER_API_URL") or "").strip().rstrip("/")
    if not base:
        raise CatalogueError("no API URL: pass url= or set ADA_VIEWER_API_URL (e.g. https://studio.example/)")
    if transport is None:
        if not base.startswith(("http://", "https://")):
            raise CatalogueError(f"ADA_VIEWER_API_URL={base!r} is not an http(s) URL")
        transport = _urllib_transport((token or os.environ.get("ADA_VIEWER_TOKEN") or "").strip())
    return ScopeCatalogue(scope, base_url=base, transport=transport)
