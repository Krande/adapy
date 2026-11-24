"""The remote worker kit: what a worker outside the cluster needs to work without storage keys.

A remote worker joins from the host URL and an nkey pair. It reads the public bootstrap
(:mod:`.bootstrap`), connects to NATS with a private inbox prefix (``QueueConfig.inbox_prefix``),
and for each job it picks up asks the API for a job grant (:mod:`.grants`) and reads and writes
that job's files through the API's job-scoped routes (:mod:`.storage`). :mod:`.preflight` checks
the pieces one at a time; :mod:`.nats_conf` writes the server-side entry for its key.

Selection: ``ADA_VIEWER_STORAGE_KIND=grants``, or for a worker, an unset kind with
``ADA_VIEWER_API_URL`` set and no ``ADA_VIEWER_S3_BUCKET`` / ``ADA_VIEWER_LOCAL_PATH``
(``config.worker_storage_kind``). A worker with storage settings is unaffected.

See ``deploy/remote-workers.md``.
"""

from __future__ import annotations

from .bootstrap import BootstrapError, WorkerBootstrap, fetch_bootstrap, parse_bootstrap
from .grants import (
    Grant,
    GrantClient,
    GrantRefusedError,
    GrantUnavailableError,
    grant_subject,
)
from .job import (
    GrantModeStorage,
    GrantModeUnsupported,
    JobGrantSession,
    active_grant_storage,
)
from .nats_conf import nats_conf_entry, render_nats_conf_entry
from .preflight import (
    AUTH_VIOLATION,
    PERMISSION_VIOLATION,
    Check,
    check_seed,
    check_version_floor,
    classify_nats_error,
    probe_grant,
    public_key_of_seed,
)
from .storage import RemoteGrantStorage, RemoteStorageError

__all__ = [
    "AUTH_VIOLATION",
    "PERMISSION_VIOLATION",
    "BootstrapError",
    "Check",
    "Grant",
    "GrantClient",
    "GrantModeStorage",
    "GrantModeUnsupported",
    "GrantRefusedError",
    "GrantUnavailableError",
    "JobGrantSession",
    "RemoteGrantStorage",
    "RemoteStorageError",
    "WorkerBootstrap",
    "active_grant_storage",
    "check_seed",
    "check_version_floor",
    "classify_nats_error",
    "fetch_bootstrap",
    "grant_subject",
    "nats_conf_entry",
    "parse_bootstrap",
    "probe_grant",
    "public_key_of_seed",
    "render_nats_conf_entry",
]
