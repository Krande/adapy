// Admin > Plugins, without React: which gates there are and what each switch says. The rule the server
// applies is in `ada/comms/rest/plugin_access.py`: a user's own switch decides when there is one (on,
// or an off that wins over the scope's on), else the scope's, else off.

import type {PluginAccessGate, PluginAccessRow} from "@/services/api/pluginAccess";

/** What a user's switch for one plugin is: none (the scope decides), on, or off. */
export type UserSwitch = "inherit" | "on" | "off";

/** Every gate to offer a switch for, sorted: what the server knows (its providers, backend plugins,
 *  bindings and switches) and what this build registered with `access`. */
export function gateIds(
    server: readonly PluginAccessGate[],
    registered: readonly (string | null | undefined)[],
    rows: readonly PluginAccessRow[] = [],
): string[] {
    const ids = new Set<string>();
    for (const g of server) ids.add(g.id);
    for (const g of registered) if (g) ids.add(g);
    for (const r of rows) ids.add(r.plugin_id);
    return [...ids].sort();
}

function find(rows: readonly PluginAccessRow[], kind: "scope" | "user", principal: string, gate: string) {
    return rows.find((r) => r.principal_kind === kind && r.principal === principal && r.plugin_id === gate);
}

/** Whether a scope's switch for `gate` is on (a scope's off is the same as none). */
export function scopeSwitch(rows: readonly PluginAccessRow[], scope: string, gate: string): boolean {
    return !!find(rows, "scope", scope, gate)?.enabled;
}

export function userSwitch(rows: readonly PluginAccessRow[], sub: string, gate: string): UserSwitch {
    const row = find(rows, "user", sub, gate);
    return row ? (row.enabled ? "on" : "off") : "inherit";
}

/** The scopes and users that have a switch, beside the ones a list already shows. */
export function principalsWithRows(rows: readonly PluginAccessRow[], kind: "scope" | "user"): string[] {
    return [...new Set(rows.filter((r) => r.principal_kind === kind).map((r) => r.principal))].sort();
}

/** One line on what is on for a scope or a user: "robot-sim" / "robot-sim: off" / "nothing". */
export function summary(rows: readonly PluginAccessRow[], kind: "scope" | "user", principal: string): string {
    const mine = rows.filter((r) => r.principal_kind === kind && r.principal === principal);
    if (kind === "scope") {
        const on = mine.filter((r) => r.enabled).map((r) => r.plugin_id).sort();
        return on.length ? on.join(", ") : "nothing on";
    }
    if (!mine.length) return "as the scope says";
    return mine
        .map((r) => `${r.plugin_id}: ${r.enabled ? "on" : "off"}`)
        .sort()
        .join(", ");
}
