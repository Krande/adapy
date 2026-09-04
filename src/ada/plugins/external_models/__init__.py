"""External models — core's API for listing and retrieving models stored
somewhere adapy does not manage, plus the built-in ``demo`` provider.

TWO THINGS LIVE HERE, and keeping them apart is the design:

1. **The API** (:mod:`.providers`). A registry of providers behind one
   interface. Core owns this and has no opinion about where models come from.
   Any plugin — in-tree or out — registers with
   :func:`register_external_model_provider` and gets no special treatment.

2. **The built-in ``demo`` provider** (:mod:`.catalog`). An object-store
   catalogue, two levels deep: a bucket prefix is a collection, each object
   under it is a loadable model. Defaults to a no-network stub.

Why the split matters: a consumer — a models dropdown, or another plugin's
panel — asks for a provider by id and calls the same three methods either way.
Swapping one catalogue for another is a deployment change, not a code change at
the call site. If a consumer ever needs to know which provider it is talking to
in order to work, this abstraction has failed.

Discovery: list ``ada.plugins.external_models`` in ``ADA_WORKER_PRELOAD``, or
call :func:`register` directly. Importing this module does NOT auto-register —
an import side effect that mutates a global registry is exactly the surprise
the preload list exists to make explicit.
"""

from __future__ import annotations

from ada.plugins import register_plugin_backend
from ada.plugins.external_models.catalog import (
    Collection,
    ExternalModel,
    ExternalModelCatalog,
    ModelRevision,
    S3ExternalModelCatalog,
    StubExternalModelCatalog,
    demo_catalog_from_env,
)
from ada.plugins.external_models.providers import (
    external_model_provider_ids,
    external_model_providers,
    get_external_model_provider,
    register_external_model_provider,
    reset_providers,
    unregister_external_model_provider,
)

__all__ = [
    "PLUGIN_ID",
    "WORKER_CAPABILITY",
    "DEMO_PROVIDER_ID",
    "register",
    "register_demo_provider",
    # the API
    "register_external_model_provider",
    "unregister_external_model_provider",
    "get_external_model_provider",
    "external_model_providers",
    "external_model_provider_ids",
    "reset_providers",
    # the seam a provider implements
    "ExternalModelCatalog",
    "Collection",
    "ExternalModel",
    "ModelRevision",
    # the built-in provider's pieces
    "StubExternalModelCatalog",
    "S3ExternalModelCatalog",
    "demo_catalog_from_env",
]

PLUGIN_ID = "external-models"
WORKER_CAPABILITY = "external-models"
DEMO_PROVIDER_ID = "demo"


def register_demo_provider() -> None:
    """Register only the built-in object-store provider.

    Separate from :func:`register` so a deployment can host the vendor provider
    alone — the API is useful with zero built-in providers.
    """
    register_external_model_provider(
        DEMO_PROVIDER_ID,
        demo_catalog_from_env,
        label="Demo (object store)",
    )


def register() -> None:
    """Register the backend plugin spec and the built-in ``demo`` provider.

    Registers through :func:`ada.plugins.register_plugin_backend` like any
    third-party plugin rather than being special-cased. If that ever stops being
    true, the plugin contract has grown a shortcut core should not have.
    """
    register_plugin_backend(
        PLUGIN_ID,
        name="External models",
        description="List and load models from external providers (object store, vendor catalogues).",
        version="1.0.0",
        worker_capability=WORKER_CAPABILITY,
        job_entrypoint="ada.plugins.external_models.adapy_plugin:run_job",
    )
    register_demo_provider()
