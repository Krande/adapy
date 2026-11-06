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
    assert (args.file, args.port, args.no_window, args.shell_arg) == ("m.ifc", 40000, True, ["--no-spacemouse"])
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["desktop", "--static", "a", "--zip", "b"])
