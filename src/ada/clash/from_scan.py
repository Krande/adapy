"""Run a clash check from a MEMBER SCAN rather than from a model -- the browser's way in.

WHY THIS EXISTS. A clash check needs members, not geometry, and `adacpp`'s wasm build can state
the members of an IFC in the browser (embind `scanMembers` -> JSONL, no server, no tessellation,
no ifcopenshell). What it cannot do is decide which contacts are joints, what type each one is,
and who could detail it: those are rules, they live in `ada.clash`, and they are the part nobody
should write twice. A second implementation in TypeScript would pass its own tests and quietly
disagree with the server's answer about a real model -- worse than not offering the feature.

So the browser runs THIS, in pyodide, on the scan the wasm module just wrote. Same identify, same
classify, same match, same `ada.clash/result@1` document the REST route returns, parsed by the
same code in the panel. The browser saves the upload and the job; it does not get its own rules.

WHAT DIFFERS IN A BROWSER, HONESTLY. `identify_joints` runs three passes and the two plate passes
reach a CAD backend for distances. Where pyodide has no such backend those passes report a warning
and leave their joints uncounted -- which the result document already carries and the panel already
shows, because "no plate joints" and "plate joints were never looked for" have always been
different answers here. The beam-to-beam pass needs no backend and always runs.
"""

from __future__ import annotations

import pathlib
from typing import Any, Iterable

from ada.clash.options import ClashOptions

__all__ = ["clash_check_from_members", "clash_check_from_scan"]


def clash_check_from_members(
    members: Iterable[dict],
    *,
    source_key: str,
    name: str = "ifc_members",
    options: ClashOptions | None = None,
    source_sha256: str | None = None,
) -> dict:
    """Identify, classify, match and group from member RECORDS; return the result document.

    A dict rather than a `ClashResult` because every caller of this one is across a boundary --
    postMessage out of a pyodide worker, a JSON body, a cached artifact -- and the document is
    the thing those carry. A caller that wants the object can still use `run_clash_check`.
    """
    from ada.cadit.ifc.read.native_members import members_to_part
    from ada.clash.identify import run_clash_check

    part = members_to_part(members, name=name)
    result = run_clash_check(
        part,
        source_key=source_key,
        options=options or ClashOptions(),
        source_sha256=source_sha256,
        # Where the answer came from, carried in the document itself. A joint list is only as
        # trustworthy as the read behind it, and "this was scanned natively, in a browser" is
        # something a reader of the result -- or of a bug report quoting it -- needs to know
        # without having to guess from which route returned it.
        provenance={"reader": "native-member-scan"},
    )
    return result.to_dict()


def clash_check_from_scan(
    jsonl: str | pathlib.Path,
    *,
    source_key: str,
    options: ClashOptions | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """The same, reading the scan from a JSONL file (`adacpp.ifc_members/1`).

    This is the one the pyodide worker calls: the wasm module has just written the scan to a path
    both runtimes can see, and neither the members nor the model are ever held whole on the way
    through.
    """
    from ada.cadit.ifc.read.native_members import members_from_jsonl

    return clash_check_from_members(
        members_from_jsonl(jsonl),
        source_key=source_key,
        name=pathlib.Path(jsonl).stem or "ifc_members",
        options=options,
        source_sha256=source_sha256,
    )
