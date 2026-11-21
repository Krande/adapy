# solidcore-py (EXPERIMENTAL)

A thin PyO3 binding of [solidcore](https://github.com/tangency-labs/solidcore), the pure-Rust
port of NVIDIA's SMLib B-rep kernel (Apache-2.0), so adapy can try it behind its CAD backend
abstraction (`ada.cad.solidcore_backend.SolidcoreBackend`).

solidcore publishes no Python package and nothing on PyPI or conda-forge, so this crate builds
it from source, pinned to one commit of the official repository (see `Cargo.toml`).

## Build

```
pixi run -e tests-solidcore build-solidcore   # maturin develop --release (about 4 min cold)
pixi run -e tests-solidcore test-solidcore
pixi run -e tests-solidcore python scripts/diagnostics/solidcore_compare.py
```

The `tests-solidcore` environment carries Rust and maturin from conda-forge next to pythonocc and
ada-cpp, so the three kernels can be compared in one process. On Windows the MSVC linker comes
from the Visual Studio Build Tools. solidcore's repository has file names longer than 260
characters (test fixtures); cargo's git checkout copes with them, a plain `git clone` on Windows
needs `core.longpaths=true`.

## What it binds

`solidcore-ops` (primitives, extrude, revolve, ruled/smooth loft, planar faces, booleans,
transform, mass properties, bounds, point classification, the kernel's validity check),
`solidcore-tess` (display mesh) and `solidcore-step` (AP242 writer). Topology is handed to Python
as the B-rep record's JSON (`Body.record_json()`), so faces/edges/vertices/loops are explored on
the Python side.

The kernel works in millimetres; the adapy backend scales metres by 1000 on the way in.
