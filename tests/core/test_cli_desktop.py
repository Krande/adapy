"""``ada desktop``'s pure parts: the port policy, finding the shell, unpacking the viewer.

Nothing here starts a server or a window.
"""

from __future__ import annotations

import argparse
import pathlib
import socket
import zipfile

import pytest

from ada_cli import CliUsageError
from ada_cli import desktop as d

# ── the port ──────────────────────────────────────────────────────────────


def test_default_port_is_fixed_per_user_and_in_range():
    a = d.default_port("alice")
    assert a == d.default_port("alice") == d.default_port("ALICE")
    assert d.DEFAULT_PORT_BASE <= a < d.DEFAULT_PORT_BASE + d.DEFAULT_PORT_SPAN
    # Not one constant for everyone: two users on one machine should not usually collide.
    assert len({d.default_port(u) for u in ("alice", "bob", "carol", "dave", "erin")}) > 1


def test_requested_port_prefers_cli_then_env_then_default():
    assert d.requested_port(4000, {d.ENV_PORT: "5000"}) == (4000, "--port")
    assert d.requested_port(None, {d.ENV_PORT: " 5000 "}) == (5000, f"${d.ENV_PORT}")
    port, source = d.requested_port(None, {})
    assert source == "default" and port == d.default_port()


@pytest.mark.parametrize("env, cli", [({d.ENV_PORT: "abc"}, None), ({}, 0), ({}, 70000), ({d.ENV_PORT: "0"}, None)])
def test_requested_port_rejects_nonsense(env, cli):
    with pytest.raises(CliUsageError):
        d.requested_port(cli, env)


def test_choose_port_uses_a_free_port_as_is():
    assert d.choose_port(38600, probe=lambda p: "free", free=lambda: 1) == d.PortChoice(38600)


def test_choose_port_attaches_to_our_own_viewer():
    assert d.choose_port(38600, probe=lambda p: "ours", free=lambda: 1) == d.PortChoice(38600, attach=True)


def test_choose_port_falls_back_and_says_why_when_taken():
    choice = d.choose_port(38600, probe=lambda p: "taken", free=lambda: 51234)
    assert choice == d.PortChoice(51234, fallback_from=38600)
    msg = d.fallback_warning(choice, "default")
    assert "38600" in msg and "51234" in msg and "settings" in msg


def test_own_viewer_signature():
    ours = {"transport": "rest", "apiBase": "/api", "localShutdown": {"available": True, "token": "t"}}
    assert d.is_own_viewer(ours)
    # adapy's API without the launcher's shutdown route (a plain `ada serve api`, a deployment).
    assert not d.is_own_viewer({**ours, "localShutdown": {"available": False}})
    assert not d.is_own_viewer({"transport": "rest", "apiBase": "/api"})
    assert not d.is_own_viewer({**ours, "transport": "ws"})
    assert not d.is_own_viewer(["not", "a", "dict"])


def test_probe_port_free_and_taken_by_a_non_http_listener():
    with socket.socket() as s:
        s.bind((d.LOCAL_HOST, 0))
        port = s.getsockname()[1]
        s.listen(1)
        # Something listens but does not answer /api/config like our viewer: taken, never "ours".
        assert d.probe_port(port) == "taken"
    assert d.probe_port(d.free_port()) == "free"


# ── the shell binary ──────────────────────────────────────────────────────


def test_binary_lookup_order(tmp_path):
    cands = d.binary_candidates(
        "x/explicit.exe",
        environ={d.ENV_BINARY: "y/env.exe"},
        prefix=tmp_path,
        which=lambda name: "z/on-path.exe",
        windows=True,
    )
    assert cands[:3] == [pathlib.Path(p) for p in ("x/explicit.exe", "y/env.exe", "z/on-path.exe")]
    assert cands[3] == tmp_path / "Library" / "bin" / "ada-desktop.exe"
    assert cands[-1] == tmp_path / "bin" / "ada-desktop"


def test_find_binary_in_the_environment(tmp_path):
    exe = tmp_path / "Library" / "bin" / "ada-desktop.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert d.find_binary(None, environ={}, prefix=tmp_path, which=lambda n: None, windows=True) == exe


def test_missing_binary_gives_the_install_hint(tmp_path):
    with pytest.raises(d.DesktopError, match="conda install"):
        d.find_binary(None, environ={}, prefix=tmp_path, which=lambda n: None, windows=True)


def test_explicit_binary_must_exist(tmp_path):
    with pytest.raises(CliUsageError):
        d.find_binary(str(tmp_path / "nope.exe"), environ={}, prefix=tmp_path, which=lambda n: None)


# ── the viewer build ──────────────────────────────────────────────────────


def _zip(path, html="<html></html>"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("index.html", html)
    return path


def test_static_dir_unpacks_once_per_content(tmp_path):
    cache = tmp_path / "cache"
    z = _zip(tmp_path / "index.zip")
    out = d.static_dir(z, cache)
    assert out == cache / "spa" / d.zip_key(z)
    assert (out / "index.html").read_text() == "<html></html>"
    (out / "marker").write_text("kept")
    assert d.static_dir(z, cache) == out and (out / "marker").is_file()  # reused, not re-extracted
    # New content, new directory; the old one stays for a viewer that may still serve it.
    out2 = d.static_dir(_zip(tmp_path / "index.zip", "<html>2</html>"), cache)
    assert out2 != out and out.is_dir()
    assert not list((cache / "spa").glob("*.tmp-*"))


def test_static_dir_refuses_a_zip_without_index_html(tmp_path):
    z = tmp_path / "other.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("sub/index.html", "")
    with pytest.raises(d.DesktopError):
        d.static_dir(z, tmp_path / "cache")


def test_resolve_static_defers_to_an_installed_bundle(tmp_path):
    args = argparse.Namespace(static=None, zip=None)
    assert d.resolve_static(args, {"ADA_VIEWER_BUNDLE": "plugins"}) is None
    assert d.resolve_static(args, {"ADA_VIEWER_STATIC_PATH": str(tmp_path)}) is None


def test_platform_dirs():
    cache, data = d._platform_dirs({"LOCALAPPDATA": "C:/L"}, "win32")
    assert cache.as_posix() == "C:/L/ada/desktop/cache" and data.as_posix() == "C:/L/ada/desktop/data"
    cache, data = d._platform_dirs({"HOME": "/h", "XDG_CACHE_HOME": "/c"}, "linux")
    assert cache.as_posix() == "/c/ada/desktop" and data.as_posix() == "/h/.local/share/ada/desktop"


def test_viewer_url_opens_a_file_from_the_personal_scope():
    base = "http://127.0.0.1:38600/"
    assert d.viewer_url(base) == base
    assert d.viewer_url(base, "my model.ifc") == base + "?scope=user%3Ame&file=my+model.ifc"


def test_live_clients_prunes_dead_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "cache_root", lambda *a, **k: tmp_path)
    clients = tmp_path / "clients" / "38600"
    clients.mkdir(parents=True)
    for pid in ("111", "222", "junk"):
        (clients / pid).touch()
    assert d.live_clients(38600, alive=lambda pid: pid == 222) == [222]
    assert sorted(p.name for p in clients.iterdir()) == ["222", "junk"]


# ── the parser ────────────────────────────────────────────────────────────


def test_parser_surface():
    from ada_cli.main import _build_parser

    args = _build_parser().parse_args(
        ["desktop", "m.ifc", "--port", "40000", "--no-window", "--shell-arg=--no-spacemouse"]
    )
    assert (args.words, args.port, args.no_window, args.shell_arg) == (["m.ifc"], 40000, True, ["--no-spacemouse"])
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["desktop", "--static", "a", "--zip", "b"])


def test_parser_dev_flags():
    from ada_cli.main import _build_parser

    a = _build_parser().parse_args(["desktop", "--dev", "--dev-port", "40001"])
    assert (a.dev, a.dev_port, a.frontend_url) == (True, 40001, None)
    a = _build_parser().parse_args(["desktop", "--frontend-url", "http://localhost:5999"])
    assert a.frontend_url == "http://localhost:5999" and not a.dev


def test_dev_vite_port_follows_the_viewer_port():
    assert d.dev_vite_port(38500) == 38500 + d.DEV_VITE_PORT_OFFSET


def test_frontend_url_is_loopback_http_only():
    assert d.frontend_url_or_error("http://localhost:5999/x") == "http://localhost:5999/"
    assert d.frontend_url_or_error("http://127.0.0.1:1") == "http://127.0.0.1:1/"
    for bad in ("https://127.0.0.1:1", "http://example.com:80", "127.0.0.1:5"):
        with pytest.raises(CliUsageError):
            d.frontend_url_or_error(bad)


def test_vite_command_prefers_node_then_pixi_then_fails(tmp_path):
    node = d.vite_command(tmp_path, 39000, "node", "pixi")
    assert node[0] == "node" and node[1].endswith("vite.js") and "39000" in node and "--strictPort" in node
    assert node[node.index("--config") + 1] == d.DEV_VITE_CONFIG
    assert d.vite_command(tmp_path, 1, None, "pixi")[:5] == ["pixi", "run", "-e", "frontend", "node"]
    with pytest.raises(d.DesktopError):
        d.vite_command(tmp_path, 1, None, None)


def test_backend_dev_command_reloads_the_given_directory(tmp_path):
    cmd = d.backend_dev_command("py", 38600, tmp_path)
    assert cmd[:4] == ["py", "-m", "uvicorn", "ada.comms.rest.app:app"]
    assert cmd[cmd.index("--port") + 1] == "38600" and "--reload" in cmd
    assert cmd[cmd.index("--reload-dir") + 1] == str(tmp_path)


def test_dev_shell_binary_is_the_checkout_build(tmp_path):
    assert d.dev_shell_binary(tmp_path) is None
    rel = tmp_path / "desktop" / "target" / "release"
    rel.mkdir(parents=True)
    exe = rel / (d.BINARY_NAME + (".exe" if d.os.name == "nt" else ""))
    exe.write_bytes(b"")
    assert d.dev_shell_binary(tmp_path) == exe


# -- remote mode: --server, saved profiles ------------------------------------------------------


def _ns(**kw):
    base = dict(
        server=None,
        local=False,
        insecure=False,
        auth_origin=[],
        words=[],
        no_window=True,
        binary=None,
        title="T",
        shell_arg=[],
        timing=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_normalize_origin_keeps_only_the_origin():
    assert d.normalize_origin("https://studio.example.com/app/x?y=1#z") == "https://studio.example.com/"
    assert d.normalize_origin("HTTPS://Studio.Example.com:8443") == "https://studio.example.com:8443/"
    assert d.normalize_origin("http://localhost:5173/") == "http://localhost:5173/"
    assert d.normalize_origin("http://[::1]:5173/") == "http://[::1]:5173/"


@pytest.mark.parametrize(
    "bad",
    [
        "http://studio.example.com/",
        "studio.example.com",
        "ftp://studio.example.com",
        "https://u:p@studio.example.com",
        "https://studio.example.com:99999",
        "https://",
    ],
)
def test_normalize_origin_rejects(bad):
    with pytest.raises(CliUsageError):
        d.normalize_origin(bad)


def test_plain_http_to_a_remote_host_needs_insecure():
    with pytest.raises(CliUsageError, match="--insecure"):
        d.normalize_origin("http://lab.example.com:8080")
    assert d.normalize_origin("http://lab.example.com:8080", insecure=True) == "http://lab.example.com:8080/"


def test_config_root_per_platform(tmp_path):
    assert d.config_root({"APPDATA": "C:/R", "USERPROFILE": "C:/U"}, "win32").as_posix() == "C:/R/ada/desktop"
    assert d.config_root({"HOME": "/h"}, "linux").as_posix() == "/h/.config/ada/desktop"
    assert d.config_root({"HOME": "/h", "XDG_CONFIG_HOME": "/x"}, "linux").as_posix() == "/x/ada/desktop"


def test_profiles_round_trip(tmp_path):
    f = tmp_path / "sub" / "servers.json"
    assert d.load_servers(f) == {}
    a = d.make_profile("prod", "https://studio.example.com/anything")
    b = d.make_profile("lab", "http://lab.example.com:8080", insecure=True, auth_origins=["https://idp.example.org/x"])
    d.save_servers({"prod": a, "lab": b}, f)
    back = d.load_servers(f)
    assert back == {"prod": a, "lab": b}
    assert back["lab"].auth_origins == ("https://idp.example.org/",) and back["lab"].insecure
    assert not (tmp_path / "sub" / "servers.json.tmp").exists()


def test_damaged_profile_file_is_an_error_not_silently_replaced(tmp_path):
    f = tmp_path / "servers.json"
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(d.DesktopError, match="not a valid"):
        d.load_servers(f)
    f.write_text('{"servers": {"a": {"nourl": 1}}}', encoding="utf-8")
    with pytest.raises(d.DesktopError):
        d.load_servers(f)


@pytest.mark.parametrize("name", ["", "a b", "https://x.example.com", "-x", "a/b"])
def test_profile_names_are_plain_words(name):
    with pytest.raises(CliUsageError):
        d.make_profile(name, "https://studio.example.com")


def test_resolve_server_precedence():
    prof = {"prod": d.make_profile("prod", "https://studio.example.com")}
    # nothing asked for: the local viewer
    assert d.resolve_server(_ns(), {}, prof) is None
    # a URL, a profile name, the environment
    assert d.resolve_server(_ns(server="https://a.example.com/x"), {}, prof).url == "https://a.example.com/"
    s = d.resolve_server(_ns(server="prod"), {}, prof)
    assert s.url == "https://studio.example.com/" and s.name == "prod"
    assert d.resolve_server(_ns(), {d.ENV_SERVER: "prod"}, prof).name == "prod"
    # --server beats the environment, --local beats both
    assert d.resolve_server(_ns(server="https://a.example.com"), {d.ENV_SERVER: "prod"}, prof).name is None
    assert d.resolve_server(_ns(local=True), {d.ENV_SERVER: "prod"}, prof) is None
    with pytest.raises(CliUsageError):
        d.resolve_server(_ns(local=True, server="prod"), {}, prof)


def test_resolve_server_unknown_name_lists_the_known_ones():
    prof = {"prod": d.make_profile("prod", "https://studio.example.com")}
    with pytest.raises(CliUsageError, match="prod"):
        d.resolve_server(_ns(server="staging"), {}, prof)
    with pytest.raises(CliUsageError, match="https"):
        d.resolve_server(_ns(server="http://lab.example.com"), {}, prof)


def test_resolve_server_merges_flags_into_the_profile():
    prof = {
        "lab": d.make_profile("lab", "http://lab.example.com", insecure=True, auth_origins=["https://a.example.org"])
    }
    s = d.resolve_server(_ns(server="lab", auth_origin=["https://b.example.org/p"]), {}, prof)
    assert s.insecure and s.auth_origins == ("https://a.example.org/", "https://b.example.org/")


def test_auth_origins_come_from_config_and_discovery():
    cfg = {"auth": {"enabled": True, "issuer": "https://login.example.org/realm/x"}}
    disco = {"authorization_endpoint": "https://sso.example.org/authorize", "end_session_endpoint": 5}
    assert d.auth_origins_from(cfg, disco) == ["https://login.example.org/", "https://sso.example.org/"]
    assert d.auth_origins_from(cfg) == ["https://login.example.org/"]
    assert d.auth_origins_from({"auth": {"enabled": False, "issuer": "https://x.example.org"}}) == []
    assert d.auth_origins_from({"transport": "rest"}) == [] and d.auth_origins_from(None) == []


def test_discover_auth_origins_is_best_effort():
    docs = {
        "https://s.example.com/api/config": {"auth": {"enabled": True, "issuer": "https://idp.example.org/r/"}},
        "https://idp.example.org/r/.well-known/openid-configuration": {
            "authorization_endpoint": "https://idp.example.org/r/auth"
        },
    }
    assert d.discover_auth_origins("https://s.example.com/", lambda u: docs[u]) == ["https://idp.example.org/"]

    def down(url):
        raise OSError("unreachable")

    warned = []
    assert d.discover_auth_origins("https://s.example.com/", down, warned.append) == [] and "--auth-origin" in warned[0]


def test_shell_command_remote_trusts_exactly_the_server():
    s = d.Server(url="http://lab.example.com:8080/", insecure=True)
    cmd = d.shell_command_remote(
        pathlib.Path("ada-desktop.exe"), s, ["https://idp.example.org/"], "T", ["--no-spacemouse"]
    )
    assert cmd[cmd.index("--url") + 1] == cmd[cmd.index("--trust-origin") + 1] == "http://lab.example.com:8080/"
    assert cmd[cmd.index("--auth-origin") + 1] == "https://idp.example.org/"
    assert "--insecure" in cmd and cmd[-1] == "--no-spacemouse"
    https = d.shell_command_remote(pathlib.Path("x"), d.Server(url="https://s.example.com/"), [], "T", [])
    assert "--insecure" not in https and "--auth-origin" not in https


def test_remote_mode_refuses_local_only_flags_and_files():
    s = d.Server(url="https://s.example.com/")
    with pytest.raises(CliUsageError, match="--port"):
        d.run_remote(_ns(port=40000), s)
    with pytest.raises(CliUsageError, match="FILE"):
        d.run_remote(_ns(words=["m.ifc"]), s)


def test_servers_command_add_list_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(d, "servers_path", lambda *a, **k: tmp_path / "servers.json")
    ns = _ns()
    assert d.run_servers(["add", "prod", "https://studio.example.com/x"], ns) == 0
    assert d.run_servers(["list"], ns) == 0
    assert "prod\thttps://studio.example.com/" in capsys.readouterr().out
    assert d.run_servers(["remove", "prod"], ns) == 0
    with pytest.raises(CliUsageError):
        d.run_servers(["remove", "prod"], ns)
    with pytest.raises(CliUsageError):
        d.run_servers(["add", "x"], ns)


def test_parser_server_flags_and_servers_words():
    from ada_cli.main import _build_parser

    a = _build_parser().parse_args(
        ["desktop", "--server", "prod", "--insecure", "--auth-origin", "https://a.example.org"]
    )
    assert (a.server, a.insecure, a.auth_origin, a.words) == ("prod", True, ["https://a.example.org"], [])
    a = _build_parser().parse_args(["desktop", "servers", "add", "prod", "https://studio.example.com"])
    assert a.words == ["servers", "add", "prod", "https://studio.example.com"]


# -- dev frontend against a hosted backend: --dev --backend ---------------------------------------


def test_parser_backend_flag():
    from ada_cli.main import _build_parser

    a = _build_parser().parse_args(["desktop", "--backend", "work"])
    assert a.backend == "work" and not a.dev


def test_local_only_flag_error_has_the_right_grammar():
    with pytest.raises(CliUsageError, match=r"--port only applies to the local viewer"):
        d.run_remote(_ns(port=40000), d.Server(url="https://s.example.com/"))
    with pytest.raises(CliUsageError, match=r"--port, --data only apply to"):
        d.refuse_local_only(_ns(port=1, data="x"), ("port", "data"))


def test_window_host_is_localhost_and_redirect_uri_follows_it():
    front = d.dev_window_url(39198)
    assert front == "http://localhost:39198/"
    assert d.redirect_uri_for(front) == "http://localhost:39198/auth/callback"


def test_backend_dev_port_is_stable_per_user():
    port = d.dev_vite_port(d.requested_port(None, {})[0])
    assert port == d.default_port() + d.DEV_VITE_PORT_OFFSET == d.dev_vite_port(d.default_port())


def test_shell_command_dev_backend_trusts_only_the_localhost_origin():
    s = d.Server(url="https://s.example.com/")
    cmd = d.shell_command_dev_backend(
        pathlib.Path("ada-desktop.exe"), "http://localhost:39198/", s, ["https://idp.example.org/"], "T", ["--x"]
    )
    assert cmd[cmd.index("--url") + 1] == cmd[cmd.index("--trust-origin") + 1] == "http://localhost:39198/"
    assert "https://s.example.com/" not in cmd
    assert cmd[cmd.index("--auth-origin") + 1] == "https://idp.example.org/" and "--insecure" not in cmd
    assert cmd[-1] == "--x"


def test_entra_is_recognised_from_the_discovered_origins():
    assert d.is_entra(["https://login.microsoftonline.com/"])
    assert d.is_entra(["https://idp.example.org/", "https://login.microsoftonline.us/"])
    assert not d.is_entra(["https://idp.example.org/", "http://login.microsoftonline.com/"])
    assert not d.is_entra(["https://login.microsoftonline.com.example.net/"]) and not d.is_entra([])


def test_entra_discovery_yields_the_login_host():
    tenant = "00000000-0000-0000-0000-000000000000"
    issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
    docs = {
        "https://s.example.com/api/config": {"auth": {"enabled": True, "issuer": issuer}},
        f"{issuer}/.well-known/openid-configuration": {
            "authorization_endpoint": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            "end_session_endpoint": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/logout",
        },
    }
    found = d.discover_auth_origins("https://s.example.com/", lambda u: docs[u])
    assert found == ["https://login.microsoftonline.com/"] and d.is_entra(found)


def test_no_sso_is_passed_to_the_shell():
    from ada_cli.main import _build_parser

    a = _build_parser().parse_args(["desktop", "--server", "work", "--no-sso", "--shell-arg=--no-spacemouse"])
    assert a.no_sso and d.shell_args(a) == ["--no-spacemouse", "--no-sso"]
    assert d.shell_args(_build_parser().parse_args(["desktop"])) == []
    cmd = d.shell_command_remote(pathlib.Path("x"), d.Server(url="https://s.example.com/"), [], "T", d.shell_args(a))
    assert cmd[-1] == "--no-sso"


def test_print_sign_in_mentions_entra_and_sso(capsys):
    d.print_sign_in(["https://login.microsoftonline.com/"], _ns())
    out = capsys.readouterr().out
    assert "https://login.microsoftonline.com" in out and "Entra ID" in out and "Windows account: on" in out
    d.print_sign_in(["https://login.microsoftonline.com/"], _ns(no_sso=True))
    assert "Windows account: off" in capsys.readouterr().out
    d.print_sign_in(["https://idp.example.org/"], _ns())
    assert "Entra" not in capsys.readouterr().out


def test_collect_auth_origins_filters_plain_http_and_the_server_itself():
    s = d.Server(url="https://s.example.com/", auth_origins=("https://extra.example.org/",))

    def found(_url):
        return ["https://idp.example.org/", "https://s.example.com/", "http://plain.example.org/"]

    assert d.collect_auth_origins(s, found) == ["https://idp.example.org/", "https://extra.example.org/"]


def _dispatch(monkeypatch, args, profiles=None, environ=None):
    """Run :func:`d.run` with the three runners stubbed; returns which one ran and with what."""
    calls = []
    monkeypatch.setattr(d, "run_dev", lambda a: calls.append(("dev",)) or 0)
    monkeypatch.setattr(d, "run_dev_backend", lambda a, s: calls.append(("backend", s.url)) or 0)
    monkeypatch.setattr(d, "run_remote", lambda a, s: calls.append(("remote", s.url)) or 0)
    monkeypatch.setattr(d, "load_servers", lambda *a: profiles or {})
    for k in (d.ENV_SERVER,):
        monkeypatch.delenv(k, raising=False)
    for k, v in (environ or {}).items():
        monkeypatch.setenv(k, v)
    d.run(args)
    return calls


def _full(**kw):
    return _ns(**{**dict(dev=False, frontend_url=None, dev_port=None, backend=None), **kw})


def test_dev_with_server_is_an_alias_for_backend(monkeypatch):
    prof = {"work": d.Server(url="https://s.example.com/", name="work")}
    for kw in (dict(server="work"), dict(backend="work")):
        assert _dispatch(monkeypatch, _full(dev=True, **kw), prof) == [("backend", "https://s.example.com/")]
    # --backend alone implies --dev; --server alone stays plain remote mode
    assert _dispatch(monkeypatch, _full(backend="https://b.example.com"), prof) == [
        ("backend", "https://b.example.com/")
    ]
    assert _dispatch(monkeypatch, _full(server="work"), prof) == [("remote", "https://s.example.com/")]


def test_plain_dev_stays_local_even_when_a_server_is_in_the_environment(monkeypatch):
    calls = _dispatch(monkeypatch, _full(dev=True), environ={d.ENV_SERVER: "https://s.example.com"})
    assert calls == [("dev",)]


def test_backend_and_server_exclude_each_other(monkeypatch):
    with pytest.raises(CliUsageError, match="exclude each other"):
        _dispatch(monkeypatch, _full(backend="https://a.example.com", server="https://b.example.com"))
    with pytest.raises(CliUsageError, match="--local and --backend"):
        _dispatch(monkeypatch, _full(backend="https://a.example.com", local=True))


def test_backend_url_must_be_https_unless_loopback(monkeypatch):
    with pytest.raises(CliUsageError, match="plain http"):
        _dispatch(monkeypatch, _full(backend="http://lab.example.com"))


def test_run_dev_backend_prints_the_redirect_uri_and_never_starts_a_viewer(monkeypatch, capsys):
    monkeypatch.setattr(d, "probe_port", lambda p, host=d.LOCAL_HOST: "free")
    monkeypatch.setattr(d, "discover_auth_origins", lambda url: ["https://idp.example.org/"])
    started = []
    monkeypatch.setattr(
        d.subprocess, "Popen", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(KeyboardInterrupt)
    )
    monkeypatch.setattr(d, "vite_command", lambda *a: ["node", "vite"])
    args = _full(port=None, dev=True, dev_port=39198, viewer=None, no_window=True)
    assert d.run_dev_backend(args, d.Server(url="https://s.example.com/", name="work")) == 130
    out = capsys.readouterr().out
    assert "Register this redirect URI for the app's SPA platform: http://localhost:39198/auth/callback" in out
    assert "https://idp.example.org" in out
    assert len(started) == 1 and started[0][0] == ["node", "vite"]  # Vite only: no uvicorn viewer


def test_run_dev_backend_refuses_a_busy_fixed_port(monkeypatch):
    monkeypatch.setattr(d, "probe_port", lambda p, host=d.LOCAL_HOST: "taken")
    monkeypatch.setattr(d, "discover_auth_origins", lambda url: [])
    with pytest.raises(d.DesktopError, match="39198 is in use"):
        d.run_dev_backend(_full(port=None, dev_port=39198, no_window=True), d.Server(url="https://s.example.com/"))


def test_run_dev_backend_refuses_local_only_flags_and_files():
    s = d.Server(url="https://s.example.com/")
    with pytest.raises(CliUsageError, match="--data only applies"):
        d.run_dev_backend(_full(data="x"), s)
    with pytest.raises(CliUsageError, match="FILE"):
        d.run_dev_backend(_full(words=["m.ifc"]), s)


# config.js rewriting lives in the Vite helper (plain .mjs): exercised with node when it is installed.

_FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "src" / "frontend"


def _rewrite(text, backend, dev):
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (
        "import {rewriteConfigJs as r} from './vite.desktop-proxy.mjs';"
        "const [t,b,d]=JSON.parse(process.argv[1]);process.stdout.write(r(t,b,d));"
    )
    return subprocess.run(
        [node, "--input-type=module", "-e", script, json.dumps([text, backend, dev])],
        cwd=_FRONTEND,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_config_js_rewrite_points_absolute_server_addresses_at_the_dev_origin():
    body = (
        'window.API_BASE = "https://s.example.com/api";\n'
        'window.WS_URL = "wss://s.example.com/api/ws";\n'
        'window.AUTH_ISSUER = "https://idp.example.org/tenant";\n'
    )
    out = _rewrite(body, "https://s.example.com/", "http://localhost:39198")
    assert 'window.API_BASE = "http://localhost:39198/api";' in out
    assert 'window.WS_URL = "ws://localhost:39198/api/ws";' in out
    assert 'AUTH_ISSUER = "https://idp.example.org/tenant"' in out  # the identity provider is untouched


def test_config_js_rewrite_leaves_the_stock_relative_config_alone():
    body = 'window.COMMS_MODE = "rest";\nwindow.API_BASE = "/api";\n'
    assert _rewrite(body, "https://s.example.com/", "http://localhost:39198") == body
