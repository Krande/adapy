"""The adacpp wasm base version must live in exactly ONE place.

It didn't, and it shipped wrong for two releases. deploy/Dockerfile.viewer declared the pin as an
``ARG ADACPP_BASE_IMAGE`` default, but both CI systems passed ``--build-arg ADACPP_BASE_IMAGE=...``
with their own literal — and a passed build-arg OVERRIDES an ARG default. Each literal sat under a
"keep in sync with deploy/Dockerfile.viewer" comment, and both had drifted to 0.13.2 while the
Dockerfile said 0.14.0. So the published viewer and the hosted deploy both built on a base two
releases old, and bumping the Dockerfile changed nothing but the comment. A third copy — the audit
CLI's default image — was still on 0.9.0, so wasm sweeps validated an engine six releases older than
the one that shipped.

Comments asserting the invariant are what failed, twice. These tests enforce it instead:
the workflows now DERIVE the pin (github lets the ARG default stand; forgejo seds it out), and where
duplication is unavoidable — ada_cli ships in a wheel with no deploy/ tree — CI pins the equality.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# Only CONCRETE version literals. The prose in these files legitimately writes
# "ghcr.io/krande/adacpp-wasm-base:<ver>" as a placeholder, which must not trip this.
_CONCRETE_PIN = re.compile(r"adacpp-wasm-base:(\d+\.\d+\.\d+)")
_ARG_PIN = re.compile(r"^ARG ADACPP_BASE_IMAGE=(\S+)$", re.MULTILINE)

_REPO = pathlib.Path(__file__).parents[2]
_DOCKERFILE = _REPO / "deploy" / "Dockerfile.viewer"

# Files allowed to carry a concrete pin, and why.
_DOCKERFILE_REL = "deploy/Dockerfile.viewer"  # THE pin
_AUDIT_REL = "src/ada_cli/audit.py"  # must equal it; can't derive from a wheel

_needs_repo = pytest.mark.skipif(
    not _DOCKERFILE.is_file(),
    reason=f"no repo tree at {_REPO} (sdist/wheel test env) — deploy/ is not packaged",
)


def _dockerfile_pin() -> str:
    found = _ARG_PIN.findall(_DOCKERFILE.read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected exactly one ARG ADACPP_BASE_IMAGE in {_DOCKERFILE_REL}, got {found}"
    return found[0]


@_needs_repo
def test_dockerfile_declares_exactly_one_concrete_pin():
    """The ARG default is the pin, and it is a real tag — an empty or templated default would make
    the derived consumers (forgejo's sed, the unpassed build-arg) resolve to nothing."""
    pin = _dockerfile_pin()
    assert _CONCRETE_PIN.search(pin), f"the ARG default must be a concrete tag, got {pin!r}"


@_needs_repo
def test_no_workflow_restates_the_wasm_base_version():
    """A workflow carrying its own version literal is the exact bug this file documents.

    github's publish-image must not pass the build-arg at all (so the ARG default stands); forgejo
    must sed the value out of the Dockerfile. Either one hardcoding a version silently overrides the
    pin, and — as happened — nothing notices until someone diffs a running image.
    """
    offenders = []
    for wf in sorted((_REPO / ".github" / "workflows").glob("*.y*ml")) + sorted(
        (_REPO / ".forgejo" / "workflows").glob("*.y*ml")
    ):
        for i, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            if _CONCRETE_PIN.search(line):
                offenders.append(f"{wf.relative_to(_REPO)}:{i}: {line.strip()}")
    assert not offenders, (
        "workflows must DERIVE the adacpp wasm base pin from "
        f"{_DOCKERFILE_REL}, never restate it:\n  " + "\n  ".join(offenders)
    )


@_needs_repo
def test_audit_default_image_matches_the_viewer_pin():
    """The wasm sweep must validate the engine that actually ships.

    ada_cli can't read deploy/ (it ships in a wheel without it), so this literal is the one
    unavoidable duplicate — which is exactly why the equality is pinned here rather than trusted.
    """
    from ada_cli.audit import ADACPP_DEFAULT_IMAGE

    assert ADACPP_DEFAULT_IMAGE == _dockerfile_pin(), (
        f"ada_cli.audit.ADACPP_DEFAULT_IMAGE ({ADACPP_DEFAULT_IMAGE}) has drifted from the pin in "
        f"{_DOCKERFILE_REL} ({_dockerfile_pin()}) — a sweep would validate a different engine than "
        "the viewer serves"
    )


@_needs_repo
def test_audit_default_is_at_or_above_the_refusal_floor():
    """The default must not sit below the version the sweep refuses to run."""
    from ada_cli.audit import ADACPP_DEFAULT_IMAGE, ADACPP_MIN_VERSION

    m = _CONCRETE_PIN.search(ADACPP_DEFAULT_IMAGE)
    assert m, f"ADACPP_DEFAULT_IMAGE must carry a concrete version, got {ADACPP_DEFAULT_IMAGE!r}"
    assert tuple(int(p) for p in m.group(1).split(".")) >= ADACPP_MIN_VERSION
