"""Cover the S3Config + load_settings plumbing for dual-endpoint
deployments (in-cluster internal endpoint + public-facing endpoint
for browser-targeted presigned URLs).

The actual URL minting test path needs a real S3Store connection and
lives elsewhere — here we just verify the env var feeds the right
dataclass field so a typo in the var name or a forgotten ``or None``
gets caught at unit-test speed.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from ada.comms.rest.config import load_settings


@contextmanager
def _env(**overrides: str | None):
    """Apply env overrides, restore previous state on exit. ``None`` =
    delete the variable in the body."""
    snapshot: dict[str, str | None] = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _base_env() -> dict[str, str | None]:
    """Minimal S3-kind env so ``load_settings`` succeeds. Override these
    in each test for the dimension under exercise."""
    return {
        "ADA_VIEWER_STORAGE_KIND": "s3",
        "ADA_VIEWER_S3_BUCKET": "test-bucket",
        "ADA_VIEWER_S3_ENDPOINT": "http://garage.svc.cluster.local:3900",
        "ADA_VIEWER_S3_ENDPOINT_PUBLIC": None,
        "ADA_VIEWER_S3_ACCESS_KEY_ID": "k",
        "ADA_VIEWER_S3_SECRET_ACCESS_KEY": "s",
        "ADA_VIEWER_AUTH_ENABLED": "false",
    }


def test_endpoint_public_defaults_to_none_when_unset():
    with _env(**_base_env()):
        s = load_settings()
        assert s.s3 is not None
        assert s.s3.endpoint == "http://garage.svc.cluster.local:3900"
        assert s.s3.endpoint_public is None


def test_endpoint_public_picked_up_from_env():
    env = _base_env()
    env["ADA_VIEWER_S3_ENDPOINT_PUBLIC"] = "https://garage.example.com"
    with _env(**env):
        s = load_settings()
        assert s.s3 is not None
        assert s.s3.endpoint == "http://garage.svc.cluster.local:3900"
        assert s.s3.endpoint_public == "https://garage.example.com"


def test_endpoint_public_blank_string_normalises_to_none():
    # Empty / whitespace-only is the helm-chart default when the operator
    # hasn't set anything; treat it the same as "not provided" so
    # Storage.from_settings doesn't build a redundant second S3Store.
    env = _base_env()
    env["ADA_VIEWER_S3_ENDPOINT_PUBLIC"] = "   "
    with _env(**env):
        s = load_settings()
        assert s.s3 is not None
        assert s.s3.endpoint_public is None
