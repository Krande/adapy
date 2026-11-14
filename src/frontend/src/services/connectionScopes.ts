// The admin's assignment of worker pools' connection specs to scopes (`public.connection_scopes`,
// read by routes/connections.py): `{capability: [scope, ...]}`. A capability it does not name is
// offered in every scope; an empty list offers it nowhere; core's built-ins are always offered.
// A scope is its wire form (`shared`, `project:<id>`), and `user` stands for every personal scope.
//
// Dependency-free: the admin section and its tests share these.

export const CONNECTION_SCOPES_KEY = "public.connection_scopes";

/** Every personal scope, as the setting names them. */
export const PERSONAL_SCOPES = "user";

export type ConnectionScopesMap = Readonly<Record<string, readonly string[]>>;

/** The stored value, or `{}` for anything unreadable (which offers every spec everywhere). */
export function parseConnectionScopes(raw: string | null | undefined): ConnectionScopesMap {
  if (!raw) return {};
  try {
    const v = JSON.parse(raw) as unknown;
    if (!v || typeof v !== "object" || Array.isArray(v)) return {};
    const out: Record<string, string[]> = {};
    for (const [cap, scopes] of Object.entries(v as Record<string, unknown>)) {
      if (Array.isArray(scopes)) out[cap] = scopes.filter((s): s is string => typeof s === "string");
    }
    return out;
  } catch {
    return {};
  }
}

export function serialiseConnectionScopes(map: ConnectionScopesMap): string {
  return JSON.stringify(map);
}

/** The setting's name for an admin row's scope: a personal scope is `user`, whosever it is. */
export function settingScopeFor(rowScope: string): string {
  return rowScope === PERSONAL_SCOPES || rowScope.startsWith("user:") ? PERSONAL_SCOPES : rowScope;
}

/** The scopes `capability` is restricted to, or null where it is offered everywhere. */
export function scopesFor(map: ConnectionScopesMap, capability: string): readonly string[] | null {
  const v = map[capability];
  return Array.isArray(v) ? v : null;
}

/** `capability` offered everywhere (`null`) or only in `scopes`. */
export function withScopes(map: ConnectionScopesMap, capability: string, scopes: readonly string[] | null): ConnectionScopesMap {
  const next: Record<string, readonly string[]> = { ...map };
  if (scopes === null) delete next[capability];
  else next[capability] = [...new Set(scopes)].sort();
  return next;
}

/** Tick or untick one scope for a restricted capability (an unrestricted one is restricted to it). */
export function toggleScope(map: ConnectionScopesMap, capability: string, scope: string, on: boolean): ConnectionScopesMap {
  const cur = scopesFor(map, capability) ?? [];
  return withScopes(map, capability, on ? [...cur, scope] : cur.filter((s) => s !== scope));
}

/** Whether `scope` offers `capability`'s specs -- the server's rule, for the UI's summary. */
export function offersIn(map: ConnectionScopesMap, capability: string, scope: string): boolean {
  const scopes = scopesFor(map, capability);
  return scopes === null || scopes.includes(settingScopeFor(scope));
}
