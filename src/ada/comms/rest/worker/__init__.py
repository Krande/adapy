"""Conversion worker: pulls jobs from NATS JetStream, runs the
converter in a threadpool, writes the derived GLB to storage, and
updates the job's status in KV.

Run as `python -m ada.comms.rest.worker`. Reads the same env vars as
the API service so a single image can be deployed twice (api + worker)
with the same config map.

Crash semantics: a job message is acked only after the derived blob
is uploaded and the KV entry is marked done. If the worker dies
mid-conversion the message is redelivered after `ack_wait`; the next
worker reconverts (deterministic output, so this is safe).

This package is the facade: the concerns live in submodules (``pools`` — capability pools and
scheduling; ``state`` — process-wide slots; ``memory`` — parent hygiene + self probes; ``audit``
— audit-row bookkeeping; ``blobs`` — source/derived blob helpers; ``settings`` — per-job
conversion settings; ``advertise`` / ``registration`` — what the worker publishes; ``process``
— one job around a format handler; ``loop`` — the process itself). Per-format handlers live in
``ada.comms.rest.formats``. The names below are re-exported so ``ada.comms.rest.worker``
keeps its public surface.
"""

from __future__ import annotations

import time  # noqa: F401 — tests patch ``worker.time.sleep``

from ada.config import logger  # noqa: F401

from .. import db as db_module  # noqa: F401 — tests patch ``worker.db_module.<fn>``
from .advertise import (  # noqa: F401
    _advertised_specs,
    _capture_worker_packages,
    _gate_advertised_engines,
    _gate_enum_option,
    _gate_serializer_axis,
)
from .audit import (  # noqa: F401
    _attach_cpp_profiles,
    _audit_done,
    _convert_meta_for,
    _report_job_status_over_api,
)
from .blobs import (  # noqa: F401
    SourceFetch,
    _ensure_sif_index,
    _try_reduced_sif_source,
    _try_sin_stream_uri,
    _wait_for_blob,
    fetch_source,
)
from .loop import (  # noqa: F401
    _heartbeat_until_stopped,
    _run,
    _warm_convert_imports,
    run,
)
from .memory import (  # noqa: F401
    _read_self_proc_io,
    _read_self_rusage,
    _read_self_vmhwm_kb,
    _trim_parent_memory,
)
from .pools import (  # noqa: F401
    BUS_HEARTBEAT_FAILURE_LIMIT,
    BUS_HEARTBEAT_SECONDS,
    FETCH_BATCH,
    FETCH_TIMEOUT,
    IN_PROGRESS_REFRESH_SECONDS,
    MAX_DELIVERIES,
    POOL_STREAK_LIMIT,
    _advance_pool_cursor,
    _bool_env,
    _declared_capabilities,
    _per_fetch_timeout,
    _pool_capabilities,
    _worker_id,
)
from .process import _process_one, _should_skip_cancelled  # noqa: F401
from .registration import Registration, build_registration  # noqa: F401
from .settings import ConversionSettings, read_conversion_settings  # noqa: F401
from .source_nodes import (  # noqa: F401
    _rest_source_nodes_config,
    _RestSourceNodesRecorder,
    _source_nodes_recorder,
    _SyncSourceNodesFacade,
    _SyncStorageFacade,
)
from .state import (  # noqa: F401
    STRUCTURAL_ARTIFACT_WAIT_BUDGET_S,
    STRUCTURAL_ARTIFACT_WAIT_INTERVAL_S,
    STRUCTURAL_SECTIONS_WAIT_BUDGET_S,
    WORKER_LIVENESS_FILE,
    _scope_of,
    _touch_liveness,
)

# Per-format handler entrypoints, re-exported lazily (PEP 562): the handler modules import
# ``worker.audit`` / ``worker.state`` / ..., so importing them eagerly here would make
# ``import ada.comms.rest.formats`` (handler first) a circular import.
_FORMAT_EXPORTS = {
    "_run_component_build": "component",
    "_run_procedural_engine_build": "engine_build",
    "_infer_equipment_geometry": "equipment",
    "_load_cad_mesh": "equipment",
    "_run_equipment_bbox": "equipment",
    "_run_fea_artefact_bake": "fea",
    "_run_fea_meta_compute": "fea",
    "_parity_child": "parity",
    "_run_parity_validation": "parity",
    "_run_plugin_job": "plugin",
    "_run_procedural_build": "procedural_build",
    "_serialize_structural_artifact": "procedural_build",
    "_run_procedural_detail": "procedural_detail",
    "_run_procedural_relocations": "procedural_detail",
    "_run_procedural_export_model": "procedural_export",
    "_run_procedural_export_xlsx": "procedural_export",
    "_run_procedural_import_xlsx": "procedural_export",
    "_run_utility_job": "utility",
}


def __getattr__(name: str):
    module = _FORMAT_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.formats.{module}"), name)
    globals()[name] = value
    return value


# Register the built-in handlers so ``_process_one`` can resolve a job. Last, so a handler
# module importing ``worker.<submodule>`` finds it initialised whichever package loads first.
from .. import formats as _formats  # noqa: E402,F401
