"""The process that holds a connection library for ``ada dev connections``.

``python -m ada.api.connections.devhost PATH`` loads the library ``PATH`` (an
``ada_connections.toml`` or its folder) and answers JSON-line requests on stdin, one reply line per
request on stdout. The library's own code runs here, not in the viewer: a builder that hangs or
crashes takes this process down and the viewer starts another.

On start it writes one ``{"event": "ready", ...}`` line (the load result and the specs). Requests:

* ``{"id": 1, "op": "specs"}`` → ``{"id": 1, "ok": true, "generation": n, "specs": [...]}``
* ``{"id": 2, "op": "build", "spec_name": ..., "inputs": {...}, "glb_path": ...}``
  → ``{"id": 2, "ok": true, "inputs": {...}, "log": [...], "counts": {...}}``
* ``{"id": 4, "op": "subject", "spec_name": ..., "inputs": {...}, "path": ...}`` writes the detail's weld
  assembly (a simulation subject) to ``path`` → ``{"ok": true, "inputs": {...}, "welds": n}``; a detail
  without welds fails with ``ValueError: the detail has no welds``
* ``{"id": 3, "op": "reload"}`` → ``{"id": 3, "ok": true, "load": {...}, "specs": [...]}``

A failed request answers ``{"id": n, "ok": false, "error": ..., "traceback": ..., "declined": bool}``;
``declined`` marks a builder's ``NotApplicable`` (it refused these members) apart from a crash, and
the traceback starts at the library's own code.

The protocol owns the real stdout; ``print`` in library code (and anything a native library
writes to fd 1) goes to stderr instead, so it cannot corrupt a reply.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import traceback
from dataclasses import asdict
from typing import Any, TextIO


def _protocol_stream() -> TextIO:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return out


def _send(out: TextIO, msg: dict[str, Any]) -> None:
    out.write(json.dumps(msg, default=str) + "\n")
    out.flush()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m ada.api.connections.devhost PATH", file=sys.stderr)
        return 2
    out = _protocol_stream()

    from ada.api.connections.library import (
        Library,
        LibraryConfigError,
        format_user_traceback,
        is_declined,
        load_config,
    )

    try:
        library = Library(load_config(argv[0]))
    except LibraryConfigError as e:
        _send(out, {"event": "ready", "ok": False, "error": str(e), "traceback": None, "specs": []})
        return 1

    def _specs() -> list[dict[str, Any]]:
        try:
            return library.specs()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return []

    load = library.load()
    _send(out, {"event": "ready", **asdict(load), "generation": library.generation, "specs": _specs()})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            op = req.get("op")
            if op == "specs":
                reply = {"generation": library.generation, "specs": _specs()}
            elif op == "reload":
                load = library.reload()
                reply = {"load": asdict(load), "generation": library.generation, "specs": _specs()}
            elif op == "build":
                reply = library.build(req["spec_name"], req.get("inputs") or {}, pathlib.Path(req["glb_path"]))
                reply["generation"] = library.generation
            elif op == "subject":
                reply = library.subject(req["spec_name"], req.get("inputs") or {}, pathlib.Path(req["path"]))
                reply["generation"] = library.generation
            elif op == "quit":
                _send(out, {"id": req_id, "ok": True})
                return 0
            else:
                raise ValueError(f"unknown op {op!r}")
            _send(out, {"id": req_id, "ok": True, **reply})
        except BaseException as e:  # noqa: BLE001 - every failure is a reply; the loop keeps serving
            if isinstance(e, KeyboardInterrupt):
                raise
            _send(
                out,
                {
                    "id": req_id,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "traceback": format_user_traceback(e),
                    # A builder's NotApplicable: it refused these members, which is a message
                    # for the developer, not a crash.
                    "declined": is_declined(e),
                },
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
