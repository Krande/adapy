import React, {useEffect, useMemo, useState} from "react";

import {ApiError, viewerApi} from "@/services/viewerApi";
import type {AdminUser, AdminUserWorkerAccess} from "@/services/viewerApi";
import {auditHash} from "../adminTabs";
import {fmtTimestamp} from "../scheduleFormat";
import {filterUsers, userLabel, userScopes} from "./userScopes";

const DEFAULT_WORKER_ACCESS: AdminUserWorkerAccess = {
    enabled: false,
    allowed_capabilities: [],
    updated_by: null,
    updated_at: null,
};

function userWorkerAccess(u: AdminUser): AdminUserWorkerAccess {
    return u.worker_access ?? DEFAULT_WORKER_ACCESS;
}

const UsersTab: React.FC<{
    /** Open the Audit tab filtered to this user. Supplied by AdminPanel so the
     * link also works embedded, where there is no URL hash to navigate. */
    onOpenAudit: (userSub: string) => void;
}> = ({onOpenAudit}) => {
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [knownCapabilities, setKnownCapabilities] = useState<string[]>([]);
    const [savingSub, setSavingSub] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [query, setQuery] = useState("");
    const [expanded, setExpanded] = useState<string | null>(null);

    const reload = async () => {
        setLoading(true);
        try {
            const [userList, workersResp] = await Promise.all([
                viewerApi.adminListUsers(),
                viewerApi.adminListWorkers().catch(() => ({workers: []})),
            ]);
            setUsers(userList);
            const capSet = new Set<string>();
            for (const w of workersResp.workers) {
                for (const c of w.capabilities ?? []) {
                    const clean = c.trim().toLowerCase();
                    if (clean) capSet.add(clean);
                }
            }
            for (const u of userList) {
                for (const c of u.worker_access?.allowed_capabilities ?? []) {
                    const clean = c.trim().toLowerCase();
                    if (clean) capSet.add(clean);
                }
            }
            setKnownCapabilities(Array.from(capSet).sort());
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

    const updateWorkerAccess = async (
        userSub: string,
        next: {enabled: boolean; allowed_capabilities: string[]},
    ) => {
        setSavingSub(userSub);
        try {
            const updated = await viewerApi.adminUpdateUserWorkerAccess(userSub, next);
            setUsers((prev) =>
                prev.map((u) =>
                    u.sub === userSub
                        ? {
                              ...u,
                              worker_access: {
                                  enabled: updated.enabled,
                                  allowed_capabilities: updated.allowed_capabilities,
                                  updated_by: updated.updated_by,
                                  updated_at: updated.updated_at,
                              },
                          }
                        : u,
                ),
            );
            setKnownCapabilities((prev) => {
                const merged = new Set(prev);
                for (const c of updated.allowed_capabilities) merged.add(c);
                return Array.from(merged).sort();
            });
            setError(null);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setSavingSub(null);
        }
    };

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
                        saving={savingSub === u.sub}
                        knownCapabilities={knownCapabilities}
                        onToggle={() => setExpanded((cur) => (cur === u.sub ? null : u.sub))}
                        onOpenAudit={onOpenAudit}
                        onUpdateWorkerAccess={(next) => void updateWorkerAccess(u.sub, next)}
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
    saving: boolean;
    knownCapabilities: string[];
    onToggle: () => void;
    onOpenAudit: (userSub: string) => void;
    onUpdateWorkerAccess: (next: {enabled: boolean; allowed_capabilities: string[]}) => void;
}> = ({user: u, open, saving, knownCapabilities, onToggle, onOpenAudit, onUpdateWorkerAccess}) => {
    const panelId = `admin-user-${u.sub}`;
    const wa = userWorkerAccess(u);
    const capCount = wa.allowed_capabilities.length;

    return (
        <li>
            <div
                className={
                    "w-full px-3 sm:px-4 py-3 sm:py-2 hover:bg-gray-800 flex items-center gap-2 " +
                    (open ? "bg-gray-800" : "")
                }
            >
                <button
                    type="button"
                    className="flex-1 min-w-0 text-left flex items-center gap-2"
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
                </button>
                {u.is_admin ? (
                    <>
                        <span className="rounded-sm bg-amber-900/60 text-amber-200 px-1 text-[10px] uppercase shrink-0">
                            admin
                        </span>
                        <span
                            className="rounded-sm bg-emerald-900/40 text-emerald-300 px-1.5 py-0.5 text-[10px] shrink-0"
                            title="Admins always have worker pool access"
                        >
                            workers: always
                        </span>
                    </>
                ) : (
                    <button
                        type="button"
                        disabled={saving}
                        aria-pressed={wa.enabled}
                        data-testid={`worker-access-toggle-${u.sub}`}
                        title={
                            wa.enabled
                                ? "Worker pool access enabled for all capabilities (click to disable)"
                                : capCount > 0
                                  ? `Worker pool access enabled for ${capCount} capability override(s) (click to enable all)`
                                  : "Worker pool access disabled — browser WASM only (click to enable)"
                        }
                        onClick={() =>
                            onUpdateWorkerAccess({
                                enabled: !wa.enabled,
                                allowed_capabilities: wa.allowed_capabilities,
                            })
                        }
                        className={
                            "rounded-sm px-2 py-0.5 text-[11px] border shrink-0 transition-colors disabled:opacity-50 " +
                            (wa.enabled
                                ? "bg-emerald-900/50 border-emerald-700 text-emerald-200 hover:bg-emerald-800/60"
                                : capCount > 0
                                  ? "bg-blue-900/40 border-blue-700 text-blue-200 hover:bg-blue-800/50"
                                  : "bg-gray-800 border-gray-700 text-gray-400 hover:bg-gray-700")
                        }
                    >
                        {saving
                            ? "Saving…"
                            : wa.enabled
                              ? "Workers: ON"
                              : capCount > 0
                                ? `Workers: ${capCount} cap${capCount === 1 ? "" : "s"}`
                                : "Workers: OFF"}
                    </button>
                )}
                <span className="text-xs text-gray-500 shrink-0 hidden sm:inline">
                    {u.projects.length} project{u.projects.length === 1 ? "" : "s"}
                </span>
            </div>
            {open && (
                <UserDetail
                    id={panelId}
                    user={u}
                    saving={saving}
                    knownCapabilities={knownCapabilities}
                    onOpenAudit={onOpenAudit}
                    onUpdateWorkerAccess={onUpdateWorkerAccess}
                />
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
    saving: boolean;
    knownCapabilities: string[];
    onOpenAudit: (userSub: string) => void;
    onUpdateWorkerAccess: (next: {enabled: boolean; allowed_capabilities: string[]}) => void;
}> = ({id, user: u, saving, knownCapabilities, onOpenAudit, onUpdateWorkerAccess}) => {
    const scopes = userScopes(u);
    const wa = userWorkerAccess(u);
    const [customCap, setCustomCap] = useState("");
    // A real href, so middle-click / "copy link" give a shareable URL; a
    // plain click is handled in-app so the embedded panel (no hash routing)
    // works too.
    const auditHref = `/admin#${auditHash(u.sub, "log")}`;
    // Recorded at sign-in since the claims migration; null before that.
    const notRecorded = "Not recorded yet — filled in at the user's next sign-in";

    const capabilities = useMemo(() => {
        const s = new Set<string>(knownCapabilities);
        for (const c of wa.allowed_capabilities) s.add(c);
        return Array.from(s).sort();
    }, [knownCapabilities, wa.allowed_capabilities]);

    const toggleCapability = (cap: string) => {
        const current = new Set(wa.allowed_capabilities);
        if (current.has(cap)) current.delete(cap);
        else current.add(cap);
        onUpdateWorkerAccess({
            enabled: wa.enabled,
            allowed_capabilities: Array.from(current).sort(),
        });
    };

    const addCustomCapability = () => {
        const clean = customCap.trim().toLowerCase();
        if (!clean) return;
        const current = new Set(wa.allowed_capabilities);
        current.add(clean);
        setCustomCap("");
        onUpdateWorkerAccess({
            enabled: wa.enabled,
            allowed_capabilities: Array.from(current).sort(),
        });
    };

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

            <div className="border-t border-gray-700/70 pt-2 space-y-2">
                <div className="text-[11px] uppercase tracking-wide text-gray-400">Worker Pool Access</div>
                {u.is_admin ? (
                    <p className="text-gray-400">
                        Administrators always have full access to the server worker pool.
                    </p>
                ) : (
                    <div className="space-y-2">
                        <label className="flex items-center gap-2 cursor-pointer select-none">
                            <input
                                type="checkbox"
                                checked={wa.enabled}
                                disabled={saving}
                                onChange={(e) =>
                                    onUpdateWorkerAccess({
                                        enabled: e.target.checked,
                                        allowed_capabilities: wa.allowed_capabilities,
                                    })
                                }
                            />
                            <span>Allow all server worker pool jobs</span>
                        </label>
                        <div>
                            <div className="text-gray-400 mb-1">
                                Per-capability overrides{" "}
                                {wa.enabled && (
                                    <span className="text-gray-500">(all capabilities already allowed)</span>
                                )}
                            </div>
                            {capabilities.length > 0 ? (
                                <div className="flex flex-wrap gap-2">
                                    {capabilities.map((cap) => {
                                        const checked = wa.allowed_capabilities.includes(cap);
                                        return (
                                            <label
                                                key={cap}
                                                className="inline-flex items-center gap-1.5 bg-gray-800 border border-gray-700 rounded-sm px-2 py-0.5 cursor-pointer select-none"
                                            >
                                                <input
                                                    type="checkbox"
                                                    checked={checked}
                                                    disabled={saving}
                                                    data-testid={`worker-cap-${u.sub}-${cap}`}
                                                    onChange={() => toggleCapability(cap)}
                                                />
                                                <span className="font-mono text-[11px]">{cap}</span>
                                            </label>
                                        );
                                    })}
                                </div>
                            ) : (
                                <div className="text-gray-500">
                                    No advertised capabilities online yet. Add a capability name below to grant it.
                                </div>
                            )}
                            <div className="flex items-center gap-1.5 mt-2">
                                <input
                                    className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-0.5 text-white font-mono text-[11px] w-44"
                                    placeholder="Add capability (e.g. abaqus)"
                                    value={customCap}
                                    disabled={saving}
                                    onChange={(e) => setCustomCap(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter") {
                                            e.preventDefault();
                                            addCustomCapability();
                                        }
                                    }}
                                />
                                <button
                                    type="button"
                                    disabled={saving || !customCap.trim()}
                                    onClick={addCustomCapability}
                                    className="bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded-sm disabled:opacity-50"
                                >
                                    Add
                                </button>
                            </div>
                        </div>
                        {wa.updated_at && (
                            <div className="text-[11px] text-gray-500">
                                Updated {fmtTimestamp(wa.updated_at)}
                                {wa.updated_by ? ` by ${wa.updated_by}` : ""}
                            </div>
                        )}
                    </div>
                )}
            </div>

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
