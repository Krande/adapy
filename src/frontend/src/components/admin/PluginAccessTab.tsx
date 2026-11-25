import React, {useCallback, useEffect, useMemo, useState} from "react";

import {SHARED_TARGET, projectTarget, targetLabel, userTarget} from "@/assets/providerGrants";
import {getRegisteredPlugins} from "@/plugins/registry";
import {refreshPluginAccess} from "@/plugins/pluginAccess";
import {fuzzyFilter} from "@/services/fuzzy";
import type {PluginAccessGate, PluginAccessRow} from "@/services/api/pluginAccess";
import {ApiError, viewerApi, type AdminProject, type AdminUser} from "@/services/viewerApi";
import {useMeStore} from "@/state/meStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {sortProjectsBy} from "@/utils/sortProjects";

import AdminSection from "./AdminSection";
import {gateIds, principalsWithRows, scopeSwitch, summary, userSwitch, type UserSwitch} from "./pluginAccessModel";

// Admin > Plugins: the plugins that are scope-gated (they register with `access`), turned on per scope
// and per user. A gated plugin is OFF until it is turned on here: then its buttons, panels and
// context-menu entries show, and the server starts its simulation runs and jobs. The rule:
//
//   the user's own switch decides when there is one -- On, or Off, which wins over the scope's On;
//   else the scope's switch; else off.
//
// It applies to admins too: to try a plugin, turn it on for your personal scope or for yourself.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));

interface Target {
    id: string;
    label: string;
    hint: string;
}

/** Ask again for the scope on screen: an admin who just turned a plugin on for themselves sees it. */
function refreshOwnView(): void {
    const cur = useScopeStore.getState().current;
    if (cur) void refreshPluginAccess(scopeUrlPart(cur));
}

const ScopeRows: React.FC<{
    scopes: readonly Target[];
    gates: readonly string[];
    rows: readonly PluginAccessRow[];
    onChanged: () => void;
}> = ({scopes, gates, rows, onChanged}) => {
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const flip = async (scope: string, gate: string, on: boolean) => {
        setBusy(`${scope}\u0000${gate}`);
        setError(null);
        try {
            if (on) {
                await viewerApi.adminSetPluginAccess({principal_kind: "scope", principal: scope, plugin_id: gate, enabled: true});
            } else {
                await viewerApi.adminClearPluginAccess({principal_kind: "scope", principal: scope, plugin_id: gate});
            }
            onChanged();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(null);
        }
    };
    return (
        <div className="overflow-x-auto">
            {error && <div className="px-1 py-1 text-xs text-red-300">{error}</div>}
            <table className="w-full text-xs">
                <thead className="text-left text-gray-500">
                    <tr>
                        <th className="px-2 py-1 font-normal">Scope</th>
                        {gates.map((g) => (
                            <th key={g} className="px-2 py-1 font-normal font-mono">
                                {g}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {scopes.map((s) => (
                        <tr key={s.id} className="border-t border-gray-800" data-testid={`plugin-access-scope-${s.id}`}>
                            <td className="px-2 py-1.5">
                                <span className="text-gray-100">{s.label}</span>{" "}
                                <span className="text-gray-500">{s.hint}</span>
                            </td>
                            {gates.map((g) => (
                                <td key={g} className="px-2 py-1.5">
                                    <input
                                        type="checkbox"
                                        checked={scopeSwitch(rows, s.id, g)}
                                        disabled={busy !== null}
                                        aria-label={`${g} on in ${s.label}`}
                                        onChange={(e) => void flip(s.id, g, e.target.checked)}
                                    />
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

const UserRows: React.FC<{
    users: readonly Target[];
    gates: readonly string[];
    rows: readonly PluginAccessRow[];
    onChanged: () => void;
}> = ({users, gates, rows, onChanged}) => {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const set = async (sub: string, gate: string, next: UserSwitch) => {
        setBusy(true);
        setError(null);
        try {
            if (next === "inherit") {
                await viewerApi.adminClearPluginAccess({principal_kind: "user", principal: sub, plugin_id: gate});
            } else {
                await viewerApi.adminSetPluginAccess({principal_kind: "user", principal: sub, plugin_id: gate, enabled: next === "on"});
            }
            onChanged();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="overflow-x-auto">
            {error && <div className="px-1 py-1 text-xs text-red-300">{error}</div>}
            <table className="w-full text-xs">
                <thead className="text-left text-gray-500">
                    <tr>
                        <th className="px-2 py-1 font-normal">User</th>
                        {gates.map((g) => (
                            <th key={g} className="px-2 py-1 font-normal font-mono">
                                {g}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {users.map((u) => (
                        <tr key={u.id} className="border-t border-gray-800" data-testid={`plugin-access-user-${u.id}`}>
                            <td className="px-2 py-1.5">
                                <span className="text-gray-100">{u.label}</span>{" "}
                                <span className="text-gray-500 font-mono">{u.hint}</span>
                            </td>
                            {gates.map((g) => (
                                <td key={g} className="px-2 py-1.5">
                                    <select
                                        value={userSwitch(rows, u.id, g)}
                                        disabled={busy}
                                        aria-label={`${g} for ${u.label}`}
                                        className="rounded-sm border border-gray-700 bg-gray-800 px-1 py-0.5 text-xs text-gray-100"
                                        onChange={(e) => void set(u.id, g, e.target.value as UserSwitch)}
                                    >
                                        <option value="inherit">As the scope says</option>
                                        <option value="on">On</option>
                                        <option value="off">Off</option>
                                    </select>
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

const PluginAccessTab: React.FC = () => {
    const [rows, setRows] = useState<PluginAccessRow[] | null>(null);
    const [serverGates, setServerGates] = useState<PluginAccessGate[]>([]);
    const [projects, setProjects] = useState<AdminProject[]>([]);
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [scopeFilter, setScopeFilter] = useState("");
    const [userFilter, setUserFilter] = useState("");
    const mySub = useMeStore((s) => s.sub);
    const myName = useMeStore((s) => s.displayName || s.email);

    const load = useCallback(async () => {
        try {
            const [access, projs, people] = await Promise.all([
                viewerApi.adminPluginAccess(),
                // For the names only; the switches stay usable without them.
                viewerApi.adminListProjects().catch(() => [] as AdminProject[]),
                viewerApi.adminListUsers().catch(() => [] as AdminUser[]),
            ]);
            setRows(access.rows);
            setServerGates(access.gates);
            setProjects(projs.filter((p) => !p.archived_at));
            setUsers(people);
            setError(null);
        } catch (e) {
            setError(errText(e));
        }
    }, []);
    useEffect(() => {
        void load();
    }, [load]);
    const changed = useCallback(() => {
        void load();
        refreshOwnView();
    }, [load]);

    const gates = useMemo(
        () => gateIds(serverGates, getRegisteredPlugins().map((p) => p.accessGate), rows ?? []),
        [serverGates, rows],
    );
    const projectNames = useMemo(() => new Map(projects.map((p) => [p.id, p.name])), [projects]);
    const scopes: Target[] = useMemo(() => {
        const out: Target[] = [{id: SHARED_TARGET, label: "Shared", hint: "everyone with access to this viewer"}];
        if (mySub) out.push({id: userTarget(mySub), label: "Your personal scope", hint: `${myName || mySub} only`});
        for (const p of sortProjectsBy(projects, (x) => x.name)) out.push({id: projectTarget(p.id), label: p.name, hint: p.slug});
        // A scope with a switch that is not listed above (another user's personal scope, an archived project).
        for (const s of principalsWithRows(rows ?? [], "scope")) {
            if (!out.some((t) => t.id === s)) out.push({id: s, label: targetLabel(s, projectNames), hint: s});
        }
        return out;
    }, [projects, mySub, myName, rows, projectNames]);
    const people: Target[] = useMemo(() => {
        const out: Target[] = users.map((u) => ({id: u.sub, label: u.display_name || u.email || u.sub, hint: u.sub}));
        for (const s of principalsWithRows(rows ?? [], "user")) {
            if (!out.some((t) => t.id === s)) out.push({id: s, label: s, hint: s});
        }
        return out.sort((a, b) => a.label.localeCompare(b.label));
    }, [users, rows]);
    const shownScopes = fuzzyFilter(scopes, scopeFilter, (s) => `${s.label} ${s.hint} ${summary(rows ?? [], "scope", s.id)}`);
    const shownUsers = fuzzyFilter(people, userFilter, (u) => `${u.label} ${u.hint} ${summary(rows ?? [], "user", u.id)}`);

    const refresh = (
        <button
            type="button"
            className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800"
            onClick={() => void load()}
        >
            Refresh
        </button>
    );

    return (
        <div className="h-full overflow-y-auto p-3 space-y-3" data-testid="plugin-access-admin">
            <div className="text-xs text-gray-400 space-y-1">
                <p>
                    Plugins that are <em>scope-gated</em> are off until they are turned on here. Turned on for a scope, everyone
                    working in it sees the plugin and can run it; turned on for a user, that user can in every scope they open.
                </p>
                <p>
                    A user's own switch wins: <b>Off</b> for a user hides the plugin from them even where the scope has it on.
                    Admins are no exception; to try a plugin, turn it on for your personal scope or for yourself.
                </p>
            </div>
            {error && <div className="text-xs text-red-300">{error}</div>}
            {rows !== null && gates.length === 0 && (
                <div className="text-xs text-gray-500">No plugin of this build or this server is scope-gated.</div>
            )}
            {rows !== null && gates.length > 0 && (
                <>
                    <AdminSection title="Per scope" kind="Plugin access" storageKey="ada.admin.plugins.open.scopes" testId="plugin-access-scopes" actions={refresh}>
                        <div className="px-3 py-2 space-y-1.5">
                            {scopes.length > 6 && (
                                <input
                                    type="search"
                                    value={scopeFilter}
                                    onChange={(e) => setScopeFilter(e.target.value)}
                                    placeholder={`Filter ${scopes.length} scopes…`}
                                    aria-label="Filter scopes"
                                    className="w-64 rounded-sm border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-100 placeholder:text-gray-500"
                                />
                            )}
                            <ScopeRows scopes={shownScopes} gates={gates} rows={rows} onChanged={changed} />
                        </div>
                    </AdminSection>
                    <AdminSection title="Per user" kind="Plugin access" storageKey="ada.admin.plugins.open.users" testId="plugin-access-users">
                        <div className="px-3 py-2 space-y-1.5">
                            <input
                                type="search"
                                value={userFilter}
                                onChange={(e) => setUserFilter(e.target.value)}
                                placeholder={`Filter ${people.length} users…`}
                                aria-label="Filter users"
                                className="w-64 rounded-sm border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-100 placeholder:text-gray-500"
                            />
                            <UserRows users={shownUsers} gates={gates} rows={rows} onChanged={changed} />
                        </div>
                    </AdminSection>
                </>
            )}
        </div>
    );
};

export default PluginAccessTab;
