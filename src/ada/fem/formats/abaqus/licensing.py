"""Hold an Abaqus job's start until the shared license pool has tokens for it.

Abaqus tokens come from a shared FlexNet pool. A job that starts while the
pool is short does not wait by default: it fails its license checkout and
the run is lost. Running several jobs at once -- a verification build's
solver matrix -- makes that likely, so each job first takes a *slot*:

    with abaqus_license_slot():
        ... run the job ...

A slot is granted when the pool has room for this job on top of every job
this process already holds a slot for:

    issued - (in use by others) - (tokens our running jobs need) - needed >= 0

"In use by others" is read from ``abaqus licensing ru``: its total in use,
less the checkouts it lists for this user on this host. Our own jobs are
counted by their slots instead, from the moment they are admitted -- before
their checkout shows on the server, which is the race a free-token count
alone loses when two jobs start together.

Opt-in, by environment, so an ordinary ``a.to_fem(..., execute=True)`` is
unchanged:

``ADA_ABAQUS_LICENSE_WAIT_S``
    Seconds a job waits for a slot before starting anyway. Unset or 0: no
    accounting at all (the default).
``ADA_ABAQUS_JOB_TOKENS``
    Tokens one job holds. Unset: read from the job's deck
    (:func:`abaqus_job_tokens`) -- on the license server this was written
    against a CPUS=2 Abaqus/Standard job holds 3 for an eigenfrequency
    analysis and 5 for a static one.

What this cannot see is another user's job taking the tokens between the
check and the checkout. Abaqus has its own license queueing for that
(``ABQLMQUEUE`` / ``ABQLMHANGLIMIT``); it is not switched on here.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from ada.config import logger

_USERS_OF = re.compile(r"^Users of (\S+?):\s*\(Total of (\d+) licenses? issued;\s*Total of (\d+) licenses? in use\)")
# "    ofskrand ABLNOF8VY014 ABLNOF8VY014 (v62.7) (abllic022/27006 982), start Thu 10/1 20:13, 3 licenses"
_CHECKOUT = re.compile(r"^\s+(\S+)\s+(\S+)\s+\S+.*?\(v[\d.]+\).*?start [^,]*(?:,\s*(\d+) licenses?)?\s*$")


@dataclass(frozen=True)
class AbaqusTokens:
    issued: int
    in_use: int
    mine: int
    """Tokens checked out by this user on this host -- the ones this process may already account for."""

    @property
    def others(self) -> int:
        return max(0, self.in_use - self.mine)


def parse_abaqus_tokens(text: str, user: str | None = None, host: str | None = None) -> Optional[AbaqusTokens]:
    """The ``abaqus`` feature from ``abaqus licensing ru`` output; None when it is not listed."""
    user = (user or getpass.getuser()).lower()
    host = (host or socket.gethostname()).lower()
    totals = None
    mine = 0
    in_section = False
    for line in text.splitlines():
        head = _USERS_OF.match(line)
        if head:
            in_section = head.group(1).lower() == "abaqus"
            if in_section:
                totals = (int(head.group(2)), int(head.group(3)))
            continue
        if not in_section:
            continue
        m = _CHECKOUT.match(line)
        if m and m.group(1).lower() == user and m.group(2).lower() == host:
            mine += int(m.group(3) or 1)
    if totals is None:
        return None
    return AbaqusTokens(issued=totals[0], in_use=totals[1], mine=mine)


def abaqus_token_status(timeout_s: float = 60.0) -> Optional[AbaqusTokens]:
    """The ``abaqus`` feature's tokens from the license server, or None when it cannot be asked."""
    exe = shutil.which("abaqus")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "licensing", "ru"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=exe.lower().endswith((".bat", ".cmd")),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"abaqus licensing ru failed: {exc}")
        return None
    return parse_abaqus_tokens(out.stdout + out.stderr)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


#: Tokens a CPUS=2 Abaqus/Standard job holds, by analysis, as ``abaqus licensing ru`` lists them on
#: the license server this was written against. A deck with any step other than a frequency
#: extraction is planned at the larger number.
EIGEN_JOB_TOKENS = 3
GENERAL_JOB_TOKENS = 5

_STEP_PROCEDURE = re.compile(
    r"^\s*\*(frequency|static|dynamic|visco|heat transfer|coupled temperature-displacement|buckle|"
    r"steady state dynamics|modal dynamic|random response|response spectrum|complex frequency|"
    r"soils|geostatic|direct cyclic|annealing|mass diffusion)\b",
    re.IGNORECASE,
)


def abaqus_job_tokens(inp_path: os.PathLike | str | None) -> int:
    """Tokens to plan for the job ``inp_path`` describes: ``EIGEN_JOB_TOKENS`` when its only
    analysis procedure is a frequency extraction, ``GENERAL_JOB_TOKENS`` otherwise (and when the
    deck cannot be read). Only keyword lines are looked at."""
    procedures = set()
    try:
        with open(inp_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("*") or line.lstrip().startswith("*"):
                    m = _STEP_PROCEDURE.match(line)
                    if m:
                        procedures.add(m.group(1).lower())
    except (OSError, TypeError):
        return GENERAL_JOB_TOKENS
    return EIGEN_JOB_TOKENS if procedures == {"frequency"} else GENERAL_JOB_TOKENS


_slots_lock = threading.Lock()
#: Tokens reserved by the jobs this process has admitted and not yet finished -- each at its own
#: count, so a 3-token eigen job asking for room sees a running 5-token static job as 5.
_tokens_reserved = 0


@contextlib.contextmanager
def abaqus_license_slot(
    tokens: int = GENERAL_JOB_TOKENS,
    status: Callable[[], Optional[AbaqusTokens]] = abaqus_token_status,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[bool]:
    """Hold an Abaqus job that needs ``tokens`` until the pool has room for it (see module
    docstring); yields whether it had to wait. A no-op unless ``ADA_ABAQUS_LICENSE_WAIT_S`` is set.
    Never raises on the license side: when the server cannot be asked, or the wait runs out, the
    job starts and Abaqus decides.
    """
    global _tokens_reserved
    wait_s = _env_float("ADA_ABAQUS_LICENSE_WAIT_S", 0.0)
    if wait_s <= 0:
        yield False
        return

    needed = int(_env_float("ADA_ABAQUS_JOB_TOKENS", tokens))
    deadline = clock() + wait_s
    waited = False
    while True:
        pool = status()  # outside the lock: a server round trip should not hold up the others
        with _slots_lock:
            room = None if pool is None else pool.issued - pool.others - _tokens_reserved - needed
            if room is None or room >= 0 or clock() >= deadline:
                if room is not None and room < 0:
                    logger.warning(f"Abaqus: no room for {needed} tokens after {wait_s:.0f} s; starting anyway")
                _tokens_reserved += needed
                break
            reserved = _tokens_reserved
        if not waited:
            logger.info(
                f"Abaqus: waiting for {needed} tokens ({pool.issued - pool.others} not held by others, "
                f"{reserved} held by our running jobs)"
            )
        waited = True
        sleep(10.0)
    try:
        yield waited
    finally:
        with _slots_lock:
            _tokens_reserved -= needed
