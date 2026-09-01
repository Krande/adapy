// Admin-tab identifier. Used as the URL hash on /admin (e.g.
// /admin#audit_runs deep-links straight to the audit-runs tab).
// Anchor links elsewhere in the SPA reference these values directly
// — no shared store is needed since the admin page is path-mounted.
export type AdminTab =
    // "audit" now hosts what used to be four sibling tabs — audit_runs,
    // schedules and corpus are sub-tabs of it. Their old hashes still resolve;
    // see LEGACY_AUDIT_HASHES in AdminPanel.
    | "audit"
    | "issues"
    | "performance"
    | "frontend_loads"
    | "projects"
    | "external_models"
    | "storage"
    | "workers"
    | "conversion"
    | "equipment"
    | "system"
    | "engines";

// Ids that may appear in a deep link: the current tabs, plus the three that
// became sub-tabs of "audit". The retired ids are still live in bookmarks,
// browser history and in-app triggers (the audit-sweep toast opens
// "audit_runs"), so they stay accepted and are resolved to the right sub-tab
// rather than quietly landing on the wrong panel.
export type AdminTabDeepLink = AdminTab | "audit_runs" | "corpus" | "schedules";
