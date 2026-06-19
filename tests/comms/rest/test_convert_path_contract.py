"""End-to-end of the path-return upload contract.

The disk-writing exporters (IFC / Genie XML / STEP) hand ``convert`` a
``pathlib.Path`` so the worker can stream the file to object storage via
``Storage.put_path`` without ever holding it as a parent-side ``bytes``
buffer. GLB / mesh handlers still return bytes (they tessellate into a
BytesIO). These tests pin both halves and the full
convert→fork→put_path→read-back round-trip the worker runs.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest
from obstore.store import LocalStore

from ada.comms.rest.converter import convert, result_bytes
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage
from ada.comms.rest.subprocess_convert import run_isolated_convert


def _fem(fem_files: pathlib.Path) -> pathlib.Path:
    return fem_files / "sesam/beamMassT1.FEM"


def test_convert_returns_path_for_disk_targets(fem_files):
    # Genie XML is a streaming, disk-writing exporter — convert hands back the
    # path of the file it wrote, not bytes.
    src = _fem(fem_files)
    result = convert(src, str(src), "xml")
    try:
        assert isinstance(result, pathlib.Path)
        assert result.exists()
        assert b"<" in result.read_bytes()  # looks like XML
    finally:
        if isinstance(result, pathlib.Path):
            result.unlink(missing_ok=True)


def test_convert_returns_bytes_for_glb(fem_files):
    # GLB tessellates into a BytesIO; the in-RAM bytes contract is unchanged.
    src = _fem(fem_files)
    result = convert(src, str(src), "glb")
    assert isinstance(result, (bytes, bytearray))
    assert len(result) > 0


def test_result_bytes_normalises_both(fem_files):
    src = _fem(fem_files)
    as_path = convert(src, str(src), "xml")
    as_bytes = convert(src, str(src), "glb")
    try:
        assert isinstance(result_bytes(as_path), bytes)
        assert result_bytes(as_bytes) is as_bytes or result_bytes(as_bytes) == bytes(as_bytes)
    finally:
        if isinstance(as_path, pathlib.Path):
            as_path.unlink(missing_ok=True)


def test_full_chain_fork_to_put_path(tmp_path, fem_files):
    # Mirror what the worker does: run convert in the forked child, take the
    # returned out_path, stream it to storage with put_path, and read it back.
    src = _fem(fem_files)
    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = Scope.shared()

    async def drive() -> bytes:
        iresult = await run_isolated_convert(
            convert,
            src_path=src,
            source_key=str(src),
            target_format="xml",
        )
        assert iresult.exit_code == 0
        assert iresult.out_path is not None and iresult.out_path.exists()
        try:
            await storage.put_path(
                scope, "_derived/beam.xml", iresult.out_path, content_encoding="gzip"
            )
        finally:
            iresult.cleanup_output()
        # The work dir + output file are gone once cleanup_output ran.
        assert iresult.out_path is None
        return await storage.get_bytes(scope, "_derived/beam.xml")

    data = asyncio.run(drive())
    assert b"<" in data  # gzip stored, transparently inflated on read


def test_full_chain_glb_bytes_path(tmp_path, fem_files):
    # The bytes branch still round-trips: child writes the bytes into the
    # result slot, parent streams that file up via put_path.
    src = _fem(fem_files)
    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = Scope.shared()

    async def drive() -> bytes:
        iresult = await run_isolated_convert(
            convert, src_path=src, source_key=str(src), target_format="glb"
        )
        assert iresult.exit_code == 0
        assert iresult.out_path is not None
        try:
            await storage.put_path(scope, "_derived/beam.glb", iresult.out_path)
        finally:
            iresult.cleanup_output()
        return await storage.get_bytes(scope, "_derived/beam.glb")

    data = asyncio.run(drive())
    assert data[:4] == b"glTF"  # GLB magic
