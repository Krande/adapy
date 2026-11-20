"""The committed viewer bundle carries a stamp of its inputs, and ``check`` fails once they move."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).parents[3]
_SCRIPT = _REPO / "scripts" / "packaging" / "bundle_inputs.py"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="no repo tree (sdist/wheel test env)")

_DOCKERFILE = "ARG ADACPP_BASE_IMAGE=example/base:1\nARG ADACPP_RENDER_REF=abc123\nARG ADACPP_GIT_URL=https://x/y.git\n"


@pytest.fixture(scope="module")
def bi():
    spec = importlib.util.spec_from_file_location("bundle_inputs", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _put(root: pathlib.Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode())


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _put(root, "deploy/Dockerfile.viewer", _DOCKERFILE)
    _put(root, "src/frontend/src/App.tsx", "export const A = 1;\n")
    _put(root, "src/frontend/src/index.html", "<html></html>\n")
    _put(root, "src/frontend/src/__tests__/App.test.ts", "test\n")
    _put(root, "src/frontend/src/util.test.ts", "test\n")
    _put(root, "src/frontend/frontend.md", "docs\n")
    _put(root, "src/frontend/vite.config.embed.ts", "embed\n")
    _put(root, "src/frontend/package.json", json.dumps({"name": "v", "version": "1.0.0", "dependencies": {"a": "^1"}}))
    _put(
        root,
        "src/frontend/package-lock.json",
        json.dumps({"name": "v", "version": "1.0.0", "packages": {"": {"version": "1.0.0"}, "node_modules/a": {"version": "1.2.3"}}}),
    )
    _put(root, "src/ada/visit/rendering/resources/index.zip", "zip")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def _stale(bi, root) -> bool:
    return bi.check(root) != 0


def test_a_fresh_stamp_passes(bi, repo):
    assert bi.write(repo) == 0
    assert bi.check(repo) == 0


def test_no_stamp_fails(bi, repo, capsys):
    assert bi.check(repo) == 1
    assert "no stamp" in capsys.readouterr().err


def test_hashing_is_deterministic(bi, repo):
    first = bi.digest(bi.collect(repo))
    assert first == bi.digest(bi.collect(repo))
    # the digest is over sorted names, so the order files were created in cannot matter
    ids = bi.collect(repo)
    assert bi.digest(dict(reversed(list(ids.items())))) == bi.digest(ids)


@pytest.mark.parametrize(
    "rel",
    [
        "src/frontend/src/__tests__/App.test.ts",
        "src/frontend/src/util.test.ts",
        "src/frontend/frontend.md",
        "src/frontend/vite.config.embed.ts",
    ],
)
def test_a_change_outside_the_build_does_not_make_it_stale(bi, repo, rel):
    bi.write(repo)
    _put(repo, rel, "changed\n")
    assert bi.check(repo) == 0


@pytest.mark.parametrize("rel", ["src/frontend/src/App.tsx", "src/frontend/src/index.html"])
def test_a_source_change_makes_it_stale_and_names_the_file(bi, repo, rel, capsys):
    bi.write(repo)
    _put(repo, rel, "changed\n")
    assert bi.check(repo) == 1
    err = capsys.readouterr().err
    assert "the viewer bundle is stale" in err
    assert "pixi run wbuild" in err
    assert f"changed  {rel}" in err


def test_a_new_input_makes_it_stale(bi, repo, capsys):
    bi.write(repo)
    _put(repo, "src/frontend/src/New.tsx", "x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert bi.check(repo) == 1
    assert "added    src/frontend/src/New.tsx" in capsys.readouterr().err


def test_an_untracked_file_is_not_an_input(bi, repo):
    bi.write(repo)
    _put(repo, "src/frontend/src/Scratch.tsx", "x\n")
    assert bi.check(repo) == 0


def test_a_dockerfile_pin_change_makes_it_stale(bi, repo, capsys):
    bi.write(repo)
    _put(repo, "deploy/Dockerfile.viewer", _DOCKERFILE.replace("abc123", "def456"))
    assert bi.check(repo) == 1
    assert "pin:ADACPP_RENDER_REF" in capsys.readouterr().err


def test_other_dockerfile_changes_do_not(bi, repo):
    bi.write(repo)
    _put(repo, "deploy/Dockerfile.viewer", _DOCKERFILE + "RUN echo hello\n")
    assert bi.check(repo) == 0


def test_a_release_version_bump_does_not_make_it_stale(bi, repo):
    bi.write(repo)
    _put(repo, "src/frontend/package.json", json.dumps({"name": "v", "version": "2.0.0", "dependencies": {"a": "^1"}}))
    _put(
        repo,
        "src/frontend/package-lock.json",
        json.dumps({"name": "v", "version": "2.0.0", "packages": {"": {"version": "2.0.0"}, "node_modules/a": {"version": "1.2.3"}}}),
    )
    assert bi.check(repo) == 0


def test_a_dependency_change_does_make_it_stale(bi, repo):
    bi.write(repo)
    _put(repo, "src/frontend/package.json", json.dumps({"name": "v", "version": "1.0.0", "dependencies": {"a": "^2"}}))
    assert bi.check(repo) == 1
    bi.write(repo)
    _put(
        repo,
        "src/frontend/package-lock.json",
        json.dumps({"name": "v", "version": "1.0.0", "packages": {"": {"version": "1.0.0"}, "node_modules/a": {"version": "1.2.4"}}}),
    )
    assert bi.check(repo) == 1


def test_a_hand_edited_stamp_fails(bi, repo, capsys):
    bi.write(repo)
    stamp = repo / bi.STAMP
    stamp.write_text(stamp.read_text().replace(bi.HASH_LINE + " ", bi.HASH_LINE + " 0"), encoding="utf-8")
    assert bi.check(repo) == 1
    assert "edited by hand" in capsys.readouterr().err


def test_cli_exit_code(repo):
    run = lambda cmd: subprocess.run([sys.executable, str(_SCRIPT), cmd, "--root", str(repo)], capture_output=True, text=True)
    assert run("check").returncode == 1
    assert run("write").returncode == 0
    assert run("check").returncode == 0


def test_this_repos_input_set_excludes_tests_and_includes_the_registry(bi):
    names = bi.collect(_REPO) if (_REPO / ".git").exists() else None
    if names is None:
        pytest.skip("not a git checkout")
    assert "src/frontend/src/plugins/registry.generated.ts" in names
    assert "src/frontend/package.json" in names
    assert "pin:ADACPP_RENDER_REF" in names
    assert not any("/__tests__/" in n or ".test." in n for n in names)
