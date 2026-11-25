-- 056_plugin_access.sql -- which scope, and which user, may use a scope-gated plugin.
--
-- A plugin opts into the gate (its frontend registration says `access: "scope"`, a simulation
-- provider or backend plugin spec of it says `access_gate`). A gated plugin is OFF everywhere until
-- an admin turns it on here. A plugin that does not opt in is never looked up in this table.
--
-- One row per (principal, plugin):
--   principal_kind 'scope' -- `principal` is a storage prefix (`shared`, `projects/<uuid>`,
--                             `users/<sub>`), as everywhere else in the database: everyone working
--                             in that scope may use the plugin there.
--   principal_kind 'user'  -- `principal` is a user's OIDC sub: that user may use the plugin in
--                             every scope they can open.
--
-- The effective rule (``ada.comms.rest.plugin_access.effective_enabled``): the user's own row
-- decides when there is one -- `enabled = false` is an explicit "off for this user" that wins over
-- an "on" for the scope; else the scope's row; else off. A scope row with `enabled = false` is the
-- same as no row (kept so an admin's "off" leaves a trace of who turned it off).
--
-- `plugin_id` is the gate's id: the plugin's id, or the id several registrations of one plugin
-- share (`access: {gate: "<id>"}`). Lower-case, as the registry and the routes keep it.

CREATE TABLE IF NOT EXISTS plugin_access (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_kind  TEXT NOT NULL,
    principal       TEXT NOT NULL,
    plugin_id       TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_by      TEXT,
    note            TEXT,
    CONSTRAINT plugin_access_kind CHECK (principal_kind IN ('scope', 'user')),
    CONSTRAINT plugin_access_principal_not_blank CHECK (length(principal) > 0),
    -- Not the provider store and not a corpus: nobody works in either with a plugin's UI.
    CONSTRAINT plugin_access_scope_prefix CHECK (
        principal_kind <> 'scope'
        OR principal = 'shared'
        OR principal LIKE 'projects/_%'
        OR principal LIKE 'users/_%'
    ),
    CONSTRAINT plugin_access_plugin_id CHECK (plugin_id ~ '^[a-z0-9][a-z0-9._-]{0,79}$')
);

-- One row per (principal, plugin): setting it again replaces it.
CREATE UNIQUE INDEX IF NOT EXISTS plugin_access_identity
    ON plugin_access (principal_kind, principal, plugin_id);

-- The read path's lookup: "what is on for this scope and this user".
CREATE INDEX IF NOT EXISTS plugin_access_principal_idx ON plugin_access (principal_kind, principal);
-- The admin's view from the other side: "where is this plugin on".
CREATE INDEX IF NOT EXISTS plugin_access_plugin_idx ON plugin_access (plugin_id);
