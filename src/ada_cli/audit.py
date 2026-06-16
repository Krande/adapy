"""``ada audit`` — query, fetch, and locally re-run viewer audit conversions.

A read-only client over the admin audit API. It consolidates what used to be
ad-hoc ``curl`` calls plus the ``scripts/audit_fetch.py`` / ``audit_repro.py``
helpers:

    ada audit runs                     list recent regression-sweep runs
    ada audit run <run_id> [--failed]  show a run's per-cell jobs
    ada audit log [--target step ...]  query the per-conversion audit log
    ada audit perf [--source-ext .fem] function-level hot paths in a cell
    ada audit fetch <audit_id>         download a conversion's source blob
    ada audit repro <audit_id>         run that conversion locally (the local
                                       run option for a given audit)
    ada audit wasm-sweep <run_id>      re-run a run's cells locally through the
                                       in-browser WASM engine (node-pyodide),
                                       producing a local pass/fail report

Auth: ``ADAPY_API_TOKEN`` (a CLI token from the admin panel). Base URL:
``ADAPY_API_BASE`` or ``ADAPY_BASE_URL`` (host or full URL) — same env pair
the audit scripts already use.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import pathlib
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_OUT = "./audit_repro"

# ── WASM support matrix (mirror of frontend wasmSupport.ts) ───────────────
# The single source of truth on the frontend is
# src/frontend/src/services/conversion/wasmSupport.ts; keep this in lockstep.
WASM_MESH_EXTS = {"obj", "stl", "ply", "gltf", "dae", "off", "glb"}
WASM_FEA_EXTS = {"rmed", "med", "sif", "sin"}
WASM_FEM_DECK_EXTS = {"inp", "fem"}  # ada.from_fem decks (Abaqus / Sesam)
WASM_TARGETS_BY_FORMAT = {
    "sat": {"glb", "obj", "stl", "step", "xml", "ifc"},
    "ifc": {"glb", "obj", "stl", "step", "xml", "ifc"},
    "step": {"glb", "ifc", "xml", "stl", "obj", "step"},
    "mesh": {"glb", "obj", "stl"},
    # fem geometry targets + deck↔deck rewrites (inp/fem/med); identity pairs
    # (inp→inp, fem→fem) are excluded by the self-conversion guard below.
    "fem": {"glb", "ifc", "step", "xml", "obj", "stl", "inp", "fem", "med"},
    "genie": {"glb", "ifc", "step", "xml", "obj", "stl"},
}
# adacpp wheels below this version predate the OCCT symbol-isolation fix
# (-fvisibility=hidden) + serialize_brep — a sweep with one validates the OLD
# broken engine, so we refuse it unless explicitly overridden.
ADACPP_MIN_VERSION = (0, 9, 0)
ADACPP_DEFAULT_IMAGE = "ghcr.io/krande/adacpp-wasm-base:0.9.0"
IFC_WASM_WHEEL = (
    "https://ifcopenshell.github.io/wasm-wheels/" "ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl"
)


# ── config / http ────────────────────────────────────────────────────────


def _normalize_base(raw: str) -> str:
    raw = raw.rstrip("/")
    # The viewer is virtually always behind TLS; bare-hostname copy-paste is
    # the common ergonomic mistake, so default the scheme to https://.
    return raw if "://" in raw else f"https://{raw}"


def _config(args: argparse.Namespace) -> tuple[str, str]:
    # Honour a .env in the CWD even when called outside `ada audit`
    # (e.g. the scripts/ shims), without ever clobbering a real env var.
    from ada_cli import load_dotenv_cwd

    load_dotenv_cwd()
    base = (getattr(args, "url", None) or "").strip()
    if not base:
        for name in ("ADAPY_API_BASE", "ADAPY_BASE_URL"):
            val = os.environ.get(name, "").strip()
            if val:
                base = val
                break
    token = (getattr(args, "token", None) or os.environ.get("ADAPY_API_TOKEN", "")).strip()
    missing = []
    if not base:
        missing.append("ADAPY_API_BASE or ADAPY_BASE_URL (or --url), e.g. https://viewer.example.com")
    if not token:
        missing.append("ADAPY_API_TOKEN (or --token) — mint a CLI token in the admin panel")
    if missing:
        for m in missing:
            print(f"missing: {m}", file=sys.stderr)
        sys.exit(2)
    return _normalize_base(base), token


def _get(base: str, token: str, path: str) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(f"{base}{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} on {path}: {body[:300]}", file=sys.stderr)
        sys.exit(1)


def _get_json(base: str, token: str, path: str) -> dict:
    body, _ = _get(base, token, path)
    return json.loads(body)


def _qs(params: dict) -> str:
    items = {k: v for k, v in params.items() if v not in (None, "")}
    return ("?" + urllib.parse.urlencode(items)) if items else ""


def _short(text: str | None, n: int = 80) -> str:
    return (text or "").replace("\n", " ").strip()[:n]


# ── commands ─────────────────────────────────────────────────────────────


def cmd_runs(args: argparse.Namespace) -> int:
    base, token = _config(args)
    d = _get_json(base, token, "/api/admin/audit/runs" + _qs({"limit": args.limit, "before_started_at": args.before}))
    runs = d.get("runs", [])
    if args.json:
        print(json.dumps(runs, indent=2))
        return 0
    print(f"{'run_id':36}  {'status':9}  {'started':25}  {'total':>6}  {'failed':>6}")
    for r in runs:
        print(
            f"{str(r.get('id')):36}  {str(r.get('status')):9}  {str(r.get('started_at'))[:25]:25}  "
            f"{str(r.get('total') or 0):>6}  {str(r.get('failed') or 0):>6}"
        )
    if not runs:
        print("(no runs)", file=sys.stderr)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    base, token = _config(args)
    d = _get_json(base, token, f"/api/admin/audit/runs/{args.run_id}")
    run = d.get("run") or {}
    jobs = d.get("jobs", [])
    if args.failed:
        jobs = [j for j in jobs if j.get("status") == "error"]
    if args.format:
        jobs = [j for j in jobs if j.get("target_format") == args.format.lstrip(".")]
    if args.json:
        print(json.dumps({"run": run, "jobs": jobs}, indent=2))
        return 0
    print(
        f"run {run.get('id')}  status={run.get('status')}  "
        f"total={run.get('total')}  failed={run.get('failed')}  started={str(run.get('started_at'))[:25]}"
    )
    print(f"{'status':7}  {'target':6}  {'dur_s':>6}  {'rss_mb':>7}  {'key / error'}")
    for j in jobs:
        dur = (j.get("duration_ms") or 0) // 1000
        rss = (j.get("peak_rss_kb") or 0) // 1024
        tail = j.get("key") or ""
        if j.get("status") == "error":
            tail = f"{tail}  :: {_short(j.get('error'), 100)}"
        print(f"{str(j.get('status')):7}  {str(j.get('target_format')):6}  {dur:>6}  {rss:>7}  {tail}")
    if not jobs:
        print("(no matching jobs)", file=sys.stderr)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    base, token = _config(args)
    # The list endpoint filters by action/scope only; source/target/status/grep
    # are matched client-side, paging back until the row budget is met.
    source = (args.source or "").lstrip(".").lower()
    target = (args.target or "").lstrip(".").lower()
    rows: list[dict] = []
    before = None
    for _ in range(max(1, args.pages)):
        d = _get_json(base, token, "/api/admin/audit" + _qs({"limit": 200, "before_id": before}))
        entries = d.get("entries", [])
        if not entries:
            break
        for r in entries:
            key = (r.get("key") or "").lower()
            err = r.get("error") or ""
            if source and not key.endswith(f".{source}"):
                continue
            if target and (r.get("target_format") or "").lower() != target:
                continue
            if args.status and (r.get("status") or "") != args.status:
                continue
            if args.key and args.key.lower() not in key:
                continue
            if args.grep and args.grep.lower() not in err.lower():
                continue
            rows.append(r)
            if len(rows) >= args.limit:
                break
        if len(rows) >= args.limit:
            break
        before = d.get("next_before_id")
        if not before:
            break
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"{'id':>8}  {'status':7}  {'target':6}  {'key / error'}")
    for r in rows:
        tail = r.get("key") or ""
        if r.get("status") == "error":
            tail = f"{tail}  :: {_short(r.get('error'), 100)}"
        print(f"{str(r.get('id')):>8}  {str(r.get('status')):7}  {str(r.get('target_format')):6}  {tail}")
    if not rows:
        print("(no matching audit rows)", file=sys.stderr)
    return 0


def cmd_perf(args: argparse.Namespace) -> int:
    base, token = _config(args)

    # Run-scoped view: the function-level hotspots endpoint aggregates over a
    # time window only, so when the caller pins a run / worker build / trigger
    # we switch to the cell-level snapshot, which the server can lock to a run.
    if args.run or args.worker_tag or args.trigger:
        d = _get_json(
            base,
            token,
            "/api/admin/audit/perf"
            + _qs(
                {
                    "audit_run_id": args.run,
                    "worker_image_tag": args.worker_tag,
                    "trigger": args.trigger or "all",
                    "since": args.since,
                }
            ),
        )
        if args.json:
            print(json.dumps(d, indent=2))
            return 0
        print(
            f"run={d.get('audit_run_id')} worker={d.get('worker_image_tag')} "
            f"trigger={d.get('trigger')} since={d.get('since_days')}d"
        )
        cells = d.get("cells", [])
        if args.source_ext:
            cells = [c for c in cells if (c.get("source_ext") or "").lstrip(".") == args.source_ext.lstrip(".")]
        if args.target:
            cells = [c for c in cells if (c.get("target_format") or "") == args.target.lstrip(".")]
        print(f"{'cell':16}  {'n':>4}  {'fail%':>5}  {'dur_p95_s':>9}  {'rss_p95_mb':>10}  {'rss_max_mb':>10}  stream?")
        for c in cells:
            cell = f"{c.get('source_ext')}->{c.get('target_format')}"
            fail = (c.get("failure_rate") or 0) * 100
            durp95 = (c.get("duration_ms_p95") or 0) / 1000
            rssp95 = (c.get("peak_rss_kb_p95") or 0) / 1024
            rssmax = (c.get("peak_rss_max_kb") or 0) / 1024
            stream = c.get("streaming")
            stream_flag = "yes" if (isinstance(stream, dict) and stream.get("is_candidate")) else "no"
            print(
                f"{cell:16}  {c.get('sample_count') or 0:>4}  {fail:>5.0f}  "
                f"{durp95:>9.1f}  {rssp95:>10.0f}  {rssmax:>10.0f}  {stream_flag}"
            )
        if not cells:
            print("(no matching cells)", file=sys.stderr)
        return 0

    d = _get_json(
        base,
        token,
        "/api/admin/audit/perf/hotspots"
        + _qs(
            {
                "source_ext": args.source_ext,
                "target_format": args.target,
                "since": args.since,
                "limit": args.limit,
            }
        ),
    )
    if args.json:
        print(json.dumps(d, indent=2))
        return 0
    print(
        f"source_ext={d.get('source_ext')} target={d.get('target_format')} "
        f"profiles_in_window={d.get('profiles_in_window')}"
    )
    hotspots = d.get("functions") or d.get("hotspots") or d.get("rows") or []
    print(f"{'cumtime_s':>10}  {'calls':>12}  {'function'}")
    for h in hotspots:
        cum = h.get("agg_cumtime") or h.get("cumtime") or h.get("cumulative") or 0
        calls = h.get("agg_ncalls") or h.get("ncalls") or h.get("calls") or 0
        func = h.get("func") or h.get("function") or ""
        loc = f"  ({h.get('file')}:{h.get('line')})" if h.get("file") is not None else ""
        print(f"{float(cum):>10.1f}  {str(calls):>12}  {func}{loc}")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    base, token = _config(args)
    d = _get_json(base, token, f"/api/admin/audit/{args.audit_id}/profile/stats" + _qs({"limit": args.limit}))
    if args.json:
        print(json.dumps(d, indent=2))
        return 0
    if "detail" in d:
        print(d["detail"], file=sys.stderr)
        return 1
    print(
        f"audit_id={d.get('audit_id')} total_tottime={float(d.get('total_tottime') or 0):.1f}s "
        f"rows={d.get('row_count')}"
    )
    rows = sorted(d.get("rows", []), key=lambda r: r.get(args.sort) or 0, reverse=True)
    print(f"{'cumtime_s':>10}  {'tottime_s':>10}  {'ncalls':>10}  function")
    for r in rows:
        loc = f"  ({r.get('file')}:{r.get('line')})" if r.get("file") is not None else ""
        print(
            f"{float(r.get('cumtime') or 0):>10.2f}  {float(r.get('tottime') or 0):>10.2f}  "
            f"{str(r.get('ncalls')):>10}  {r.get('func')}{loc}"
        )
    return 0


# ── fetch / repro (the local-run path for a given audit) ──────────────────


def fetch(base: str, token: str, audit_id: int, out_root: pathlib.Path) -> tuple[pathlib.Path, dict]:
    """Download a conversion's metadata + source blob to ``out_root/<id>/``.

    Idempotent: re-uses an already-downloaded source. Returns (src_path, meta).
    """
    dest = out_root / str(audit_id)
    meta_path = dest / "audit.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        key_tail = (meta.get("key") or "").rsplit("/", 1)[-1]
        candidate = dest / key_tail if key_tail else None
        if candidate and candidate.exists():
            return candidate, meta

    dest.mkdir(parents=True, exist_ok=True)
    meta_bytes, _ = _get(base, token, f"/api/admin/audit/{audit_id}")
    meta = json.loads(meta_bytes)
    meta_path.write_text(json.dumps(meta, indent=2))

    source_bytes, headers = _get(base, token, f"/api/admin/audit/{audit_id}/source")
    if (headers.get("content-encoding") or "").lower() == "gzip":
        source_bytes = gzip.decompress(source_bytes)
    name = (meta.get("key") or "").rsplit("/", 1)[-1] or f"audit-{audit_id}.bin"
    src = dest / name
    src.write_bytes(source_bytes)
    return src, meta


def cmd_fetch(args: argparse.Namespace) -> int:
    base, token = _config(args)
    src, meta = fetch(base, token, args.audit_id, pathlib.Path(args.out))
    print(f"{src}  (target={meta.get('target_format')} status={meta.get('status')})")
    return 0


def cmd_repro(args: argparse.Namespace) -> int:
    base, token = _config(args)
    src, meta = fetch(base, token, args.audit_id, pathlib.Path(args.out))
    target = args.target or meta.get("target_format") or "glb"

    print(f"repro audit_id={args.audit_id} src={src} target={target}")
    print(f"original status={meta.get('status')} error={_short(meta.get('error'), 160)!r}")

    # Lazy import — keeps the rest of `ada audit` free of the FEM/CAD stack.
    from ada.comms.rest.converter import convert

    def on_progress(stage: str, frac: float) -> None:
        print(f"  [{frac:5.1%}] {stage}")

    try:
        out = convert(src, str(src), target, on_progress)
    except Exception:
        import traceback

        sys.stderr.write("\n--- repro raised ---\n")
        traceback.print_exc()
        return 1

    out_path = src.with_suffix(f".out.{target.lstrip('.')}")
    out_path.write_bytes(out)
    print(f"ok: wrote {out_path} ({len(out)} bytes)")
    return 0


def cmd_parity(args: argparse.Namespace) -> int:
    # Local-first, like cmd_repro: run the cross-format parity check in-process.
    # No admin API needed for a local PATH; --audit-id reuses fetch() to pull the
    # source blob first (the parity analogue of `ada audit repro`).
    from dataclasses import asdict

    # Remote mode: read persisted parity results for a finished audit run.
    if getattr(args, "run", None):
        d = _get_json(*_config(args), f"/api/admin/audit/runs/{args.run}/parity")
        rows = d.get("parity", [])
        if args.json:
            print(json.dumps(d, indent=2))
            return 0
        bad = 0
        for r in rows:
            counts = r.get("counts") or {}
            status = "OK" if r.get("consistent") else "MISMATCH"
            bad += 0 if r.get("consistent") else 1
            cells = " ".join(f"{k}={v}" for k, v in counts.items())
            print(f"{status:8}  base={r.get('baseline')}  {r.get('source_key')}  {cells}")
        if not rows:
            print("(no parity results for this run)", file=sys.stderr)
        return 0 if bad == 0 else 1

    from ada.cadit.visual_parity import parity_for_source_file

    formats = tuple(f.strip() for f in args.formats.split(",") if f.strip())

    if args.audit_id is not None:
        base, token = _config(args)
        path, _meta = fetch(base, token, args.audit_id, pathlib.Path(args.out))
    elif args.path:
        path = pathlib.Path(args.path)
    else:
        sys.stderr.write("error: provide a local PATH or --audit-id\n")
        return 2

    result = parity_for_source_file(path, formats)
    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(result.summary())
        for fmt, msg in result.errors.items():
            print(f"  ERROR {fmt}: {msg}")
    # exit non-zero on any divergence so the command is CI/script usable
    return 0 if result.consistent else 1


# ── wasm-sweep: run a remote run's cells locally through the WASM engine ──


def _repo_root() -> pathlib.Path:
    # audit.py lives at <repo>/src/ada_cli/audit.py
    return pathlib.Path(__file__).resolve().parents[2]


def _wasm_classify(ext: str, target: str) -> tuple[str | None, str]:
    """Map (source ext, requested target) → (wasm pipeline format, effective
    target), or (None, "") if the WASM engine can't do this cell.

    FEA sources go through the bake path (format "fea", target ignored); the
    rest must match the per-format target matrix.
    """
    ext = (ext or "").lower()
    target = (target or "").lower()
    if target == "stp":  # canonicalise; the registry/matrix use "step"
        target = "step"
    if ext in WASM_FEA_EXTS:
        return "fea", "fea"
    if ext == "ifc":
        fmt = "ifc"
    elif ext in ("step", "stp"):
        fmt = "step"
    elif ext in ("sat", "acis"):
        fmt = "sat"
    elif ext in WASM_FEM_DECK_EXTS:
        fmt = "fem"
    elif ext == "xml":
        fmt = "genie"
    elif ext in WASM_MESH_EXTS:
        fmt = "mesh"
    else:
        return None, ""
    # No-op self-conversions aren't real conversions: glb→glb (mesh) and the
    # deck identity pairs inp→inp / fem→fem (the worker excludes these too).
    if fmt in ("mesh", "fem") and target == ext:
        return None, ""
    if target in WASM_TARGETS_BY_FORMAT[fmt]:
        return fmt, target
    return None, ""


def _resolve_driver(args: argparse.Namespace) -> str:
    if getattr(args, "driver", None):
        p = pathlib.Path(args.driver)
        if not p.exists():
            print(f"error: --driver {p} does not exist", file=sys.stderr)
            sys.exit(2)
        return str(p)
    rel = pathlib.Path("tools") / "pyodide-test" / "wasm_sweep_driver.js"
    cur = pathlib.Path.cwd()
    for d in [cur, *cur.parents]:
        if (d / rel).exists():
            return str(d / rel)
    fallback = _repo_root() / rel
    if fallback.exists():
        return str(fallback)
    print(
        "error: could not find tools/pyodide-test/wasm_sweep_driver.js — run from the "
        "adapy checkout or pass --driver",
        file=sys.stderr,
    )
    sys.exit(2)


def _extract_adacpp_from_image(image: str, dest: pathlib.Path) -> pathlib.Path:
    if not shutil.which("docker"):
        print(
            f"error: no adacpp wheel given and docker is unavailable to extract it from {image}.\n"
            "       Pass --adacpp-wheel PATH (or set $ADACPP_WHEEL).",
            file=sys.stderr,
        )
        sys.exit(2)
    dest.mkdir(parents=True, exist_ok=True)
    print(f"extracting adacpp wheel from {image} …", file=sys.stderr)
    created = subprocess.run(["docker", "create", image], capture_output=True, text=True)
    if created.returncode != 0:
        print(f"error: docker create {image} failed: {created.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    cid = created.stdout.strip()
    try:
        cp = subprocess.run(["docker", "cp", f"{cid}:/out/.", str(dest)], capture_output=True, text=True)
        if cp.returncode != 0:
            print(f"error: docker cp from {image} failed: {cp.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
    finally:
        subprocess.run(["docker", "rm", cid], capture_output=True)
    wheels = sorted(dest.glob("ada_cpp-*.whl"))
    if not wheels:
        print(f"error: no ada_cpp-*.whl found in {image}:/out", file=sys.stderr)
        sys.exit(1)
    return wheels[-1]


def _guard_adacpp_version(path: pathlib.Path, allow_stale: bool) -> None:
    m = re.search(r"ada_cpp-(\d+)\.(\d+)\.(\d+)", path.name)
    if not m:
        print(f"warning: cannot parse adacpp version from {path.name}", file=sys.stderr)
        return
    ver = tuple(int(x) for x in m.groups())
    if ver < ADACPP_MIN_VERSION:
        want = ".".join(str(x) for x in ADACPP_MIN_VERSION)
        msg = (
            f"adacpp wheel {path.name} is older than {want} — it predates the OCCT "
            "symbol-isolation fix + serialize_brep, so a sweep with it validates the OLD "
            "broken engine, not what ships."
        )
        if not allow_stale:
            print(f"error: {msg}\n       re-run with --allow-stale-adacpp to override.", file=sys.stderr)
            sys.exit(2)
        print(f"warning: {msg}", file=sys.stderr)


def _resolve_adacpp_wheel(args: argparse.Namespace) -> str:
    cand = None
    if getattr(args, "adacpp_wheel", None):
        cand = pathlib.Path(args.adacpp_wheel)
    elif os.environ.get("ADACPP_WHEEL"):
        cand = pathlib.Path(os.environ["ADACPP_WHEEL"])
    if cand is None:
        cand = _extract_adacpp_from_image(args.adacpp_image, pathlib.Path(args.out) / "_adacpp_wheel")
    if not cand.exists():
        print(f"error: adacpp wheel not found: {cand}", file=sys.stderr)
        sys.exit(2)
    _guard_adacpp_version(cand, getattr(args, "allow_stale_adacpp", False))
    return str(cand)


def _resolve_adapy_wheel(args: argparse.Namespace) -> str:
    if getattr(args, "adapy_wheel", None):
        p = pathlib.Path(args.adapy_wheel)
    elif os.environ.get("ADAPY_WHEEL"):
        p = pathlib.Path(os.environ["ADAPY_WHEEL"])
    else:
        cands = sorted((_repo_root() / "src" / "frontend" / "public" / "wheels").glob("ada_py-*.whl"))
        if not cands:
            print(
                "error: no adapy wheel found — build it (pixi run wheel-pyodide) or pass --adapy-wheel.",
                file=sys.stderr,
            )
            sys.exit(2)
        p = cands[-1]
    if not p.exists():
        print(f"error: adapy wheel not found: {p}", file=sys.stderr)
        sys.exit(2)
    return str(p)


def _safe_json(line: str) -> dict | None:
    try:
        obj = json.loads(line)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def _reader_thread(stream, q: queue.Queue) -> None:
    try:
        for line in stream:
            q.put(line)
    finally:
        q.put(None)  # EOF sentinel


def _wait_ready(q: queue.Queue, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            line = q.get(timeout=remaining)
        except queue.Empty:
            return False
        if line is None:
            return False
        obj = _safe_json(line)
        if obj and obj.get("type") == "ready":
            return True


def _await_result(q: queue.Queue, expect_id, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            line = q.get(timeout=remaining)
        except queue.Empty:
            return None
        if line is None:  # driver process died (crash / OOM abort)
            return None
        obj = _safe_json(line)
        if obj and obj.get("type") == "result" and obj.get("id") == expect_id:
            return obj


def _run_sweep(
    node: str,
    driver: str,
    adacpp: str,
    adapy: str,
    ifc_wheel: str,
    cells: list[dict],
    cwd: str,
    boot_timeout: float,
    cell_timeout: float,
) -> dict:
    """Drive the node-pyodide subprocess over ``cells``. One persistent process
    handles as many cells as it can; a wasm abort / OOM that kills it (or a cell
    exceeding ``cell_timeout``) is recorded against the offending cell, which is
    then popped so we always make forward progress, and the driver is restarted
    for the remainder. Returns {id: result-dict}.
    """
    results: dict = {}
    pending = list(cells)
    boot_failures = 0
    cmd = [node, driver, "--adacpp", adacpp, "--adapy", adapy, "--ifc-wheel", ifc_wheel]
    while pending:
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        q: queue.Queue = queue.Queue()
        threading.Thread(target=_reader_thread, args=(proc.stdout, q), daemon=True).start()
        if not _wait_ready(q, boot_timeout):
            proc.kill()
            boot_failures += 1
            if boot_failures >= 2:
                for c in pending:
                    results[c["id"]] = {"ok": False, "error": "driver failed to boot pyodide"}
                pending.clear()
                break
            continue
        boot_failures = 0
        progressed = False
        while pending:
            cell = pending[0]
            payload = json.dumps(
                {
                    "type": "cell",
                    "id": cell["id"],
                    "format": cell["format"],
                    "ext": cell["ext"],
                    "target": cell["target"],
                    "src": cell["src"],
                }
            )
            try:
                proc.stdin.write(payload + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                break  # driver died between cells — restart
            res = _await_result(q, cell["id"], cell_timeout)
            if res is None:
                results[cell["id"]] = {
                    "ok": False,
                    "crashed": True,
                    "error": "driver crashed or cell exceeded timeout (likely wasm OOM/abort)",
                }
                pending.pop(0)
                progressed = True
                break  # restart the driver for the rest
            results[cell["id"]] = res
            pending.pop(0)
            progressed = True
        # graceful shutdown when the session drained without crashing
        try:
            if proc.poll() is None:
                proc.stdin.write(json.dumps({"type": "quit"}) + "\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        if not progressed and pending:
            # safety valve: never spin forever on an unresponsive driver
            c = pending.pop(0)
            results[c["id"]] = {"ok": False, "error": "no progress (driver unresponsive)"}
    return results


def cmd_wasm_sweep(args: argparse.Namespace) -> int:
    base, token = _config(args)

    node = args.node or shutil.which("node")
    if not node:
        print(
            "error: node not found. Run under the frontend env, e.g.\n"
            "       pixi run -e frontend ada audit wasm-sweep ...",
            file=sys.stderr,
        )
        return 2
    driver = _resolve_driver(args)
    driver_dir = os.path.dirname(driver)
    if not os.path.isdir(os.path.join(driver_dir, "node_modules", "pyodide")):
        print(
            f"error: the pyodide npm package is missing in {driver_dir}. Install it once:\n"
            f"       (cd {driver_dir} && pixi run -e frontend npm install)",
            file=sys.stderr,
        )
        return 2

    adacpp = _resolve_adacpp_wheel(args)
    adapy = _resolve_adapy_wheel(args)
    print(f"node:   {node}", file=sys.stderr)
    print(f"adacpp: {adacpp}", file=sys.stderr)
    print(f"adapy:  {adapy}", file=sys.stderr)

    d = _get_json(base, token, f"/api/admin/audit/runs/{args.run_id}")
    run = d.get("run") or {}
    jobs = d.get("jobs", [])
    if args.format:
        want = args.format.lstrip(".")
        jobs = [j for j in jobs if (j.get("target_format") or "").lstrip(".") == want]

    out_root = pathlib.Path(args.out)
    cells: list[dict] = []
    skipped: list[dict] = []
    key_cache: dict[str, str] = {}
    for j in jobs:
        key = j.get("key") or ""
        target = (j.get("target_format") or "").lstrip(".").lower()
        ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
        fmt, eff_target = _wasm_classify(ext, target)
        if fmt is None:
            skipped.append({"key": key, "target": target, "reason": "not wasm-supported"})
            continue
        audit_id = j.get("id", j.get("audit_id"))
        if audit_id is None:
            skipped.append({"key": key, "target": target, "reason": "no audit id to fetch source"})
            continue
        if key in key_cache:
            src_path = key_cache[key]
        else:
            src, _meta = fetch(base, token, audit_id, out_root)
            src_path = str(src)
            key_cache[key] = src_path
        cells.append({"id": audit_id, "key": key, "ext": ext, "format": fmt, "target": eff_target, "src": src_path})

    if args.limit and len(cells) > args.limit:
        cells = cells[: args.limit]

    print(
        f"run {run.get('id') or args.run_id}: {len(jobs)} jobs → " f"{len(cells)} wasm cells, {len(skipped)} skipped",
        file=sys.stderr,
    )

    results = _run_sweep(
        node, driver, adacpp, adapy, args.ifc_wheel, cells, driver_dir, args.boot_timeout, args.cell_timeout
    )

    rows = []
    n_ok = n_fail = 0
    for c in cells:
        r = results.get(c["id"], {"ok": False, "error": "no result"})
        ok = bool(r.get("ok"))
        n_ok += ok
        n_fail += not ok
        rows.append(
            {
                "audit_id": c["id"],
                "key": c["key"],
                "format": c["format"],
                "target": c["target"],
                "ok": ok,
                "ms": r.get("ms"),
                "bytes": r.get("bytes"),
                "error": r.get("error"),
            }
        )

    report = {
        "run_id": run.get("id") or args.run_id,
        "adacpp_wheel": os.path.basename(adacpp),
        "adapy_wheel": os.path.basename(adapy),
        "totals": {"cells": len(cells), "ok": n_ok, "failed": n_fail, "skipped": len(skipped)},
        "cells": rows,
        "skipped": skipped,
    }
    report_path = pathlib.Path(args.report) if args.report else out_root / f"wasm_sweep_{report['run_id']}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if n_fail == 0 else 1

    print(f"{'status':7}  {'cell':16}  {'ms':>7}  {'out_kb':>8}  {'key / error'}")
    for r in rows:
        cell = f"{r['format']}->{r['target']}"
        ms = r.get("ms") or 0
        kb = (r.get("bytes") or 0) // 1024
        tail = r["key"]
        if not r["ok"]:
            tail = f"{tail}  :: {_short(r.get('error'), 100)}"
        print(f"{('ok' if r['ok'] else 'FAIL'):7}  {cell:16}  {ms:>7}  {kb:>8}  {tail}")
    print(
        f"\n{n_ok} ok, {n_fail} failed, {len(skipped)} skipped  →  {report_path}",
        file=sys.stderr,
    )
    return 0 if n_fail == 0 else 1


# ── parser wiring ────────────────────────────────────────────────────────


def add_parser(sub: argparse._SubParsersAction) -> None:
    audit = sub.add_parser(
        "audit",
        help="Query/fetch/repro viewer audit runs (needs ADAPY_API_TOKEN + ADAPY_BASE_URL).",
    )
    asub = audit.add_subparsers(dest="audit_command", required=True)

    def _remote(p: argparse.ArgumentParser) -> None:
        p.add_argument("--url", default=None, help="Viewer base URL (default: $ADAPY_API_BASE/$ADAPY_BASE_URL).")
        p.add_argument("--token", default=None, help="Bearer token (default: $ADAPY_API_TOKEN).")
        p.add_argument("--json", action="store_true", help="Emit raw JSON instead of a table.")

    runs = asub.add_parser("runs", help="List recent regression-sweep runs.")
    _remote(runs)
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument("--before", default=None, help="Page cursor: started_at of the oldest row seen.")
    runs.set_defaults(func=cmd_runs)

    run = asub.add_parser("run", help="Show a run's per-cell jobs.")
    _remote(run)
    run.add_argument("run_id")
    run.add_argument("--failed", action="store_true", help="Only failed cells.")
    run.add_argument("--format", default=None, help="Only this target format, e.g. step.")
    run.set_defaults(func=cmd_run)

    log = asub.add_parser("log", help="Query the per-conversion audit log.")
    _remote(log)
    log.add_argument("--limit", type=int, default=50, help="Max rows to return.")
    log.add_argument("--pages", type=int, default=12, help="Max 200-row pages to scan.")
    log.add_argument("--source", default=None, help="Source extension, e.g. .fem.")
    log.add_argument("--target", default=None, help="Target format, e.g. step.")
    log.add_argument("--status", default=None, help="Status, e.g. error / done.")
    log.add_argument("--key", default=None, help="Substring match on the source key.")
    log.add_argument("--grep", default=None, help="Substring match on the error text.")
    log.set_defaults(func=cmd_log)

    perf = asub.add_parser(
        "perf",
        help="Perf hot paths (function-level by default; cell-level when --run/--worker-tag/--trigger is given).",
    )
    _remote(perf)
    perf.add_argument("--source-ext", default=None, help="e.g. .fem.")
    perf.add_argument("--target", default=None, help="e.g. step.")
    perf.add_argument("--since", type=int, default=30, help="Window in days (default 30).")
    perf.add_argument("--limit", type=int, default=25)
    perf.add_argument("--run", default=None, help="Lock to one sweep (audit_run_id) — cell-level snapshot.")
    perf.add_argument("--worker-tag", default=None, help="Lock to one worker image tag (cell-level snapshot).")
    perf.add_argument("--trigger", default=None, help="all | audit | user (cell-level snapshot).")
    perf.set_defaults(func=cmd_perf)

    profile = asub.add_parser("profile", help="Per-conversion function stats (one audit row's cProfile).")
    _remote(profile)
    profile.add_argument("audit_id", type=int)
    profile.add_argument("--limit", type=int, default=50)
    profile.add_argument(
        "--sort",
        default="cumtime",
        choices=["cumtime", "tottime", "ncalls"],
        help="Sort key (default cumtime).",
    )
    profile.set_defaults(func=cmd_profile)

    fetch_p = asub.add_parser("fetch", help="Download a conversion's source blob.")
    _remote(fetch_p)
    fetch_p.add_argument("audit_id", type=int)
    fetch_p.add_argument("--out", default=DEFAULT_OUT, help=f"Download root (default: {DEFAULT_OUT}).")
    fetch_p.set_defaults(func=cmd_fetch)

    repro = asub.add_parser("repro", help="Run a given audit's conversion locally.")
    _remote(repro)
    repro.add_argument("audit_id", type=int)
    repro.add_argument("--out", default=DEFAULT_OUT, help=f"Download root (default: {DEFAULT_OUT}).")
    repro.add_argument("--target", default=None, help="Override the audit's target_format.")
    repro.set_defaults(func=cmd_repro, needs_ada_logging=True)

    sweep = asub.add_parser(
        "wasm-sweep",
        help="Run a remote run's cells locally through the WASM engine (node-pyodide), "
        "producing a local pass/fail report. No DB writes.",
    )
    _remote(sweep)
    sweep.add_argument("run_id", help="The audit run id whose cells to re-run via WASM.")
    sweep.add_argument("--out", default=DEFAULT_OUT, help=f"Download root for source blobs (default: {DEFAULT_OUT}).")
    sweep.add_argument("--report", default=None, help="Report JSON path (default: <out>/wasm_sweep_<run>.json).")
    sweep.add_argument("--format", default=None, help="Only cells with this target format, e.g. glb.")
    sweep.add_argument("--limit", type=int, default=0, help="Cap the number of wasm cells (0 = all).")
    sweep.add_argument(
        "--adacpp-wheel",
        default=None,
        help="adacpp wasm wheel (default: $ADACPP_WHEEL or extract from --adacpp-image).",
    )
    sweep.add_argument(
        "--adacpp-image",
        default=ADACPP_DEFAULT_IMAGE,
        help=f"Base image to extract the adacpp wheel from (default: {ADACPP_DEFAULT_IMAGE}).",
    )
    sweep.add_argument(
        "--allow-stale-adacpp",
        action="store_true",
        help="Permit an adacpp wheel older than the isolation-fix release (validates the OLD engine).",
    )
    sweep.add_argument(
        "--adapy-wheel",
        default=None,
        help="adapy pyodide wheel (default: $ADAPY_WHEEL or src/frontend/public/wheels/ada_py-*.whl).",
    )
    sweep.add_argument(
        "--ifc-wheel",
        default=IFC_WASM_WHEEL,
        help="ifcopenshell wasm wheel URL (default: the canonical pyodide_2025_0 wheel).",
    )
    sweep.add_argument(
        "--driver", default=None, help="Path to wasm_sweep_driver.js (default: discovered from the checkout)."
    )
    sweep.add_argument("--node", default=None, help="node binary (default: $PATH; run under `pixi run -e frontend`).")
    sweep.add_argument(
        "--boot-timeout", type=float, default=180.0, help="Seconds to wait for pyodide boot (default: 180)."
    )
    sweep.add_argument("--cell-timeout", type=float, default=300.0, help="Per-cell timeout in seconds (default: 300).")
    # No needs_ada_logging: the sweep's Python side is pure urllib + subprocess
    # orchestration — adapy only runs inside the node-pyodide driver, so we must
    # NOT import ada here (it isn't installed in the frontend env we run under).
    sweep.set_defaults(func=cmd_wasm_sweep)

    parity = asub.add_parser(
        "parity",
        help="Cross-format visual-parity check on a local model "
        "(exports to ifc/xml/step, reloads, compares visualized element counts).",
    )
    _remote(parity)  # --url/--token/--json (url/token only used with --audit-id)
    parity.add_argument("path", nargs="?", help="Local source model file.")
    parity.add_argument(
        "--audit-id", type=int, default=None, help="Instead of PATH: fetch this audit's source blob first."
    )
    parity.add_argument("--run", default=None, help="Remote: print persisted parity results for this audit run id.")
    parity.add_argument(
        "--formats",
        default="ifc,xml,step",
        help="Comma-separated structure-preserving formats (default: ifc,xml,step).",
    )
    parity.add_argument("--out", default=DEFAULT_OUT, help=f"Download root for --audit-id (default: {DEFAULT_OUT}).")
    parity.set_defaults(func=cmd_parity, needs_ada_logging=True)
