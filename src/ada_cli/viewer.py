"""``ada viewer`` -- drive your open viewer from the command line, and read what the clash pipeline
stored (``docs/architecture/viewer_control.md``).

Two transports, one set of commands:

* **remote** -- a hosted viewer's REST API (``POST /api/viewer/commands`` and friends), with the
  same URL / token / scope the other remote commands use (``ada_cli.resolve_remote_config``:
  ``--url`` / ``ADAPY_API_BASE``, ``--token`` / ``ADAPY_API_TOKEN``, ``--scope`` / ``ADAPY_API_SCOPE``).
  The server must allow viewer control (``ADA_VIEWER_CONTROL``) and the tab must have External
  controls on.
* **local** -- the desktop viewer's websocket server (``ada view``; ``ws://localhost:8765``): the
  command goes to a viewer as ``VIEWER_COMMAND`` (``ada.comms.viewer_wire``), relayed by the
  server, and its ``VIEWER_ANSWER`` comes back by request id.

``--via auto`` (the default) picks **remote when a viewer URL is configured** (``--url`` or one of the
URL variables), else **local**; ``--via remote`` / ``--via local`` force one.

Exit codes: 0 when every targeted tab answered ``done``; 1 when the server refused (no viewer open,
viewer control disabled, ...) or any tab answered ``error``, closed (``gone``) or did not answer in
time (``pending``); 2 for a wrong invocation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from typing import Any, Callable

from ada_cli import CliUsageError

#: The local websocket server the desktop viewer runs (``ada view``).
DEFAULT_WS_HOST = "localhost"
DEFAULT_WS_PORT = 8765
DEFAULT_WAIT_S = 10.0

#: Replaced in tests: an ``httpx.Client``-like object for the remote transport.
HTTP_FACTORY: Callable[[], Any] | None = None
#: Replaced in tests: builds the local websocket client from (host, port).
WS_CLIENT_FACTORY: Callable[[str, int], Any] | None = None


class ViewerFailure(Exception):
    """The server refused, or a tab failed: printed, exit 1."""


# ── transports ───────────────────────────────────────────────────────────────────────────────────


class RemoteViewer:
    """The hosted viewer's REST API."""

    def __init__(self, base_url: str, token: str, http: Any) -> None:
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = http

    def _call(self, method: str, path: str, *, timeout: float = 30.0, **kw) -> Any:
        resp = self.http.request(method, f"{self.base}{path}", headers=self.headers, timeout=timeout, **kw)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text
            if not isinstance(detail, str):
                detail = json.dumps(detail)
            raise ViewerFailure(f"{resp.status_code}: {detail}")
        return resp.json()

    def sessions(self) -> list[dict]:
        return self._call("GET", "/api/viewer/sessions")["sessions"]

    def session(self, session_id: str) -> dict:
        return self._call("GET", f"/api/viewer/sessions/{session_id}")

    def send(self, payload: dict, session: str | None, wait_s: float) -> dict:
        body = {**payload, "wait_s": wait_s, **({"session_id": session} if session else {})}
        return self._call("POST", "/api/viewer/commands", json=body, timeout=wait_s + 30.0)

    def schema(self) -> dict:
        return self._call("GET", "/api/viewer/commands/schema")

    def joints(self, scope: str, params: dict) -> dict:
        return self._call("GET", f"/api/scopes/{scope}/clash-contacts/joints", params=params)

    def detail(self, scope: str, detail_id: str, params: dict) -> dict:
        return self._call("GET", f"/api/scopes/{scope}/clash-detail/models/{detail_id}", params=params)


class LocalViewer:
    """The desktop viewer through its websocket server: a session is a viewer's instance id."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def _viewers(self) -> list[dict]:
        return [
            {"session_id": str(c.instance_id), "client": {"name": c.name, "address": c.address}}
            for c in (self.client.list_connected_web_clients() or [])
        ]

    def sessions(self) -> list[dict]:
        return self._viewers()

    def session(self, session_id: str) -> dict:
        found = [s for s in self._viewers() if s["session_id"] == session_id]
        if not found:
            raise ViewerFailure(f"no viewer {session_id!r} on this websocket server")
        return found[0]

    def send(self, payload: dict, session: str | None, wait_s: float) -> dict:
        from ada.comms.fb_wrap_model_gen import CommandTypeDC, TargetTypeDC
        from ada.comms.rest.viewer_control import _steps
        from ada.comms.viewer_wire import command_message, payload_of

        viewers = [v["session_id"] for v in self._viewers()]
        targets = [session] if session else viewers
        if session and session not in viewers:
            raise ViewerFailure(
                f"no viewer {session!r} on this websocket server (open: {', '.join(viewers) or 'none'})"
            )
        if not targets:
            raise ViewerFailure("you have no viewer open (no web client on the websocket server)")
        command_id = uuid.uuid4().hex
        for target in targets:
            self.client.send(
                command_message(
                    command_id,
                    payload,
                    instance_id=self.client.instance_id,
                    target_id=int(target),
                    client_type=TargetTypeDC.LOCAL,
                )
            )
        results: dict[str, dict] = {}
        deadline = time.monotonic() + wait_s
        while len(results) < len(targets) and (left := deadline - time.monotonic()) > 0:
            try:
                msg = self.client.receive(timeout=left)
            except TimeoutError:
                break
            if msg.command_type != CommandTypeDC.VIEWER_ANSWER or msg.request_id != command_id:
                continue
            answer = payload_of(msg) or {}
            ok = bool(answer.get("ok"))
            result = {
                "status": "done" if ok else "error",
                "error": None if ok else str(answer.get("error") or "failed"),
                "result": answer.get("result"),
                "state": answer.get("state"),
            }
            if "commands" in payload:
                result["steps"] = _steps(answer.get("steps"), payload["commands"])
            results[str(msg.instance_id)] = result
        return {
            "command_id": command_id,
            **payload,
            "results": [{"session_id": t, **results.get(t, {"status": "pending"})} for t in targets],
        }

    def schema(self) -> dict:
        from ada.comms.rest.viewer_control import command_schema

        return command_schema()


class _WsClient:
    """The sync websocket client, with the two calls :class:`LocalViewer` makes on its socket."""

    def __init__(self, host: str, port: int) -> None:
        from ada.comms.wsock.client_sync import WebSocketClientSync

        self.inner = WebSocketClientSync(host, port, "local")
        self.instance_id = self.inner.instance_id

    def __enter__(self):
        self.inner.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.inner.disconnect()

    def list_connected_web_clients(self):
        return self.inner.list_connected_web_clients()

    def send(self, data: bytes) -> None:
        self.inner.websocket.send(data)

    def receive(self, timeout: float | None = None):
        return self.inner.receive(timeout=timeout)


def _via(args: argparse.Namespace) -> str:
    from ada_cli import resolve_remote_config

    if args.via != "auto":
        return args.via
    url, _, _ = resolve_remote_config(getattr(args, "url", None))
    return "remote" if url else "local"


def _remote(args: argparse.Namespace) -> tuple[RemoteViewer, str]:
    from ada_cli import resolve_remote_config

    url, token, scope = resolve_remote_config(args.url, args.token, getattr(args, "scope", None))
    if not url:
        raise CliUsageError("no viewer URL: pass --url or set ADAPY_API_BASE (or use --via local)")
    if HTTP_FACTORY is not None:
        http = HTTP_FACTORY()
    else:
        import httpx

        http = httpx.Client()
    return RemoteViewer(url, token, http), scope or "user:me"


def _with_viewer(args: argparse.Namespace, fn: Callable[[Any, str], Any]) -> Any:
    """Run ``fn(viewer, scope)`` on the transport ``--via`` picks."""
    if _via(args) == "remote":
        viewer, scope = _remote(args)
        return fn(viewer, scope)
    factory = WS_CLIENT_FACTORY or _WsClient
    try:
        client = factory(args.ws_host, args.ws_port)
        with client as c:
            return fn(LocalViewer(c), "")
    except (OSError, ConnectionError) as exc:
        raise ViewerFailure(
            f"no websocket server at ws://{args.ws_host}:{args.ws_port} ({exc}); start a viewer with `ada view`, "
            "or pass --url for a hosted one"
        ) from exc


# ── output ───────────────────────────────────────────────────────────────────────────────────────


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _short(value: Any, limit: int = 100) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _outcome_failed(outcome: dict) -> bool:
    return any(r.get("status") != "done" for r in outcome.get("results", []))


def _print_outcome(outcome: dict, wait_s: float) -> None:
    for r in outcome.get("results", []):
        sid = str(r.get("session_id", ""))[:12]
        status = r.get("status")
        if status == "done":
            line = "done" + (f"  {_short(r.get('result'))}" if r.get("result") is not None else "")
        elif status == "pending":
            line = f"pending  (no answer within {wait_s:g} s; ask again with --json for the command id)"
        elif status == "gone":
            line = f"gone  {r.get('error') or 'the tab closed'}"
        else:
            line = f"error  {r.get('error')}"
        print(f"{sid}  {line}")
        for step in r.get("steps") or []:
            extra = step.get("error") or (_short(step.get("result"), 70) if step.get("result") is not None else "")
            print(
                f"    {step['index'] + 1}. {step.get('action')}: {step.get('status')}" + (f"  {extra}" if extra else "")
            )


def _print_state(sid: str, state: dict | None) -> None:
    state = state or {}
    clash = state.get("clash") or {}
    selection = state.get("selection") or {}
    print(f"{sid[:12]}  scope={state.get('scope')}  models={len(state.get('loaded_models') or [])}")
    for model in state.get("loaded_models") or []:
        print(f"    model: {model}")
    if state.get("detail_models_loaded"):
        print(f"    detail models: {', '.join(state['detail_models_loaded'])}")
    print(
        f"    selection: {selection.get('count', 0)}"
        + (f"  bounds={selection['bounds']}" if selection.get("bounds") else "")
    )
    if clash.get("run_key") or clash.get("selected_joint"):
        print(f"    clash run: {clash.get('run_key')}  joint: {clash.get('selected_joint')}")
    if state.get("camera"):
        cam = state["camera"]
        print(f"    camera: eye={cam.get('eye')} target={cam.get('target')}")


# ── commands ─────────────────────────────────────────────────────────────────────────────────────


def _send(args: argparse.Namespace, payload: dict) -> int:
    from pydantic import ValidationError

    from ada.comms.rest.viewer_control import parse_request

    try:
        req = parse_request(payload)
    except ValidationError as exc:
        raise CliUsageError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))
    body = req.payload()
    outcome = _with_viewer(args, lambda v, _scope: v.send(body, args.session, args.wait))
    if args.json:
        _print_json(outcome)
    else:
        _print_outcome(outcome, args.wait)
    return 1 if _outcome_failed(outcome) else 0


def _vec(text: str, what: str) -> list[float]:
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) != 3:
        raise CliUsageError(f"{what} must be three numbers, x,y,z (got {text!r})")
    return values


def cmd_sessions(args: argparse.Namespace) -> int:
    sessions = _with_viewer(args, lambda v, _scope: v.sessions())
    if args.json:
        _print_json({"sessions": sessions})
        return 0
    if not sessions:
        print("(no viewer open)", file=sys.stderr)
        return 0
    for s in sessions:
        client = s.get("client") or {}
        name = client.get("title") or client.get("name") or ""
        idle = f"  idle {s['idle_s']:.0f}s" if isinstance(s.get("idle_s"), (int, float)) else ""
        print(f"{s['session_id']}  {name}{idle}")
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    def read(viewer, _scope):
        if isinstance(viewer, LocalViewer):
            # The desktop server keeps no state for the agent: ask the viewers.
            return viewer.send({"command": {"action": "get_state"}}, args.session, args.wait)
        if args.session:
            return {"sessions": [viewer.session(args.session)]}
        return {"sessions": viewer.sessions()}

    out = _with_viewer(args, read)
    if "results" in out:
        if args.json:
            _print_json(out)
        else:
            for r in out["results"]:
                if r.get("status") == "done":
                    _print_state(r["session_id"], r.get("state"))
                else:
                    print(f"{r['session_id']}  {r.get('status')}  {r.get('error') or ''}")
        return 1 if _outcome_failed(out) else 0
    if args.json:
        _print_json(out)
        return 0
    if not out["sessions"]:
        raise ViewerFailure("you have no viewer open")
    for s in out["sessions"]:
        _print_state(s["session_id"], s.get("state"))
    return 0


def cmd_focus_joint(args: argparse.Namespace) -> int:
    command = {"action": "focus_joint", "joint_id": args.joint_id}
    if args.run_key:
        command["run_key"] = args.run_key
    if args.scope:
        command["scope"] = args.scope
    if args.no_frame:
        command["frame"] = False
    return _send(args, {"command": command})


def cmd_select(args: argparse.Namespace) -> int:
    command: dict = {"action": "select", "names": args.names}
    if args.file:
        command["file"] = args.file
    if args.no_frame:
        command["frame"] = False
    return _send(args, {"command": command})


def cmd_clear_selection(args: argparse.Namespace) -> int:
    return _send(args, {"command": {"action": "clear_selection"}})


def cmd_camera(args: argparse.Namespace) -> int:
    command: dict = {"action": "camera"}
    if args.fit:
        command["fit"] = args.fit
    if args.view:
        command["view"] = args.view
    if args.eye or args.look_at:
        if not (args.eye and args.look_at):
            raise CliUsageError("--eye and --target go together")
        command["eye"] = _vec(args.eye, "--eye")
        command["target"] = _vec(args.look_at, "--target")
    if args.box:
        values = [float(v) for v in args.box.split(",")] if args.box else []
        if len(values) != 6:
            raise CliUsageError("--box must be six numbers: xmin,ymin,zmin,xmax,ymax,zmax")
        command["box"] = {"min": values[:3], "max": values[3:]}
    return _send(args, {"command": command})


def cmd_open_detail(args: argparse.Namespace) -> int:
    command: dict = {"action": "open_detail_model", "detail_id": args.detail_id}
    if args.joint:
        command["joint_id"] = args.joint
    if args.scope:
        command["scope"] = args.scope
    return _send(args, {"command": command})


def cmd_find(args: argparse.Namespace) -> int:
    command: dict = {"action": "find_objects", "names": args.names}
    if args.file:
        command["file"] = args.file
    return _send(args, {"command": command})


def cmd_run(args: argparse.Namespace) -> int:
    try:
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        payload = json.loads(text)
    except OSError as exc:
        raise CliUsageError(f"cannot read {args.file}: {exc}")
    except json.JSONDecodeError as exc:
        raise CliUsageError(f"{args.file} is not JSON: {exc}")
    if isinstance(payload, list):
        payload = {"commands": payload}
    elif isinstance(payload, dict) and "action" in payload:
        payload = {"command": payload}
    if not isinstance(payload, dict):
        raise CliUsageError("expected a command, a list of commands, or {command|commands, ...}")
    # The body's own session / wait, unless given on the command line.
    if args.session is None and isinstance(payload.get("session_id"), str):
        args.session = payload["session_id"]
    if isinstance(payload.get("wait_s"), (int, float)) and args.wait == DEFAULT_WAIT_S:
        args.wait = float(payload["wait_s"])
    payload = {k: v for k, v in payload.items() if k in ("command", "commands")}
    return _send(args, payload)


def cmd_schema(args: argparse.Namespace) -> int:
    _print_json(_with_viewer(args, lambda v, _scope: v.schema()))
    return 0


def _need_remote(args: argparse.Namespace, what: str) -> tuple[RemoteViewer, str]:
    if args.via == "local":
        raise CliUsageError(f"{what} reads the hosted viewer's stored clash data: it needs --via remote (or auto)")
    viewer, scope = _remote(args)
    return viewer, args.scope or scope


def cmd_joints(args: argparse.Namespace) -> int:
    viewer, scope = _need_remote(args, "ada viewer joints")
    params: dict = {"key": args.run, "limit": args.limit}
    if args.ids:
        params["ids"] = ",".join(args.ids)
    if args.member:
        params["member"] = args.member
    out = viewer.joints(scope, params)
    if args.json:
        _print_json(out)
        return 0
    run = out["run"]
    print(f"run {run['key']}  ({run['label']}, {run['contacts']} clashes, {run['created_at']})")
    for j in out["joints"]:
        members = ", ".join(f"{m['name']}" + (f" ({m['role']})" if m.get("role") else "") for m in j["members"])
        specs = ", ".join(a.get("spec", "") for a in j.get("applicable") or []) or "-"
        print(f"{j['id']}  {members}  at {j['centre']}  specs: {specs}")
    for missing in out.get("missing") or []:
        print(f"{missing}  not in this run", file=sys.stderr)
    if out.get("truncated"):
        print(f"(first {len(out['joints'])}; --limit for more)", file=sys.stderr)
    return 1 if out.get("missing") else 0


def cmd_detail(args: argparse.Namespace) -> int:
    viewer, scope = _need_remote(args, "ada viewer detail")
    params = {"joint_ids": ",".join(args.joints)} if args.joints else {}
    out = viewer.detail(scope, args.detail_id, params)
    if args.json:
        _print_json(out)
        return 0
    model = out["model"]
    print(f"{model['detail_id']}  {model.get('label')}  {model.get('status')}")
    for j in out["joints"]:
        line = f"{j['joint_id']}  {j['status']}"
        if j.get("not_built"):
            line += f"  {j['not_built'].get('reason')}"
        print(line)
        for conn in j.get("connections") or []:
            welds = conn.get("welds") or []
            print(f"    {conn['connection']}: {len(conn.get('plates') or [])} plates, {len(welds)} welds")
            for w in welds:
                print(
                    f"      {w.get('name')}  {w.get('weld_type')}  throat={w.get('throat')}  length={w.get('length')}"
                )
        for wf in j.get("weld_failures") or []:
            print(f"    weld failed: {wf.get('place')}  {wf.get('reason')}")
        contact = j.get("contact")
        if contact and j["status"] != "built":
            members = ", ".join(m["name"] + (f" ({m['role']})" if m.get("role") else "") for m in contact["members"])
            print(f"    members: {members}  at {contact['centre']}")
    return 0


# ── the parser ───────────────────────────────────────────────────────────────────────────────────


def _guarded(fn: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        try:
            return fn(args)
        except ViewerFailure as exc:
            print(f"ada viewer: {exc}", file=sys.stderr)
            return 1

    return run


def add_parser(sub: argparse._SubParsersAction) -> None:
    viewer = sub.add_parser(
        "viewer",
        help="Drive your open viewer (focus a joint, select, camera, open a detail model) and read clash results.",
        description=(
            "Drive your open viewer and read what the clash pipeline stored. Remote (a hosted viewer's REST API, "
            "with the URL/token of the other remote commands) when a viewer URL is configured, else the desktop "
            "viewer's websocket server; --via forces one."
        ),
    )
    vsub = viewer.add_subparsers(dest="viewer_command", required=True)

    def common(p: argparse.ArgumentParser, *, tab: bool = True) -> argparse.ArgumentParser:
        p.add_argument("--via", choices=("auto", "remote", "local"), default="auto", help="Transport (default auto).")
        p.add_argument("--url", default=None, help="Hosted viewer URL (default: $ADAPY_API_BASE).")
        p.add_argument("--token", default=None, help="Bearer token (default: $ADAPY_API_TOKEN).")
        p.add_argument("--ws-host", default=DEFAULT_WS_HOST, help=f"Local websocket server host ({DEFAULT_WS_HOST}).")
        p.add_argument(
            "--ws-port", type=int, default=DEFAULT_WS_PORT, help=f"Local websocket server port ({DEFAULT_WS_PORT})."
        )
        p.add_argument("--json", action="store_true", help="Print the raw answer as JSON.")
        p.add_argument(
            "--wait", type=float, default=DEFAULT_WAIT_S, help="Seconds to wait for the answers (default 10)."
        )
        if tab:
            p.add_argument(
                "--session", default=None, help="One tab (a session id; locally a viewer's instance id). Default: all."
            )
        return p

    p = common(vsub.add_parser("sessions", help="List your open viewer tabs."), tab=False)
    p.set_defaults(func=_guarded(cmd_sessions))

    p = common(vsub.add_parser("state", help="What your viewer tabs show: models, selection, camera, clash run."))
    p.set_defaults(func=_guarded(cmd_state))

    p = common(vsub.add_parser("focus-joint", help="Focus a clash joint: select its members and frame them."))
    p.add_argument("joint_id")
    p.add_argument("--run-key", default=None, help="Open this convex-contact run first.")
    p.add_argument("--scope", default=None, help="The scope of --run-key (default: the tab's).")
    p.add_argument("--no-frame", action="store_true", help="Leave the camera where it is.")
    p.set_defaults(func=_guarded(cmd_focus_joint))

    p = common(vsub.add_parser("select", help="Select objects by name in a loaded model and frame them."))
    p.add_argument("names", nargs="+")
    p.add_argument("--file", default=None, help="The loaded model (default: the active one).")
    p.add_argument("--no-frame", action="store_true", help="Leave the camera where it is.")
    p.set_defaults(func=_guarded(cmd_select))

    p = common(vsub.add_parser("clear-selection", help="Clear the selection."))
    p.set_defaults(func=_guarded(cmd_clear_selection))

    p = common(vsub.add_parser("camera", help="Move the camera: one of --fit, --view, --eye with --target, --box."))
    move = p.add_mutually_exclusive_group(required=True)
    move.add_argument("--fit", choices=("selection", "all"))
    move.add_argument("--view", choices=("top", "bottom", "front", "back", "left", "right", "isometric"))
    move.add_argument("--eye", default=None, help="x,y,z (with --target).")
    move.add_argument("--box", default=None, help="xmin,ymin,zmin,xmax,ymax,zmax: frame this box.")
    p.add_argument("--target", dest="look_at", default=None, help="x,y,z the camera looks at (with --eye).")
    p.set_defaults(func=_guarded(cmd_camera))

    p = common(vsub.add_parser("open-detail", help="Load a detail model; with --joint, show that joint."))
    p.add_argument("detail_id")
    p.add_argument("--joint", default=None, help="A joint to show in it (or in its run, when not built).")
    p.add_argument("--scope", default=None, help="The model's scope (default: the tab's).")
    p.set_defaults(func=_guarded(cmd_open_detail))

    p = common(vsub.add_parser("find", help="Look objects up by name in the loaded models (centre, box)."))
    p.add_argument("names", nargs="+")
    p.add_argument("--file", default=None, help="Look in this loaded model only.")
    p.set_defaults(func=_guarded(cmd_find))

    p = common(vsub.add_parser("run", help="Send a command or a batch from a JSON file (- for stdin)."))
    p.add_argument("file", help="A command, a list of commands, or a POST /api/viewer/commands body.")
    p.set_defaults(func=_guarded(cmd_run))

    p = common(vsub.add_parser("schema", help="The command vocabulary as JSON schema."), tab=False)
    p.set_defaults(func=_guarded(cmd_schema))

    p = common(
        vsub.add_parser("joints", help="A clash run's joints: members, roles, centre, specs (hosted)."), tab=False
    )
    p.add_argument("--run", default="latest", help="A run key, or latest (default).")
    p.add_argument("--ids", nargs="+", default=None, help="Only these joint ids.")
    p.add_argument("--member", default=None, help="Only joints with a member of this name.")
    p.add_argument("--limit", type=int, default=100, help="At most this many (default 100).")
    p.add_argument("--scope", default=None, help="Scope (default: $ADAPY_API_SCOPE or user:me).")
    p.set_defaults(func=_guarded(cmd_joints))

    p = common(
        vsub.add_parser("detail", help="A detail model per joint: built, declined, its welds (hosted)."), tab=False
    )
    p.add_argument("detail_id")
    p.add_argument("--joints", nargs="+", default=None, help="Only these joint ids.")
    p.add_argument("--scope", default=None, help="Scope (default: $ADAPY_API_SCOPE or user:me).")
    p.set_defaults(func=_guarded(cmd_detail))
