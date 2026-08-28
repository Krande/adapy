"""Catalogue seam for externally-stored models — the generalisation of what an
out-of-tree plugin does against a third-party asset API, reduced to what an
object store already gives you.

TWO LEVELS, NOT FOUR. A catalogue whose hierarchy lives in manifest JSON blobs
needs several levels, because each has to be walked to find the loadable thing.
An object store *is* a tree, so the same job needs only:

    collection  -- a top-level prefix in the bucket
    model       -- one object under it. The loadable thing. One GLB.

Adding manifest levels back would be reproducing accidental complexity.

Every network call sits behind :class:`ExternalModelCatalog` with a stub
selected by default, so a deployment must opt *in* to reaching a bucket. A
worker with no catalogue credentials degrades to a browsable fixture rather
than erroring on every request — and the fixture's names are suffixed
``(stub)`` so a misconfigured deployment is visible rather than silently empty.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "Collection",
    "ExternalModel",
    "ExternalModelCatalog",
    "StubExternalModelCatalog",
    "S3ExternalModelCatalog",
    "demo_catalog_from_env",
    "MODEL_SUFFIXES",
]

# Only these are offered as loadable. The viewer mounts a model by URL through
# SceneHandle.loadModelFromUrl, which drives the same loader the storage browser
# uses, so the set is "what that loader accepts" and not "everything in the
# bucket" — otherwise a stray README.md shows up in the dropdown as a model.
MODEL_SUFFIXES = (".glb", ".gltf")


@dataclass(frozen=True)
class Collection:
    """A top-level prefix in the catalogue bucket."""

    id: str
    name: str


@dataclass(frozen=True)
class ExternalModel:
    """One loadable object under a collection."""

    id: str
    name: str
    collection: str
    key: str
    size: int | None = None


@runtime_checkable
class ExternalModelCatalog(Protocol):
    """The entire seam. Three methods; anything the viewer needs is composed
    from them."""

    def list_collections(self) -> list[Collection]: ...

    def list_models(self, collection: str) -> list[ExternalModel]: ...

    def model_download_url(self, collection: str, model_id: str, *, expires_in_seconds: int = 900) -> str: ...


def _model_name(key: str) -> str:
    base = key.rsplit("/", 1)[-1]
    for suf in MODEL_SUFFIXES:
        if base.lower().endswith(suf):
            return base[: -len(suf)]
    return base


@dataclass
class StubExternalModelCatalog:
    """Fixture-backed, no network, no credentials. **The default.**

    Deliberately returns URLs under an unfetchable scheme: browsing works under
    the stub, but loading geometry fails loudly rather than looking like a
    network blip.
    """

    _data: dict[str, list[str]] = field(
        default_factory=lambda: {
            "demo": ["kitchen.glb", "pipe-rack.glb"],
            "samples": ["beam-assembly.glb"],
        }
    )

    def list_collections(self) -> list[Collection]:
        return [Collection(id=c, name=f"{c} (stub)") for c in sorted(self._data)]

    def list_models(self, collection: str) -> list[ExternalModel]:
        keys = self._data.get(collection)
        if keys is None:
            return []
        return [
            ExternalModel(
                id=_model_name(k),
                name=f"{_model_name(k)} (stub)",
                collection=collection,
                key=f"{collection}/{k}",
                size=None,
            )
            for k in sorted(keys)
        ]

    def model_download_url(self, collection: str, model_id: str, *, expires_in_seconds: int = 900) -> str:
        return f"stub://external-models/{collection}/{model_id}"


class S3ExternalModelCatalog:
    """Lists a bucket two levels deep and mints presigned GETs.

    Built on obstore, the same client core storage uses, so this adds no
    dependency. It is a SEPARATE store from the viewer's own: different bucket,
    different credentials. The viewer's key is not supposed to reach here.

    Like core storage, the signing store may point at a different (public)
    endpoint than the one used for listing — the browser has to resolve the
    host in the URL we hand it, and it will not be the cluster-internal one.
    Signatures stay valid because object_store signs against the host header
    derived from the endpoint URL.
    """

    def __init__(
        self,
        bucket: str,
        *,
        endpoint: str | None = None,
        endpoint_public: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        from obstore.store import S3Store

        kwargs: dict[str, object] = {"region": region, "virtual_hosted_style_request": False}
        if endpoint:
            kwargs["endpoint"] = endpoint
            # Cluster-local Garage/MinIO listen on http; object_store rejects
            # plain http by default, so opt in only when the URL clearly is.
            if endpoint.lower().startswith("http://"):
                kwargs["allow_http"] = True
        if access_key_id:
            kwargs["access_key_id"] = access_key_id
        if secret_access_key:
            kwargs["secret_access_key"] = secret_access_key

        self._bucket = bucket
        self._store = S3Store(bucket, **kwargs)

        self._presign_store = self._store
        if endpoint_public and endpoint_public != endpoint:
            pk = dict(kwargs)
            pk["endpoint"] = endpoint_public
            # Recompute rather than inherit, so the cluster-local http
            # relaxation never leaks onto a public host.
            if endpoint_public.lower().startswith("http://"):
                pk["allow_http"] = True
            else:
                pk.pop("allow_http", None)
            self._presign_store = S3Store(bucket, **pk)

    def _list_keys(self, prefix: str = "") -> list[str]:
        import obstore as obs

        out: list[str] = []
        for batch in obs.list(self._store, prefix=prefix or None):
            for meta in batch:
                out.append(meta["path"])
        return out

    def list_collections(self) -> list[Collection]:
        seen: set[str] = set()
        for key in self._list_keys():
            head, sep, _ = key.partition("/")
            # An object at the bucket root belongs to no collection. Skipped
            # rather than invented into one, so the tree stays exactly two deep.
            if sep:
                seen.add(head)
        return [Collection(id=c, name=c) for c in sorted(seen)]

    def list_models(self, collection: str) -> list[ExternalModel]:
        collection = collection.strip("/")
        if not collection:
            return []
        models: list[ExternalModel] = []
        for key in self._list_keys(f"{collection}/"):
            rest = key[len(collection) + 1 :]
            # Two levels only: an object nested deeper is not a model of this
            # collection. Ignoring it beats flattening it into a bogus name.
            if "/" in rest:
                continue
            if not rest.lower().endswith(MODEL_SUFFIXES):
                continue
            models.append(
                ExternalModel(
                    id=_model_name(rest),
                    name=_model_name(rest),
                    collection=collection,
                    key=key,
                )
            )
        return sorted(models, key=lambda m: m.name)

    def model_download_url(self, collection: str, model_id: str, *, expires_in_seconds: int = 900) -> str:
        import obstore as obs

        for m in self.list_models(collection):
            if m.id == model_id:
                return obs.sign(self._presign_store, "GET", m.key, timedelta(seconds=expires_in_seconds))
        raise KeyError(f"no model {model_id!r} in collection {collection!r}")


def demo_catalog_from_env() -> ExternalModelCatalog:
    """Build the built-in ``demo`` provider from the environment. Defaults to the
    stub, so a deployment must opt in to network calls.

    This is the factory registered for the ``demo`` provider id — it is not the
    external-model API. Consumers go through
    :func:`ada.plugins.external_models.get_external_model_provider`, which is
    what lets the same call site read the vendor catalogue instead."""
    kind = (os.environ.get("ADA_EXTERNAL_MODELS_CATALOG") or "stub").strip().lower()
    if kind in ("", "stub"):
        return StubExternalModelCatalog()
    if kind == "s3":
        bucket = (os.environ.get("ADA_EXTERNAL_MODELS_S3_BUCKET") or "").strip()
        if not bucket:
            # Failing back to the stub would hide a real misconfiguration behind
            # a browsable fixture. An operator who asked for s3 wants s3.
            raise ValueError("ADA_EXTERNAL_MODELS_CATALOG=s3 requires ADA_EXTERNAL_MODELS_S3_BUCKET")
        return S3ExternalModelCatalog(
            bucket,
            endpoint=os.environ.get("ADA_EXTERNAL_MODELS_S3_ENDPOINT", "").strip() or None,
            endpoint_public=os.environ.get("ADA_EXTERNAL_MODELS_S3_ENDPOINT_PUBLIC", "").strip() or None,
            access_key_id=os.environ.get("ADA_EXTERNAL_MODELS_S3_ACCESS_KEY_ID", "").strip() or None,
            secret_access_key=os.environ.get("ADA_EXTERNAL_MODELS_S3_SECRET_ACCESS_KEY", "").strip() or None,
            region=os.environ.get("ADA_EXTERNAL_MODELS_S3_REGION", "us-east-1").strip() or "us-east-1",
        )
    raise ValueError(f"unknown ADA_EXTERNAL_MODELS_CATALOG={kind!r} (expected 'stub' or 's3')")
