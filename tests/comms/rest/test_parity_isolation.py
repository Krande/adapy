"""Parity validation runs in the memory-capped fork, not the worker process.

Re-deriving a source to ifc/xml/step and reloading each can spike RAM (or trip
a native CAD crash). The worker runs parity through ``run_isolated_convert`` so
an OOM / SIGABRT dies in the child — the cell fails — instead of taking the pod
down. This pins that the child wrapper produces a valid JSON parity result the
parent can read back.
"""

from __future__ import annotations

import asyncio
import json

from ada.comms.rest.converter import ConverterRegistry
from ada.comms.rest.subprocess_convert import run_isolated_convert
from ada.comms.rest.worker import _parity_child


def test_parity_child_runs_in_isolated_fork(fem_files):
    src = fem_files / "sesam/single_beam.xml"
    formats = [t for t in ("ifc", "xml", "step") if t in ConverterRegistry.targets_for(".xml")]
    assert formats, "expected .xml to have structure-preserving targets for parity"

    result = asyncio.run(
        run_isolated_convert(
            _parity_child,
            src,
            str(src),
            "parity",
            convert_kwargs={"formats": formats},
            timeout_s=300,
        )
    )

    # The whole check ran in the forked child and came back cleanly.
    assert result.exit_code == 0, f"signal={result.signal_name} error={result.error}"
    assert result.out_path is not None
    payload = json.loads(result.out_path.read_text())
    result.cleanup_output()

    assert payload["consistent"] is True
    assert payload["counts"]["source"] >= 1
    # Every derived format reloaded to the same element count as the source.
    for fmt in formats:
        assert payload["counts"].get(fmt) == payload["expected"]
    assert payload["summary"].startswith("[OK]")
