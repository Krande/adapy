// Admin tab identity and hash routing.
//
// Deliberately free of React and of any component import: the routing rules are
// the part worth testing, and AdminPanel transitively pulls the whole viewer —
// including a vite-only `?worker&inline` import that no plain test runner can
// resolve. Keeping the rules here makes them testable without a bundler, and
// makes the tab ids greppable in one place.

/** Sub-tab descriptor, shared by every grouped tab's strip. */
export interface SubTabSpec<Id extends string> {
    id: Id;
    label: string;
    /** Optional count shown after the label. Rendered by AdminSubTabs.
     *
     * Typed as a scalar rather than ReactNode so this module stays React-free —
     * a badge is only ever a count. */
    badge?: string | number;
}

export type AuditSubTab = "overview" | "log" | "runs" | "corpora" | "schedules";
export type PerformanceSubTab = "workers" | "frontend";
export type ProceduralSubTab = "engines" | "systems" | "equipment";

export const AUDIT_SUB_TABS: readonly SubTabSpec<AuditSubTab>[] = [
    {id: "overview", label: "Overview"},
    {id: "log", label: "Log"},
    {id: "runs", label: "Runs"},
    {id: "corpora", label: "Corpora"},
    {id: "schedules", label: "Schedules"},
];

export const PERFORMANCE_SUB_TABS: readonly SubTabSpec<PerformanceSubTab>[] = [
    {id: "workers", label: "Workers"},
    {id: "frontend", label: "Frontend"},
];

export const PROCEDURAL_SUB_TABS: readonly SubTabSpec<ProceduralSubTab>[] = [
    {id: "engines", label: "Engines"},
    {id: "systems", label: "Systems"},
    {id: "equipment", label: "Equipment"},
];

/** Top-level tabs after the grouping. */
export const VALID_TABS: ReadonlySet<string> = new Set([
    "audit",
    "issues",
    "performance",
    "projects",
    "users",
    "external_models",
    "providers",
    "storage",
    "workers",
    "conversion",
    "procedural",
]);

/** Hashes that used to be top-level tabs, mapped to where they went.
 *
 * Kept as redirects rather than dropped: they are in bookmarks, in browser
 * history and in in-app triggers (the conversion toast opens "audit_runs"). A
 * deep link that silently lands on the WRONG panel is worse than one that
 * fails, because nothing tells the operator it happened. */
export const LEGACY_TAB_HASHES: Readonly<Record<string, {tab: string; sub: string}>> = {
    // → Audit
    audit_runs: {tab: "audit", sub: "runs"},
    corpus: {tab: "audit", sub: "corpora"},
    schedules: {tab: "audit", sub: "schedules"},
    // → Performance
    frontend_loads: {tab: "performance", sub: "frontend"},
    // → Procedural Engine
    engines: {tab: "procedural", sub: "engines"},
    system: {tab: "procedural", sub: "systems"},
    equipment: {tab: "procedural", sub: "equipment"},
};

const GROUPED: Readonly<Record<string, readonly SubTabSpec<string>[]>> = {
    audit: AUDIT_SUB_TABS,
    performance: PERFORMANCE_SUB_TABS,
    procedural: PROCEDURAL_SUB_TABS,
};

/** Resolve a hash (or an in-app deep-link id) to a tab and optional sub-tab.
 *
 * ``extra`` carries plugin panel ids so a deep link to one survives a reload.
 * Plugin ids contain a colon and never a slash, so splitting on "/" cannot
 * corrupt one.
 *
 * An unknown SUB-tab yields ``sub: undefined`` — the tab's own default — rather
 * than falling through to a different tab: landing on Performance's first
 * sub-tab is right, landing on Audit is not. */
export function parseTabId(raw: string, extra: ReadonlySet<string>): {tab: string; sub?: string} {
    const legacy = LEGACY_TAB_HASHES[raw];
    if (legacy) return {tab: legacy.tab, sub: legacy.sub};

    const [head, rest] = raw.split("/", 2);
    const group = GROUPED[head];
    if (group) return {tab: head, sub: group.find((t) => t.id === rest)?.id};

    if (VALID_TABS.has(raw) || extra.has(raw)) return {tab: raw};
    return {tab: "audit"};
}

/** The one audit filter the hash carries: ``#audit?user_sub=<sub>``.
 *
 * Only the user, deliberately. It is the filter other panels hand over (the
 * Users tab's "Audit" link) and the one worth pasting to a colleague — "here
 * is everything this person did". The rest of the bar stays in-memory, for the
 * reason auditFilterStore gives: a filter is a step in an investigation, and a
 * URL that silently restores six of them is the hidden-filter failure the
 * chips exist to prevent. */
export const AUDIT_USER_PARAM = "user_sub";

/** Split ``audit/log?user_sub=x`` into the tab path parseTabId expects and the
 * query. Parsing the query separately keeps parseTabId's rules untouched —
 * ``?`` never appears in a tab, sub-tab or plugin id. */
export function parseAdminHash(
    raw: string,
    extra: ReadonlySet<string>,
): {tab: string; sub?: string; userSub?: string} {
    const q = raw.indexOf("?");
    const path = q < 0 ? raw : raw.slice(0, q);
    const target = parseTabId(path, extra);
    if (q < 0 || target.tab !== "audit") return target;
    const userSub = new URLSearchParams(raw.slice(q + 1)).get(AUDIT_USER_PARAM)?.trim();
    return userSub ? {...target, userSub} : target;
}

/** Hash for the audit tab, optionally narrowed to one user. ``sub`` picks the
 * landing sub-tab; the query is URL-encoded, so a sub with ``/``, ``?``, ``#``
 * or ``:`` (CI bots are ``ci:<slug>:<name>``) survives the round trip. */
export function auditHash(userSub?: string, sub?: AuditSubTab): string {
    const path = sub ? `audit/${sub}` : "audit";
    if (!userSub) return path;
    return `${path}?${new URLSearchParams({[AUDIT_USER_PARAM]: userSub}).toString()}`;
}
