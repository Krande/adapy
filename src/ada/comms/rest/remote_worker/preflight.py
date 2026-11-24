"""Preflight primitives for a remote worker's setup and its ``check`` command.

Each answers one question with a :class:`Check` (``ok``, a one-line ``detail``) and never puts a
secret in it: a seed is read, turned into its public key and dropped, and no message, log line or
``repr`` here carries it.

* :func:`check_seed` -- does this seed belong to the public key the operator registered?
* :func:`check_version_floor` -- is this ada-py at least the deployment's floor (bootstrap)?
* :func:`classify_nats_error` -- an *authorization* violation (the server does not know this key)
  or a *permissions* violation (it knows the key; the subject is not allowed)? Two different fixes.
* :func:`probe_grant` -- may this key ask for a grant, and does the answer reach its private inbox?
  Asks for the job ``probe``, which always answers "no such job".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .grants import GrantClient, GrantRefusedError, GrantUnavailableError

__all__ = [
    "AUTH_VIOLATION",
    "OTHER",
    "PERMISSION_VIOLATION",
    "Check",
    "check_seed",
    "check_version_floor",
    "classify_nats_error",
    "probe_grant",
    "public_key_of_seed",
]

AUTH_VIOLATION = "authorization_violation"
PERMISSION_VIOLATION = "permissions_violation"
OTHER = "other"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'ok' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


def public_key_of_seed(seed: str | bytes) -> str:
    """The public key (``U...``) of an nkey seed (``SU...``). ValueError -- without the seed in
    it -- for anything that is not one."""
    try:
        import nkeys
    except ImportError as exc:  # pragma: no cover - nkeys ships with the viewer-api environment
        raise ValueError("the 'nkeys' package is not installed; it is needed to read an nkey seed") from exc
    raw = seed.encode("ascii", "ignore") if isinstance(seed, str) else bytes(seed)
    raw = raw.strip()
    try:
        kp = nkeys.from_seed(bytearray(raw))
        public = kp.public_key
    except Exception:  # noqa: BLE001 - nkeys' errors may echo the input
        raise ValueError("not an nkey seed (expected the 'SU...' line of the key file)") from None
    try:
        kp.wipe()
    except Exception:  # noqa: BLE001 - best-effort scrub of the private key material
        pass
    return public.decode("ascii") if isinstance(public, bytes) else str(public)


def check_seed(seed: str | bytes, public_key: str) -> Check:
    """Whether ``seed`` is the private half of ``public_key`` (the key the server's ``nats.conf``
    names)."""
    try:
        actual = public_key_of_seed(seed)
    except ValueError as exc:
        return Check("seed", False, str(exc))
    expected = (public_key or "").strip()
    if actual == expected:
        return Check("seed", True, f"the seed belongs to {expected}")
    return Check(
        "seed",
        False,
        f"the seed belongs to {actual}, not {expected or '(no key given)'}: the server knows this worker "
        "by the second; register the first, or use the seed that was made with it",
    )


def check_version_floor(installed: str | None, floor: str | None, *, package: str = "ada-py") -> Check:
    """``installed`` against the deployment's floor (``worker.min_ada_py`` of the bootstrap). No
    floor passes; a version that cannot be ordered against it fails, with the reason."""
    from ..qualification import compare_versions

    if not floor:
        return Check("version", True, f"{package} {installed or '?'}; the deployment sets no floor")
    if not installed:
        return Check("version", False, f"cannot tell which {package} is installed; the deployment needs >= {floor}")
    order = compare_versions(installed, floor)
    if order is None:
        return Check("version", False, f"{package} {installed} cannot be compared with the floor {floor}")
    if order < 0:
        return Check("version", False, f"{package} {installed} is older than the deployment's floor {floor}")
    return Check("version", True, f"{package} {installed} >= {floor}")


def classify_nats_error(error: object) -> str:
    """:data:`AUTH_VIOLATION`, :data:`PERMISSION_VIOLATION` or :data:`OTHER` for an exception (or
    the text of an error the server sent, as nats-py's ``error_cb`` receives it).

    An authorization violation means the server rejected the CONNECTION: the key is not in its
    ``nats.conf`` (or the seed is not that key's). A permissions violation means the connection was
    accepted and one subject was not allowed: the key's entry lacks a subject (its capability, its
    grant subject, or its private inbox)."""
    name = type(error).__name__ if isinstance(error, BaseException) else ""
    text = f"{name} {error}".lower()
    if "authorization" in text or "authentication" in text or "auth violation" in text:
        return AUTH_VIOLATION
    if "permissions violation" in text or "permission violation" in text:
        return PERMISSION_VIOLATION
    return OTHER


async def probe_grant(
    request: Callable[[str, bytes, float], Awaitable[bytes]],
    *,
    subject_prefix: str,
    capability: str,
    worker_name: str,
    timeout_s: float = 5.0,
) -> Check:
    """Ask for the ``probe`` job's grant. "No such job" is the pass: the request was allowed out,
    the API's responder answered, and the reply came back on this connection's private inbox."""
    client = GrantClient(
        request,
        subject_prefix=subject_prefix,
        capability=capability,
        worker_name=worker_name,
        job_id="probe",
        timeout_s=timeout_s,
    )
    try:
        await client.ask()
    except GrantRefusedError as exc:
        if exc.code == "no_such_job":
            return Check("grant probe", True, f"{client.subject} answered (no such job, as expected)")
        return Check("grant probe", False, f"{client.subject} refused the probe: {exc.code}: {exc.detail}")
    except GrantUnavailableError as exc:
        return Check("grant probe", False, str(exc))
    except Exception as exc:  # noqa: BLE001 - reported, with which kind of violation it was
        kind = classify_nats_error(exc)
        return Check("grant probe", False, f"{kind}: {type(exc).__name__}: {exc}")
    return Check("grant probe", False, f"{client.subject} granted the probe job, which no API should")
