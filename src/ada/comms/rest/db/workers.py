"""Worker image package inventories."""

from __future__ import annotations

import json

import asyncpg

from ._common import _loads_jsonb


async def upsert_worker_packages(pool: asyncpg.Pool, *, worker_image_tag: str, packages: list) -> None:
    """Record a worker image's package manifest (idempotent per image tag)."""
    await pool.execute(
        """
        INSERT INTO worker_packages (worker_image_tag, packages, captured_at)
        VALUES ($1, $2, now())
        ON CONFLICT (worker_image_tag) DO UPDATE
          SET packages = EXCLUDED.packages, captured_at = now()
        """,
        worker_image_tag,
        json.dumps(packages),
    )


async def get_worker_packages(pool: asyncpg.Pool, worker_image_tag: str) -> dict | None:
    """The captured package manifest for a worker image tag (or None)."""
    row = await pool.fetchrow(
        "SELECT worker_image_tag, packages, captured_at FROM worker_packages WHERE worker_image_tag = $1",
        worker_image_tag,
    )
    if row is None:
        return None
    return {
        "worker_image_tag": row["worker_image_tag"],
        "packages": _loads_jsonb(row["packages"]) or [],
        "captured_at": row["captured_at"].isoformat() if row["captured_at"] else None,
    }
