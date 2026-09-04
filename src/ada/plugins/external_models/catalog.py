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
    "ModelRevision",
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

# Optional per-collection sidecar mapping model id -> display label, so a
# catalogue can show a meaningful name where the object key is an opaque
# identifier (an E3D ref, say). One GET per listing regardless of model count,
# which is why it is a single file rather than per-object metadata: S3 listings
# do not carry user metadata, so that shape would cost one HEAD per model.
#
# Absent, unreadable or malformed is NOT an error -- every model simply falls
# back to its filename, which is what an unlabelled collection should look like.
LABELS_FILENAME = "_labels.json"


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
    # True when `name` came from the collection's label manifest rather than
    # from the filename. Lets a UI show that a name is curated instead of
    # derived, and lets a scan job find what is still unlabelled.
    labelled: bool = False


@dataclass(frozen=True)
class ModelRevision:
    """One stored version of a model, for catalogues that keep more than one.

    A store that rebuilds a model on a schedule and retains the previous
    output(s) has more than one version of the same `(collection, model)`. Nothing
    in the three required methods can express that: `list_models` returns one
    entry per model and `model_download_url` resolves to whatever the provider
    calls current, so a consumer can only ever reach the newest one.

    `id` is opaque and provider-defined -- a timestamped prefix, an object-version
    id, a build number. Consumers pass it back verbatim and must not parse it;
    `name` is what a person picks from.
    """

    id: str
    name: str
    #: ISO-8601 if the provider knows it. A UI orders by this when present, and
    #: falls back to the order the provider returned otherwise -- so a provider
    #: that cannot date its revisions should return them newest-first.
    created_at: str | None = None
    size: int | None = None
    #: The one `model_download_url` resolves to when no revision is requested.
    #: Exactly one revision in a list should carry it.
    current: bool = False


@runtime_checkable
class ExternalModelCatalog(Protocol):
    """The entire seam. Three methods; anything the viewer needs is composed
    from them."""

    def list_collections(self) -> list[Collection]: ...

    def list_models(self, collection: str) -> list[ExternalModel]: ...

    def model_download_url(self, collection: str, model_id: str, *, expires_in_seconds: int = 900) -> str: ...

    # OPTIONAL, and intentionally not part of the Protocol's required surface: a
    # provider whose download URL carries its own signature needs nothing here,
    # while one whose fetch must be authenticated (a direct-read client against a
    # vendor API, say) returns the headers the browser should send. Absent means
    # "no headers", so an object-store provider implements nothing.
    #
    # Resolved with getattr at the call site rather than declared here, so a
    # provider written before this existed keeps satisfying the Protocol.
    #
    # ALSO OPTIONAL, and the same convention:
    #
    #   model_upload_url(collection, model_id, *, expires_in_seconds, content_type)
    #   model_upload_headers(collection, model_id) -> dict[str, str]
    #
    # A provider that can ACCEPT a model returns a URL the browser may PUT to.
    # Implementing it is how a provider opts IN -- presence is the declaration,
    # there is no flag to set and nothing to configure. A read-only catalogue
    # (a vendor API that owns its own publishing pipeline, say) implements
    # nothing and the viewer never offers upload for it, which is the correct
    # outcome rather than a button that fails.
    #
    # Symmetric with the download on purpose: the bytes go browser -> store
    # directly, never through the worker, so a 5 GB model does not become a job
    # payload. The consequence is that the store must accept a cross-origin PUT
    # from the viewer, which is a deployment fact about the bucket, not
    # something this code can assert.
    #
    # `model_upload_headers` is how a provider states how it wants a model
    # STORED -- the content type, and whether the body should be compressed.
    # The uploader obeys it rather than deciding for itself, because getting
    # this wrong is silent: a gzipped body stored without `Content-Encoding`
    # reaches the viewer as gzip bytes and fails as a JSON parse error, with
    # nothing anywhere naming compression. One catalogue was found in exactly
    # that state, one object among forty.
    #
    # ALSO OPTIONAL, same convention, and these two travel TOGETHER:
    #
    #   list_model_revisions(collection, model_id) -> list[ModelRevision]
    #   model_download_url(..., revision: str | None = None)
    #
    # A catalogue that retains more than one version of a model implements the
    # first to enumerate them, and accepts `revision` on the second to serve one.
    # A catalogue with a single current version implements neither and the viewer
    # never offers a version picker for it -- the correct outcome rather than a
    # control that fails when used.
    #
    # WHY `revision` RIDES ON THE EXISTING DOWNLOAD METHOD rather than arriving as
    # a second one: a consumer then has one way to fetch a model, not two that it
    # has to choose between. `revision` is passed ONLY when a caller asked for a
    # specific one, so a provider written before this exists never sees the
    # keyword and keeps satisfying the Protocol unchanged.
    #
    # The pairing is a real obligation, not a suggestion. Implementing
    # `list_model_revisions` while ignoring `revision` produces a picker whose
    # every entry silently serves the current version -- which looks like it works
    # and is wrong in the one way nobody checks.


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

    #: Fixture revisions, newest first. Only one collection has them, so the stub
    #: exercises both halves of the optional contract: a model that is versioned
    #: and a model that is not.
    _revisions: dict[str, list[tuple[str, str]]] = field(
        default_factory=lambda: {
            "demo/kitchen": [
                ("2024-01-02", "2024-01-02T00:00:00Z"),
                ("2024-01-01", "2024-01-01T00:00:00Z"),
            ]
        }
    )

    def list_model_revisions(self, collection: str, model_id: str) -> list[ModelRevision]:
        entries = self._revisions.get(f"{collection}/{model_id}")
        if not entries:
            # An unversioned model in a versioned catalogue. Empty, not an error:
            # a UI shows no picker for it and everything else still works.
            return []
        return [
            ModelRevision(id=rid, name=f"{rid} (stub)", created_at=created, current=(i == 0))
            for i, (rid, created) in enumerate(entries)
        ]

    def model_download_url(
        self,
        collection: str,
        model_id: str,
        *,
        expires_in_seconds: int = 900,
        revision: str | None = None,
    ) -> str:
        suffix = f"@{revision}" if revision else ""
        return f"stub://external-models/{collection}/{model_id}{suffix}"


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

    def _labels(self, collection: str) -> dict[str, str]:
        """The collection's id -> label map, or empty.

        Every failure mode here is deliberately silent: a collection with no
        manifest is the normal case, and a malformed one should degrade to
        filenames rather than break the listing it decorates.

        THE IMPORT IS INSIDE THE TRY for that reason. Left outside it, an
        environment without obstore raised straight through `list_models` --
        which is a listing failing because its DECORATION is unavailable, and
        the opposite of what this docstring promises. It also broke the test
        fake, which overrides `_list_keys` precisely so it can exercise the
        key-walking without a bucket or the dependency.
        """
        import json

        try:
            import obstore as obs

            raw = obs.get(self._store, f"{collection}/{LABELS_FILENAME}").bytes()
        except Exception:
            return {}
        try:
            parsed = json.loads(bytes(raw).decode("utf-8"))
        except Exception:
            logger.warning("external-models: %s/%s is not valid JSON", collection, LABELS_FILENAME)
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(k): str(v) for k, v in parsed.items() if isinstance(v, (str, int, float))}

    def list_models(self, collection: str) -> list[ExternalModel]:
        collection = collection.strip("/")
        if not collection:
            return []
        labels = self._labels(collection)
        models: list[ExternalModel] = []
        for key in self._list_keys(f"{collection}/"):
            rest = key[len(collection) + 1 :]
            # Two levels only: an object nested deeper is not a model of this
            # collection. Ignoring it beats flattening it into a bogus name.
            if "/" in rest:
                continue
            if rest == LABELS_FILENAME or not rest.lower().endswith(MODEL_SUFFIXES):
                continue
            model_id = _model_name(rest)
            label = labels.get(model_id)
            models.append(
                ExternalModel(
                    id=model_id,
                    # The id stays the filename-derived value: it addresses the
                    # object and must not move when someone edits a label.
                    name=label or model_id,
                    collection=collection,
                    key=key,
                    labelled=label is not None,
                )
            )
        return sorted(models, key=lambda m: m.name)

    def model_download_url(self, collection: str, model_id: str, *, expires_in_seconds: int = 900) -> str:
        import obstore as obs

        for m in self.list_models(collection):
            if m.id == model_id:
                return obs.sign(self._presign_store, "GET", m.key, timedelta(seconds=expires_in_seconds))
        raise KeyError(f"no model {model_id!r} in collection {collection!r}")

    def model_upload_url(
        self,
        collection: str,
        model_id: str,
        *,
        expires_in_seconds: int = 900,
        content_type: str | None = None,
    ) -> str:
        """A URL the browser may PUT one model to.

        Implementing this is how this catalogue declares it can accept models;
        see the note on the Protocol. An object store can, so it does.

        The key is DERIVED here from `(collection, model_id)` and never taken
        from the caller. A caller-supplied key would let a request choose where
        in the bucket its bytes land -- including over another collection's
        model, or outside the two-level layout this catalogue promises -- and a
        presigned URL grants exactly the write it names.

        Overwrite is deliberately allowed. The alternative is refusing to
        re-upload a corrected export under the name everything already
        references, which is the common case; `list_models` shows what is there
        and the caller is choosing the name.
        """
        import obstore as obs

        key = self._upload_key(collection, model_id)
        # `content_type` is accepted for symmetry with what a caller knows and
        # is deliberately NOT signed into the URL: obstore signs the method and
        # path, and binding a header would make the PUT fail whenever the
        # browser normalised it differently.
        del content_type
        return obs.sign(self._presign_store, "PUT", key, timedelta(seconds=expires_in_seconds))

    def model_upload_headers(self, collection: str, model_id: str) -> dict[str, str]:
        """How this store wants a model stored: typed, and gzipped.

        GZIPPED BECAUSE A GLB IS HIGHLY COMPRESSIBLE and is fetched over the
        network every time it is opened -- several MB of float arrays that
        halve, routinely better. The store keeps the compressed bytes and the
        browser inflates them transparently, so nothing downstream changes.

        `Content-Encoding` is the half that must not be forgotten. Storing
        gzipped bytes WITHOUT it hands the viewer gzip where it expects glTF,
        which surfaces as a JSON parse error naming neither compression nor the
        file -- a fault this catalogue has already been found in, on one object
        out of forty. Returning both together is what makes the pair
        inseparable: an uploader obeying these headers cannot store one without
        the other.
        """
        _, _, ext = model_id.rpartition(".")
        content_type = "model/gltf+json" if ext.lower() == "gltf" else "model/gltf-binary"
        return {"Content-Type": content_type, "Content-Encoding": "gzip"}

    def _upload_key(self, collection: str, model_id: str) -> str:
        """`collection/model_id.glb`, or a refusal naming what was wrong.

        Every rejection here is a request that could otherwise write outside the
        collection it named.
        """
        col = (collection or "").strip().strip("/")
        name = (model_id or "").strip()
        if not col or "/" in col:
            raise ValueError(f"invalid collection {collection!r}: expected one path segment")
        if not name:
            raise ValueError("a model id is required")
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"invalid model id {model_id!r}: no path separators, and it may not start with '.'")
        if not name.lower().endswith(MODEL_SUFFIXES):
            raise ValueError(f"invalid model id {model_id!r}: expected one of {', '.join(MODEL_SUFFIXES)}")
        return f"{col}/{name}"


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
