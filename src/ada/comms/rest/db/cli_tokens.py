"""Issued CLI bearer tokens: the record behind listing and per-token revocation.

The token string is never stored, only its claims and a short ``hint`` (see
migration 034). Verification reads a row by ``jti`` on every CLI-token request,
so the lookup is a primary-key fetch and ``last_used_at`` is only rewritten
when it is more than a minute stale.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg

_MIGRATION = "034_cli_tokens"

_COLUMNS = """
    jti, sub, email, display_name, is_admin, label, hint, issued_by,
    issued_at, expires_at, last_used_at, revoked_at, revoked_by
"""


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def _cli_token_row(row) -> dict:
    return {
        "jti": row["jti"],
        "sub": row["sub"],
        "email": row["email"],
        "display_name": row["display_name"],
        "is_admin": row["is_admin"],
        "label": row["label"],
        "hint": row["hint"],
        "issued_by": row["issued_by"],
        "issued_at": _iso(row["issued_at"]),
        "expires_at": _iso(row["expires_at"]),
        "last_used_at": _iso(row["last_used_at"]),
        "revoked_at": _iso(row["revoked_at"]),
        "revoked_by": row["revoked_by"],
    }


async def insert_cli_token(
    pool: asyncpg.Pool,
    *,
    jti: str,
    sub: str,
    email: str | None,
    display_name: str | None,
    is_admin: bool,
    label: str | None,
    hint: str,
    issued_by: str | None,
    issued_at: int,
    expires_at: int,
) -> None:
    await pool.execute(
        """
        INSERT INTO cli_tokens (jti, sub, email, display_name, is_admin, label, hint,
                                issued_by, issued_at, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, to_timestamp($9), to_timestamp($10))
        """,
        jti,
        sub,
        email,
        display_name,
        is_admin,
        label,
        hint,
        issued_by,
        issued_at,
        expires_at,
    )


async def check_cli_token(pool: asyncpg.Pool, jti: str) -> str:
    """``"active"``, ``"revoked"`` or ``"unknown"`` for a presented token's jti.

    Marks the token used as a side effect, at most once a minute, so a busy CI
    bot does not turn every request into a write.
    """
    row = await pool.fetchrow("SELECT revoked_at, last_used_at FROM cli_tokens WHERE jti = $1", jti)
    if row is None:
        return "unknown"
    if row["revoked_at"] is not None:
        return "revoked"
    await pool.execute(
        """
        UPDATE cli_tokens SET last_used_at = NOW()
        WHERE jti = $1 AND (last_used_at IS NULL OR last_used_at < NOW() - INTERVAL '1 minute')
        """,
        jti,
    )
    return "active"


async def list_cli_tokens(
    pool: asyncpg.Pool,
    *,
    include_inactive: bool = False,
    sub: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Newest first. Inactive means revoked or expired."""
    rows = await pool.fetch(
        f"""
        SELECT {_COLUMNS} FROM cli_tokens
        WHERE ($1 OR (revoked_at IS NULL AND expires_at > NOW()))
          AND ($2::text IS NULL OR sub = $2)
        ORDER BY issued_at DESC
        LIMIT $3
        """,
        include_inactive,
        sub,
        limit,
    )
    return [_cli_token_row(r) for r in rows]


async def revoke_cli_token(pool: asyncpg.Pool, jti: str, *, revoked_by: str | None) -> dict | None:
    """Revoke one token. Idempotent: an already-revoked row keeps its original
    ``revoked_at``. ``None`` when no token has that jti."""
    row = await pool.fetchrow(
        f"""
        UPDATE cli_tokens
        SET revoked_at = COALESCE(revoked_at, NOW()),
            revoked_by = COALESCE(revoked_by, $2)
        WHERE jti = $1
        RETURNING {_COLUMNS}
        """,
        jti,
        revoked_by,
    )
    return _cli_token_row(row) if row else None


async def revoke_cli_tokens_for_sub(pool: asyncpg.Pool, sub: str, *, revoked_by: str | None) -> int:
    """Mark every live row for ``sub`` revoked; the "revoke all" counterpart of
    the app_settings cutoff, so the list agrees with what verification does."""
    status = await pool.execute(
        """
        UPDATE cli_tokens SET revoked_at = NOW(), revoked_by = $2
        WHERE sub = $1 AND revoked_at IS NULL
        """,
        sub,
        revoked_by,
    )
    return int(status.split()[-1])


async def cli_tokens_tracked_since(pool: asyncpg.Pool) -> str | None:
    """When per-token tracking started on this deployment, i.e. when migration
    034 was applied. Tokens issued before then are not in the table."""
    v = await pool.fetchval("SELECT applied_at FROM schema_version WHERE version = $1", _MIGRATION)
    return _iso(v)
