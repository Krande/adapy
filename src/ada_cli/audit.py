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
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_OUT = "./audit_repro"


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
