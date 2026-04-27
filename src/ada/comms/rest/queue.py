"""NATS JetStream-backed conversion job queue.

Two NATS primitives are used together:

* a JetStream **stream** with `WORK_QUEUE` retention — work-queue
  semantics mean a message is removed once acked, so each job is
  processed exactly once across any number of workers.
* a JetStream **KV bucket** — point-in-time status lookups for the
  frontend to poll. The work-queue stream alone can't answer
  "what's the state of job X right now?" efficiently.

The queue stream carries only the `job_id` as the message body; the
full Job record (status, progress, error, ...) is stored in KV under
that id and updated as the worker progresses.

Both connect-time stream/bucket creation are idempotent; restarts are
safe.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass

import nats
from nats.js.api import RetentionPolicy, StreamConfig
from nats.js.errors import BadRequestError, BucketNotFoundError, KeyNotFoundError

from .config import QueueConfig
from .converter import derived_key_for


JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_ERROR = "error"


@dataclass
class Job:
    job_id: str
    source_key: str
    derived_key: str
    status: str
    target_format: str = "glb"
    progress: float = 0.0
    stage: str = ""
    error: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    # Scope under which the source/derived blobs live. Defaults to
    # "shared" for backward compat with phase-1 jobs already in flight
    # at the moment of upgrade.
    scope_kind: str = "shared"
    scope_id: str | None = None

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> "Job":
        # Tolerate older serialized jobs that pre-date scope_kind /
        # scope_id by ignoring unknown fields and supplying defaults.
        data = json.loads(raw.decode("utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class QueueDisabled(RuntimeError):
    """Raised when queue operations are attempted but no NATS URL is configured."""


class JobQueue:
    """Connection-managed wrapper around NATS JetStream + KV."""

    def __init__(self, cfg: QueueConfig):
        self._cfg = cfg
        self._nc = None
        self._js = None
        self._kv = None

    # --- lifecycle ---------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._cfg.url is not None

    async def connect(self) -> None:
        if not self.enabled:
            raise QueueDisabled("ADA_VIEWER_NATS_URL not set")
        self._nc = await nats.connect(self._cfg.url)
        self._js = self._nc.jetstream()

        # Stream — idempotent.
        try:
            await self._js.add_stream(
                StreamConfig(
                    name=self._cfg.stream,
                    subjects=[self._cfg.subject],
                    retention=RetentionPolicy.WORK_QUEUE,
                )
            )
        except BadRequestError:
            # already exists with compatible config
            pass

        # KV bucket — idempotent.
        try:
            self._kv = await self._js.create_key_value(bucket=self._cfg.kv_bucket, history=1)
        except BadRequestError:
            self._kv = await self._js.key_value(self._cfg.kv_bucket)

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()
            self._nc = None
            self._js = None
            self._kv = None

    # --- producer side (called from API) -----------------------------

    async def enqueue(
        self,
        source_key: str,
        target_format: str = "glb",
        *,
        scope_kind: str = "shared",
        scope_id: str | None = None,
    ) -> Job:
        now = time.time()
        job = Job(
            job_id=uuid.uuid4().hex,
            source_key=source_key,
            derived_key=derived_key_for(source_key, target_format),
            status=JOB_STATUS_QUEUED,
            target_format=target_format,
            progress=0.0,
            stage="queued",
            created_at=now,
            updated_at=now,
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
        await self._put(job)
        await self._js.publish(self._cfg.subject, job.job_id.encode("utf-8"))
        return job

    async def get(self, job_id: str) -> Job | None:
        try:
            entry = await self._kv.get(job_id)
        except KeyNotFoundError:
            return None
        except BucketNotFoundError:
            return None
        return Job.from_json(entry.value)

    async def update(self, job_id: str, **fields) -> Job | None:
        job = await self.get(job_id)
        if job is None:
            return None
        for k, v in fields.items():
            setattr(job, k, v)
        job.updated_at = time.time()
        await self._put(job)
        return job

    async def _put(self, job: Job) -> None:
        await self._kv.put(job.job_id, job.to_json())

    # --- consumer side (called from worker) --------------------------

    async def pull_subscribe(self):
        """Create a durable pull-subscriber on the work-queue stream.

        Workers fetch in batches; the durable name (from config) lets
        multiple worker pods share the same consumer cursor.
        """
        return await self._js.pull_subscribe(
            subject=self._cfg.subject,
            durable=self._cfg.durable,
        )


__all__ = [
    "Job",
    "JobQueue",
    "QueueDisabled",
    "JOB_STATUS_QUEUED",
    "JOB_STATUS_RUNNING",
    "JOB_STATUS_DONE",
    "JOB_STATUS_ERROR",
]
