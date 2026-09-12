"""asyncpg pool lifecycle (create with retry, apply migrations, close)."""

from __future__ import annotations

from typing import Optional

import asyncpg

from ada.config import logger

from .migrations import _apply_migrations


async def init_pool(database_url: str) -> Optional[asyncpg.Pool]:
    """Build a connection pool and apply pending migrations.

    Returns ``None`` when ``database_url`` is empty so callers can
    branch into shared-only mode without a try/except.

    Retries connection failures with exponential backoff. The pod
    can come up before kube-dns or before Postgres has finished
    accepting connections, and a one-shot init that gives up on the
    first ``socket.gaierror`` was leaving the API permanently in
    shared-only mode (admin endpoints all returning 503) until
    someone manually rolled the pod. Total budget ~60s — long enough
    to ride out cold-start ordering, short enough that a real
    misconfig still surfaces in the readiness probe.
    """
    if not database_url:
        logger.info("db: DATABASE_URL not set — running in shared-only mode")
        return None
    import asyncio as _asyncio

    delay = 1.0
    deadline = 60.0
    waited = 0.0
    while True:
        try:
            pool = await asyncpg.create_pool(
                dsn=database_url,
                min_size=1,
                max_size=10,
                # Kill idle connections after 10 min — Postgres servers behind
                # load balancers (PgBouncer, Garage's PG, managed Postgres) are
                # finicky about long-idle conns.
                max_inactive_connection_lifetime=600.0,
            )
            break
        except (OSError, asyncpg.exceptions.PostgresError) as exc:
            if waited >= deadline:
                logger.error(
                    "db: pool init still failing after %.0fs — giving up: %s",
                    waited,
                    exc,
                )
                raise
            logger.warning(
                "db: pool init failed (%s); retry in %.1fs (waited %.1fs/%.0fs)",
                exc,
                delay,
                waited,
                deadline,
            )
            await _asyncio.sleep(delay)
            waited += delay
            delay = min(delay * 1.6, 8.0)
    try:
        await _apply_migrations(pool)
    except Exception:
        await pool.close()
        raise
    logger.info("db: pool ready, migrations up-to-date")
    return pool


async def close_pool(pool: Optional[asyncpg.Pool]) -> None:
    if pool is not None:
        await pool.close()
