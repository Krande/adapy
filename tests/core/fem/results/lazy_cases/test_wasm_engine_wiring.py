"""The browser engine materialises the same cases as :mod:`combine`, byte for byte.

The viewer can materialise a load combination itself, with the adacpp_fea wasm
kernel over base strides cached in the browser (services/fea/feaEngineCore.ts).
The kernel's arithmetic is held equal to numpy in adacpp; what this test holds
is the WIRING on the browser side -- which stride each term reads, which
coefficient it is scaled by, how derived components (and those derived from
another field) map onto kernel ops, the per-element-type blobs, the overlay's
ranges -- by running the real module under node over these decks' base bakes
and comparing every file with what :func:`materialise_case` and
:func:`write_envelope` write.

Skipped when node, the frontend's node_modules or the built module are missing.
The module is looked up at ``ADACPP_FEA_WASM`` (a path to ``adacpp_fea.js``),
then at ``src/frontend/public/wasm/adacpp_fea.js`` (where the dev server serves
it from; see ``src/frontend/scripts/install-fea-wasm.mjs``).
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time

import pytest

from ada.fem.results.artefacts.combine import (
    case_dir_name,
    local_stride_fetcher,
    materialise_case,
    write_envelope,
)

REPO = pathlib.Path(__file__).resolve().parents[5]
FRONTEND = REPO / "src" / "frontend"
TEST_FILE = "src/__tests__/services/feaEngineWasm.test.ts"


def _module_path() -> pathlib.Path | None:
    env = os.environ.get("ADACPP_FEA_WASM")
    for candidate in ([pathlib.Path(env)] if env else []) + [FRONTEND / "public" / "wasm" / "adacpp_fea.js"]:
        if candidate.is_file() and candidate.with_suffix(".wasm").is_file():
            return candidate
    return None


def _envelope_dir(field: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in field)


def _write_reference(baked, root: pathlib.Path, name: str) -> dict:
    expected = root / name / "expected"
    envelopes = root / name / "envelopes"
    fetch = local_stride_fetcher(baked.base_dir)
    numpy_ms = {}
    for entry in baked.base["combination_steps"]:
        if entry["needs_raw"]:
            continue
        t0 = time.perf_counter()
        materialise_case(baked.base, fetch, entry["n"], out_dir=expected / case_dir_name(entry))
        numpy_ms[str(entry["n"])] = (time.perf_counter() - t0) * 1000.0
    fields = [
        f["name_canonical"]
        for f in baked.base["fields"]
        if f["name_canonical"] in ("sesam.nodes.displacement", "sesam.elements.p_stress")
    ]
    for field in fields:
        write_envelope(baked.base, fetch, field, envelopes / _envelope_dir(field))
    return {
        "name": name,
        "base_dir": str(baked.base_dir),
        "expected_dir": str(expected),
        "envelope_dir": str(envelopes),
        "envelope_fields": fields,
        "numpy_ms": numpy_ms,
    }


def test_the_wasm_engine_writes_the_numpy_cases(fixture_deck, synthetic_deck, tmp_path):
    node = shutil.which("node")
    module = _module_path()
    if node is None:
        pytest.skip("node is not installed")
    if not (FRONTEND / "node_modules" / "tsx").is_dir():
        pytest.skip("src/frontend/node_modules is missing (npm ci)")
    if module is None:
        pytest.skip("adacpp_fea.js is not built / installed (ADACPP_FEA_WASM)")

    decks = [
        _write_reference(fixture_deck, tmp_path, "fixture"),
        _write_reference(synthetic_deck, tmp_path, "synthetic"),
    ]
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"decks": decks}), encoding="utf-8")

    env = {**os.environ, "FEA_WASM_FIXTURE": str(fixture), "ADACPP_FEA_WASM": str(module)}
    proc = subprocess.run(
        [node, "--import", "tsx", "--test", "--test-reporter=tap", TEST_FILE],
        cwd=FRONTEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = proc.stdout + proc.stderr
    print(out)
    for deck in decks:
        total = sum(deck["numpy_ms"].values())
        print(f"[{deck['name']}] numpy materialise_case: {total:.1f} ms over {len(deck['numpy_ms'])} cases")
    assert proc.returncode == 0, out
    assert "# pass 2" in out and "# skipped 0" in out, out
