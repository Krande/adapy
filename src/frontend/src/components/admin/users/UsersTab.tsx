import React, {useEffect, useMemo, useState} from "react";

import {ApiError, viewerApi} from "@/services/viewerApi";
import type {AdminUser} from "@/services/viewerApi";
import {auditHash} from "../adminTabs";
import {fmtTimestamp} from "../scheduleFormat";
import {filterUsers, userLabel, userScopes} from "./userScopes";

// The Users tab: who has an account here, where they belong, and a way into
// what they did.
//
// Read-only on purpose. Membership is edited from the project side
// (ProjectsTab), where the role picker and the CI-bot controls live; a second
// place to change the same rows would be a second place for them to disagree.
// This is the other axis of the same data — start from a person rather than a
// project.
//
// One list with rows that expand in place, rather than ProjectsTab's two-pane
// layout: the detail is short, and an accordion reads the same on a phone as
// on a desktop without a separate mobile branch.

const UsersTab: React.FC<{
    /** Open the Audit tab filtered to this user. Supplied by AdminPanel so the
     * link also works embedded, where there is no URL hash to navigate. */
    onOpenAudit: (userSub: string) => void;
}> = ({onOpenAudit}) => {
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [query, setQuery] = useState("");
    const [expanded, setExpanded] = useState<string | null>(null);

    const reload = async () => {
        setLoading(true);
        try {
            setUsers(await viewerApi.adminListUsers());
            setError(null);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void reload();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const shown = useMemo(() => filterUsers(users, query), [users, query]);

    return (
        <div className="flex flex-col h-full">
            <div className="flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-gray-700 text-xs">
                <input
                    className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 w-full sm:w-72 text-white"
                    placeholder="Search name, email or OIDC sub…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                />
                <span className="text-gray-500 whitespace-nowrap">
                    {query ? `${shown.length} of ${users.length}` : `${users.length}`} user
                    {users.length === 1 ? "" : "s"}
                </span>
                <button
                    className="ml-auto bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded-sm disabled:opacity-50"
                    onClick={() => void reload()}
                    disabled={loading}
                >
                    {loading ? "Loading…" : "Refresh"}
                </button>
            </div>
            {error && (
                <div className="px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700">
                    {error}
                </div>
            )}
            <ul className="flex-1 min-h-0 overflow-auto divide-y divide-gray-800">
                {shown.map((u) => (
                    <UserRow
                        key={u.sub}
                        user={u}
                        open={expanded === u.sub}
                        onToggle={() => setExpanded((cur) => (cur === u.sub ? null : u.sub))}
                        onOpenAudit={onOpenAudit}
                    />
                ))}
                {!loading && shown.length === 0 && (
                    <li className="px-4 py-8 text-center text-gray-500 text-sm">
                        {users.length === 0 ? "No users recorded yet." : "No user matches the search."}
                    </li>
                )}
            </ul>
        </div>
    );
};

const UserRow: React.FC<{
    user: AdminUser;
    open: boolean;
    onToggle: () => void;
    onOpenAudit: (userSub: string) => void;
}> = ({user: u, open, onToggle, onOpenAudit}) => {
    const panelId = `admin-user-${u.sub}`;
    return (
        <li>
            <button
                className={
                    "w-full text-left px-3 sm:px-4 py-3 sm:py-2 hover:bg-gray-800 flex items-center gap-2 " +
                    (open ? "bg-gray-800" : "")
                }
                onClick={onToggle}
                aria-expanded={open}
                aria-controls={panelId}
            >
                <span className="text-gray-500 text-xs w-3 shrink-0">{open ? "▾" : "▸"}</span>
                <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium truncate" title={userLabel(u)}>
                        {userLabel(u)}
                    </span>
                    <span className="block text-[11px] text-gray-400 font-mono truncate" title={u.sub}>
                        {u.sub}
                    </span>
                </span>
                {u.is_admin && (
                    <span className="rounded-sm bg-amber-900/60 text-amber-200 px-1 text-[10px] uppercase shrink-0">
                        admin
                    </span>
                )}
                <span className="text-xs text-gray-500 shrink-0 hidden sm:inline">
                    {u.projects.length} project{u.projects.length === 1 ? "" : "s"}
                </span>
            </button>
            {open && (
                <UserDetail id={panelId} user={u} onOpenAudit={onOpenAudit}/>
            )}
        </li>
    );
};

const Field: React.FC<{label: string; children: React.ReactNode}> = ({label, children}) => (
    <>
        <dt className="text-gray-400">{label}</dt>
        <dd className="min-w-0 break-words">{children}</dd>
    </>
);

const Unknown: React.FC<{title?: string}> = ({title}) => (
    <span className="text-gray-500 italic" title={title}>
        unknown
    </span>
);

const UserDetail: React.FC<{
    id: string;
    user: AdminUser;
    onOpenAudit: (userSub: string) => void;
}> = ({id, user: u, onOpenAudit}) => {
    const scopes = userScopes(u);
    // A real href, so middle-click / "copy link" give a shareable URL; a
    // plain click is handled in-app so the embedded panel (no hash routing)
    // works too.
    const auditHref = `/admin#${auditHash(u.sub, "log")}`;
    // Recorded at sign-in since the claims migration; null before that.
    const notRecorded = "Not recorded yet — filled in at the user's next sign-in";

    return (
        <div id={id} className="px-3 sm:px-4 pb-3 pt-1 sm:pl-9 bg-gray-800/40 text-xs space-y-3">
            <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1">
                <Field label="Full name">{u.display_name || <Unknown/>}</Field>
                <Field label="Email">
                    {u.email ? (
                        <a className="text-blue-400 hover:text-blue-300" href={`mailto:${u.email}`}>
                            {u.email}
                        </a>
                    ) : (
                        <Unknown/>
                    )}
                </Field>
                <Field label="OIDC sub">
                    <span className="font-mono select-all">{u.sub}</span>
                </Field>
                <Field label="Admin">
                    {u.is_admin === null ? <Unknown title={notRecorded}/> : u.is_admin ? "yes" : "no"}
                </Field>
                <Field label="Groups">
                    {u.groups === null ? (
                        <Unknown title={notRecorded}/>
                    ) : u.groups.length === 0 ? (
                        <span className="text-gray-500">none</span>
                    ) : (
                        <span className="flex flex-wrap gap-1">
                            {u.groups.map((g) => (
                                <span
                                    key={g}
                                    className="bg-gray-700 rounded-sm px-1 font-mono text-[11px] max-w-[20rem] truncate"
                                    title={g}
                                >
                                    {g}
                                </span>
                            ))}
                        </span>
                    )}
                </Field>
                <Field label="Account created">
                    {u.created_at ? fmtTimestamp(u.created_at) : <Unknown/>}
                </Field>
                <Field label="Last sign-in">{fmtTimestamp(u.last_seen_at)}</Field>
                <Field label="First activity">{fmtTimestamp(u.first_activity_at)}</Field>
                <Field label="Last activity">{fmtTimestamp(u.last_activity_at)}</Field>
            </dl>

            <div>
                <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Scopes</div>
                <ul className="space-y-0.5">
                    {scopes.map((s) => (
                        <li key={`${s.kind}:${s.id ?? ""}`} className="flex items-center gap-2">
                            <span className="rounded-sm bg-gray-700 text-gray-300 px-1 text-[10px] w-14 text-center shrink-0">
                                {s.kind}
                            </span>
                            <span className={"truncate " + (s.archived ? "text-gray-500 line-through" : "")}>
                                {s.name}
                            </span>
                            {s.role && <span className="text-gray-400">{s.role}</span>}
                            {s.archived && (
                                <span className="text-[10px] uppercase text-gray-500">archived</span>
                            )}
                            {s.implicit && s.kind !== "corpus" && (
                                <span className="text-gray-500">every account</span>
                            )}
                        </li>
                    ))}
                </ul>
            </div>

            <a
                className="inline-block bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded-sm text-blue-300 hover:text-blue-200"
                href={auditHref}
                title="Open the audit log filtered to this user"
                onClick={(e) => {
                    // Let modified clicks (new tab / window) take the href.
                    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                    e.preventDefault();
                    onOpenAudit(u.sub);
                }}
            >
                Audit log →
            </a>
        </div>
    );
};

export default UsersTab;
