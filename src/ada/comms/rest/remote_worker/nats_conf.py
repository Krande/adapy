"""A remote worker's entry in the server's ``nats.conf``: its key and exactly what it may do.

:func:`nats_conf_entry` returns the permissions as data, :func:`render_nats_conf_entry` as the
text that goes into an ``authorization { users = [ ... ] }`` (or an account's ``users``) block.
Generic: the stream, buckets and subjects are parameters with adapy's defaults.

What the entry allows, and why each line is there:

subscribe
    ``_INBOX_<name>.>``                 its private reply inbox (``QueueConfig.inbox_prefix``): job
                                        pulls, KV reads and grant replies all answer here, and no
                                        other client's replies do
    ``<subject>.<capability>``          each capability's job subject

publish
    ``<grant prefix>.<capability>.<name>``  ask for a job grant, as itself only
    ``$JS.API.CONSUMER.INFO|CREATE|DURABLE.CREATE|MSG.NEXT ...<durable>-<capability>``
                                        bind and pull its pools' durable consumers, nothing else's
    ``$JS.ACK.<stream>.<durable>-<capability>.>``  ack / keep-alive those messages
    ``$JS.API.STREAM.INFO.KV_<bucket>``, ``$JS.API.DIRECT.GET.KV_<bucket>.>``, ``$KV.<bucket>.*``
                                        read and update job status (the residual risk in
                                        ``deploy/worker-trust.md`` §6: job ids are random, so this
                                        cannot be pinned per job)
    ``$KV.<registry bucket>.__meta_worker__<worker id>``  its own registry row, when the registry
                                        has a bucket of its own

Nothing here allows ``_INBOX.>``, stream administration, another pool's consumer, or the grant
bindings bucket.
"""

from __future__ import annotations

import json
import re

from ..config import DEFAULT_GRANT_SUBJECT_PREFIX, WORKER_NAME_PATTERN
from ..queue import capability_token

__all__ = ["nats_conf_entry", "render_nats_conf_entry"]

_PUBLIC_KEY = re.compile(r"^U[A-Z2-7]{55}$")


def nats_conf_entry(
    *,
    public_key: str,
    worker_name: str,
    capabilities: list[str],
    worker_id: str | None = None,
    subject: str = "ada.viewer.jobs.convert",
    stream: str = "ADA_VIEWER_JOBS",
    durable: str = "ada-viewer-worker",
    jobs_bucket: str = "ada-viewer-jobs",
    registry_bucket: str = "",
    grant_prefix: str = DEFAULT_GRANT_SUBJECT_PREFIX,
    extra_publish: list[str] | None = None,
    extra_subscribe: list[str] | None = None,
) -> dict:
    """The entry as ``{"nkey": ..., "permissions": {"publish": {"allow": [...]}, "subscribe":
    {"allow": [...]}}}``. ``public_key`` is the worker's ``U...`` key (never the seed: one is
    refused). ``worker_id`` is the registry row's id when it differs from ``worker_name``."""
    key = (public_key or "").strip()
    if key.startswith("S"):
        raise ValueError("that is a seed, not a public key: nats.conf names the public key (U...)")
    if not _PUBLIC_KEY.match(key):
        raise ValueError("not an nkey user public key (U followed by 55 base32 characters)")
    if not WORKER_NAME_PATTERN.match(worker_name or ""):
        raise ValueError(f"worker name {worker_name!r} is not one subject token")
    caps = [c for c in (capability_token(c) for c in capabilities) if c]
    if not caps:
        raise ValueError("a worker entry needs at least one capability")
    publish: list[str] = []
    subscribe = [f"_INBOX_{worker_name}.>"]
    for cap in caps:
        consumer = f"{durable}-{cap}"
        subscribe.append(f"{subject}.{cap}")
        publish += [
            f"{grant_prefix}.{cap}.{worker_name}",
            f"$JS.API.CONSUMER.INFO.{stream}.{consumer}",
            f"$JS.API.CONSUMER.CREATE.{stream}.{consumer}",
            f"$JS.API.CONSUMER.CREATE.{stream}.{consumer}.>",
            f"$JS.API.CONSUMER.DURABLE.CREATE.{stream}.{consumer}",
            f"$JS.API.CONSUMER.MSG.NEXT.{stream}.{consumer}",
            f"$JS.ACK.{stream}.{consumer}.>",
        ]
    publish += [
        f"$JS.API.STREAM.INFO.KV_{jobs_bucket}",
        f"$JS.API.DIRECT.GET.KV_{jobs_bucket}.>",
        f"$JS.API.STREAM.MSG.GET.KV_{jobs_bucket}",
        f"$KV.{jobs_bucket}.*",
    ]
    if registry_bucket and registry_bucket != jobs_bucket:
        publish += [
            f"$JS.API.STREAM.INFO.KV_{registry_bucket}",
            f"$KV.{registry_bucket}.__meta_worker__{worker_id or worker_name}",
        ]
    publish += list(extra_publish or [])
    subscribe += list(extra_subscribe or [])
    return {
        "nkey": key,
        "permissions": {
            "publish": {"allow": list(dict.fromkeys(publish))},
            "subscribe": {"allow": list(dict.fromkeys(subscribe))},
        },
    }


def render_nats_conf_entry(entry: dict, *, comment: str | None = None, indent: str = "  ") -> str:
    """``entry`` as ``nats.conf`` text, one subject a line (``json.dumps`` quoting is valid there)."""
    perms = entry["permissions"]
    lines = []
    if comment:
        lines += [f"{indent}# {line}" for line in comment.splitlines()]
    lines.append(f"{indent}{{")
    lines.append(f"{indent}  nkey: {json.dumps(entry['nkey'])}")
    lines.append(f"{indent}  permissions: {{")
    for side in ("publish", "subscribe"):
        lines.append(f"{indent}    {side}: {{")
        lines.append(f"{indent}      allow: [")
        lines += [f"{indent}        {json.dumps(s)}" for s in perms[side]["allow"]]
        lines.append(f"{indent}      ]")
        lines.append(f"{indent}    }}")
    lines.append(f"{indent}  }}")
    lines.append(f"{indent}}}")
    return "\n".join(lines) + "\n"
