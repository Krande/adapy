"""HTTP uploader for the ada-build CLI.

Pushes ``(artefact, build.json)`` pairs to adapy-viewer over the
existing ``PUT /api/scopes/project:<id>/blobs/<key>`` endpoint, using a
project-scoped CI bearer token. The relative key (
``versions/<branch>/<commit>/<filename>``) is the **stable contract**;
adapy resolves it to its full S3 layout server-side.

Environment:
    ADAPY_VIEWER_URL   — base URL, e.g. https://example/.../viewer
    ADAPY_VIEWER_TOKEN — 30-day CI bearer minted by the
                         POST /api/admin/projects/{id}/ci-bot endpoint
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Direct PUT works up to the API server's buffered-upload cap (200 MB
# at the time of writing). Stay below it with margin; above the
# threshold use the presigned-URL flow that already exists in the API.
# TODO: implement presigned-URL fallback when an artefact exceeds this.
_DIRECT_PUT_THRESHOLD_BYTES = 150 * 1024 * 1024


@dataclass
class UploadConfig:
    base_url: str
    token: str

    @classmethod
    def from_env(cls) -> "UploadConfig | None":
        url = os.environ.get("ADAPY_VIEWER_URL", "").strip().rstrip("/")
        token = os.environ.get("ADAPY_VIEWER_TOKEN", "").strip()
        if not url or not token:
            return None
        return cls(base_url=url, token=token)


def _encode_branch(branch: str) -> str:
    """URL-safe branch segment. Slashes flatten to ``__`` for readable
    S3 listings; everything else is left alone (commit SHA segments
    are already safe). The raw branch stays in the build.json sidecar.
    """
    return branch.replace("/", "__")


def _relative_key(branch: str, commit: str, filename: str) -> str:
    return f"versions/{_encode_branch(branch)}/{commit}/{filename}"


def _put_blob(
    client: httpx.Client,
    cfg: UploadConfig,
    project_slug: str,
    rel_key: str,
    file_path: pathlib.Path,
    content_type: str,
) -> None:
    url = f"{cfg.base_url}/api/scopes/project:{project_slug}/blobs/{rel_key}"
    size = file_path.stat().st_size
    if size > _DIRECT_PUT_THRESHOLD_BYTES:
        raise NotImplementedError(
            f"{file_path} is {size} bytes, above the {_DIRECT_PUT_THRESHOLD_BYTES} "
            "direct-PUT threshold; presigned-URL fallback not yet implemented"
        )
    with file_path.open("rb") as fh:
        resp = client.put(
            url,
            content=fh.read(),
            headers={
                "Authorization": f"Bearer {cfg.token}",
                "Content-Type": content_type,
                "Content-Length": str(size),
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"upload failed for {rel_key}: {resp.status_code} {resp.text}"
        )


def upload_output_dir(
    output_dir: pathlib.Path,
    project_slug: str,
    cfg: UploadConfig,
) -> int:
    """Walk ``output_dir`` for ``(artefact, .build.json)`` pairs, PUT both.

    Returns the number of artefact pairs uploaded. Each sidecar names
    the relative key components (branch, commit, artefact name) so
    upload is self-contained — no need to re-read ada_config.toml here.
    """
    sidecar_suffix = ".build.json"
    pairs: list[tuple[pathlib.Path, pathlib.Path, dict]] = []
    for sidecar in output_dir.rglob("*" + sidecar_suffix):
        artefact = sidecar.parent / sidecar.name[: -len(sidecar_suffix)]
        if not artefact.exists():
            logger.warning("orphan sidecar %s — no matching artefact", sidecar)
            continue
        meta = json.loads(sidecar.read_text())
        pairs.append((artefact, sidecar, meta))

    if not pairs:
        logger.info("no artefacts to upload under %s", output_dir)
        return 0

    count = 0
    with httpx.Client(timeout=120.0) as client:
        for artefact, sidecar, meta in pairs:
            artefact_name = meta["artefact"]
            branch = meta["git"]["branch"]
            commit = meta["git"]["commit"]
            if not branch or not commit:
                raise RuntimeError(
                    f"sidecar {sidecar} missing branch or commit; "
                    "run inside a CI checkout or set the FORGEJO_REF_NAME / "
                    "GITHUB_REF_NAME env var for detached-HEAD builds"
                )

            blob_key = _relative_key(branch, commit, artefact_name)
            sidecar_key = blob_key + sidecar_suffix

            content_type = (
                "model/gltf-binary"
                if artefact.suffix.lower() == ".glb"
                else "application/octet-stream"
            )
            _put_blob(client, cfg, project_slug, blob_key, artefact, content_type)
            _put_blob(
                client,
                cfg,
                project_slug,
                sidecar_key,
                sidecar,
                "application/json",
            )
            print(f"  uploaded {blob_key}")
            print(f"  uploaded {sidecar_key}")
            count += 1
    return count
