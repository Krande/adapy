-- 033_audit_issue_claims.sql — which forge issue each failure fingerprint was given.
--
-- The issue-bot found a fingerprint's issue by label search and opened one when the search
-- came back empty. Two syncs of one fingerprint at once (an audit run's pass and a user
-- conversion's, or two replicas) both saw nothing and both opened an issue, and the
-- forge's label search lags a fresh issue by seconds, so even back-to-back syncs did.
--
-- Each sync of a fingerprint now runs under a Postgres advisory lock on it, and records
-- here the issue it commented on or opened. The next sync reads the number from this table
-- instead of asking the lagging search. ``target`` is the forge kind + repo the number
-- belongs to, so pointing the bot at another repo starts with no claims rather than
-- borrowing issue numbers from the old one.

CREATE TABLE IF NOT EXISTS audit_issue_claims (
    target       TEXT NOT NULL,
    fp           TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (target, fp)
);
