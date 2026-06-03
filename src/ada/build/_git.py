"""Git provenance extraction for the build.json sidecar."""
from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass


@dataclass
class GitProvenance:
    commit: str
    parents: list[str]
    branch: str
    author_email: str
    timestamp: str  # ISO 8601, committer date
    remote_url: str
    is_dirty: bool

    def to_dict(self) -> dict:
        return {
            "commit": self.commit,
            "parents": self.parents,
            "branch": self.branch,
            "author": self.author_email,
            "timestamp": self.timestamp,
            "remote_url": self.remote_url,
            "is_dirty": self.is_dirty,
        }


def _run(args: list[str], cwd: pathlib.Path) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL
    ).strip()


def extract(repo: pathlib.Path) -> GitProvenance:
    """Extract git provenance from a working tree."""
    commit = _run(["rev-parse", "HEAD"], repo)
    parents_raw = _run(["rev-list", "--parents", "-n", "1", "HEAD"], repo)
    parents = parents_raw.split()[1:]

    try:
        branch = _run(["symbolic-ref", "--short", "HEAD"], repo)
    except subprocess.CalledProcessError:
        # Detached HEAD — common in CI checkouts. Fall back to env vars
        # the major CI systems set.
        branch = (
            os.environ.get("GITHUB_REF_NAME")
            or os.environ.get("CI_COMMIT_BRANCH")
            or os.environ.get("FORGEJO_REF_NAME")
            or ""
        )

    author_email = _run(["log", "-1", "--format=%ae", "HEAD"], repo)
    ts = _run(["log", "-1", "--format=%cI", "HEAD"], repo)

    try:
        remote_url = _run(["remote", "get-url", "origin"], repo)
    except subprocess.CalledProcessError:
        remote_url = ""

    is_dirty = bool(_run(["status", "--porcelain"], repo))

    return GitProvenance(
        commit=commit,
        parents=parents,
        branch=branch,
        author_email=author_email,
        timestamp=ts,
        remote_url=remote_url,
        is_dirty=is_dirty,
    )
