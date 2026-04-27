# Pyodide IFC → GLB experiment

Standalone proof-of-concept: converts an IFC file to GLB **entirely in
the browser** using Pyodide + the official `ifcopenshell` WASM wheel +
`trimesh`. No server-side conversion is involved.

## Why this exists

Validates whether a pure-frontend conversion pipeline is viable for the
hosted viewer, as an alternative (or a fast path) to the NATS-backed
worker pipeline. See the parent branch's PR description for context.

## What it tests

1. `ifcopenshell.geom` tessellation works in Pyodide WASM — without
   `pythonocc-core`, using whichever geometry kernel ships in the
   wheel (CGAL / hybrid).
2. `trimesh` can assemble the iterator output into a valid GLB
   in-memory (avoiding the file-based `IfcGltfSerializer` that the
   maintainer flagged as unreliable in WASM).
3. Cold-start cost: download size + first-load time for Pyodide and
   the IFC wheel.
4. Conversion speed on a real IFC vs the server pipeline.

## Run it

```sh
pixi run pyodide-experiment
```

That serves this directory at http://localhost:8088. Open it in a
browser, pick an IFC file, click Convert.

(Web Workers won't load from `file://` URLs, which is why we need the
local HTTP server.)

## Files

- `index.html` — UI shell.
- `main.js` — main-thread driver: file picker, log panel, postMessage
  glue.
- `worker.js` — Web Worker. Boots Pyodide via the jsdelivr CDN,
  installs `trimesh` + the pinned IFC WASM wheel via `micropip`, then
  runs the conversion in Python.

## Knobs

- **Pyodide version** — pinned in `worker.js` as `PYODIDE_VERSION`.
  Must be ABI-compatible with the IFC wheel's `pyodide_2025_0_wasm32`
  tag (Pyodide 0.27+).
- **IFC wheel URL** — pinned in `worker.js` as `IFC_WASM_WHEEL`. The
  upstream index is at https://ifcopenshell.github.io/wasm-wheels/;
  bump as needed.

## Known limitations / open questions

- The IFC tessellation may fail on the same files that crashed the
  server pipeline (`solid_geom() not implemented for Shape` on certain
  test IFCs). That's an upstream gap, not a WASM-specific one.
- We do **not** use `ada-py-core` here yet — first-pass goal is just
  to confirm the WASM stack can produce GLB at all. Adding ada-py is a
  follow-up: it would need its `ada/occ/*` re-exports guarded against
  the absent `pythonocc-core`, and a wheel hosted somewhere micropip
  can fetch.
- Cross-origin isolation (COOP/COEP) is not set; if Pyodide ever needs
  `SharedArrayBuffer` for threading, we'd need to set the headers in a
  proper static server.

## Sources

- [IfcOpenShell WASM wheels index](https://ifcopenshell.github.io/wasm-wheels/)
- [IfcOpenShell WASM serializing discussion](https://github.com/IfcOpenShell/IfcOpenShell/discussions/6502)
- [Pyodide docs](https://pyodide.org/)
