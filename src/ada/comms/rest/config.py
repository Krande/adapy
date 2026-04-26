from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class S3Config:
    bucket: str
    endpoint: str | None
    region: str
    access_key_id: str | None
    secret_access_key: str | None
    prefix: str
    # Garage and MinIO use path-style addressing; AWS uses virtual-hosted.
    virtual_hosted_style: bool


@dataclass(frozen=True)
class LocalConfig:
    path: str
    prefix: str


@dataclass(frozen=True)
class QueueConfig:
    # NATS server URL. When None, conversion endpoints are disabled and
    # the API serves only listing / direct-blob fetches.
    url: str | None
    stream: str
    subject: str
    kv_bucket: str
    durable: str


@dataclass(frozen=True)
class Settings:
    storage_kind: str  # "s3" | "local"
    s3: S3Config | None
    local: LocalConfig | None
    host: str
    port: int
    # Optional path on disk to a built frontend bundle (index.html + assets/).
    # When set, the API also serves the SPA. Empty disables static serving.
    static_path: str
    queue: QueueConfig


def _bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    kind = os.environ.get("ADA_VIEWER_STORAGE_KIND", "local").strip().lower()
    host = os.environ.get("ADA_VIEWER_HOST", "0.0.0.0")
    port = int(os.environ.get("ADA_VIEWER_PORT", "8080"))
    static_path = os.environ.get("ADA_VIEWER_STATIC_PATH", "").strip()

    nats_url = os.environ.get("ADA_VIEWER_NATS_URL", "").strip() or None
    queue = QueueConfig(
        url=nats_url,
        stream=os.environ.get("ADA_VIEWER_NATS_STREAM", "ADA_VIEWER_JOBS"),
        subject=os.environ.get("ADA_VIEWER_NATS_SUBJECT", "ada.viewer.jobs.convert"),
        kv_bucket=os.environ.get("ADA_VIEWER_NATS_KV_BUCKET", "ada-viewer-jobs"),
        durable=os.environ.get("ADA_VIEWER_NATS_DURABLE", "ada-viewer-worker"),
    )

    if kind == "s3":
        s3 = S3Config(
            bucket=os.environ["ADA_VIEWER_S3_BUCKET"],
            endpoint=os.environ.get("ADA_VIEWER_S3_ENDPOINT"),
            region=os.environ.get("ADA_VIEWER_S3_REGION", "us-east-1"),
            access_key_id=os.environ.get("ADA_VIEWER_S3_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("ADA_VIEWER_S3_SECRET_ACCESS_KEY"),
            prefix=os.environ.get("ADA_VIEWER_S3_PREFIX", "").strip("/"),
            virtual_hosted_style=_bool(
                os.environ.get("ADA_VIEWER_S3_VIRTUAL_HOSTED_STYLE"), default=False
            ),
        )
        return Settings(
            storage_kind="s3",
            s3=s3,
            local=None,
            host=host,
            port=port,
            static_path=static_path,
            queue=queue,
        )

    if kind == "local":
        local = LocalConfig(
            path=os.environ.get("ADA_VIEWER_LOCAL_PATH", "./viewer-data"),
            prefix=os.environ.get("ADA_VIEWER_LOCAL_PREFIX", "").strip("/"),
        )
        return Settings(
            storage_kind="local",
            s3=None,
            local=local,
            host=host,
            port=port,
            static_path=static_path,
            queue=queue,
        )

    raise ValueError(f"Unsupported ADA_VIEWER_STORAGE_KIND: {kind!r} (expected 's3' or 'local')")
