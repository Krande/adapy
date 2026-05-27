// Admin-tab identifier. Used as the URL hash on /admin (e.g.
// /admin#audit_runs deep-links straight to the audit-runs tab).
// Anchor links elsewhere in the SPA reference these values directly
// — no shared store is needed since the admin page is path-mounted.
export type AdminTab =
    | "audit"
    | "audit_runs"
    | "corpus"
    | "projects"
    | "storage"
    | "workers"
    | "conversion";
