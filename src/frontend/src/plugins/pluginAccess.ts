// Scope-gated plugins (1.41.0): a plugin that opts in (`PluginSpec.access`) is shown only where an admin
// turned it on -- for the open scope, or for the signed-in user (Admin > Plugins). The server decides
// (`GET /scopes/{scope}/plugin-access`, `ada/comms/rest/plugin_access.py`); this module keeps its answer
// for the open scope, asks again on every scope switch, and is what the registry's hosts consult, so a
// gated plugin's top-bar buttons, panels, property sections and commands go away without the plugin
// doing anything. `ctx.access` lets a plugin read the same state.
//
// Until the answer for the open scope is in, a gated plugin is hidden: it appears when it is on rather
// than flashing up and away. A server without a database gates nothing (`enforced: false`).

/** What a plugin registration declares: `"scope"` (gated under its own id) or `{gate}` (gated under a
 *  shared id, so several registrations of one plugin -- its panels, its admin tab, its depth camera --
 *  have one switch). */
export type PluginAccessSpec = "scope" | { gate: string };

/** The server's answer for the signed-in user in one scope. */
export interface PluginAccessAnswer {
  scope: string;
  /** False on a server without a database: nothing is gated there. */
  enforced: boolean;
  /** The gates turned on for this user in this scope. */
  enabled: string[];
  plugins?: Record<string, { enabled: boolean; via: "user" | "scope" | null }>;
}

interface AccessState {
  /** The scope (URL form) the answer is for; null before the first one. */
  scope: string | null;
  loaded: boolean;
  enforced: boolean;
  enabled: ReadonlySet<string>;
  /** Why the last ask failed, for a plugin that wants to say so. */
  error: string | null;
}

const EMPTY: ReadonlySet<string> = new Set();

// Plain module state, no store library: the registry reads it and is kept free of runtime
// dependencies (see registry.ts). A React host re-renders on `subscribePluginAccess`.
let state: AccessState = { scope: null, loaded: false, enforced: true, enabled: EMPTY, error: null };

/** The answer kept now (a snapshot; replaced, never mutated, on a change). */
export function pluginAccessState(): Readonly<AccessState> {
  return state;
}

function setState(next: Partial<AccessState>): void {
  state = { ...state, ...next };
}

const GATE_ID = /^[a-z0-9][a-z0-9._-]{0,79}$/;

/** The gate id a registration's `access` names, or null for an ungated plugin (or a malformed
 *  declaration, which is warned about and then treated as gated under the plugin id: a typo must not
 *  open what the author meant to close). */
export function accessGateOf(pluginId: string, access: PluginAccessSpec | undefined | null): string | null {
  if (access === undefined || access === null) return null;
  const raw = access === "scope" ? pluginId : typeof access === "object" ? access.gate : undefined;
  const gate = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  if (GATE_ID.test(gate)) return gate;
  console.warn(`[plugins] plugin "${pluginId}": access ${JSON.stringify(access)} names no gate; gated as "${pluginId}"`);
  return pluginId.trim().toLowerCase();
}

/** Whether a gate is open now: no gate, or the server said it is on in the open scope (or gates
 *  nothing). Closed until the answer is in. */
export function isGateOpen(gate: string | null | undefined): boolean {
  if (!gate) return true;
  const s = pluginAccessState();
  if (!s.loaded) return false;
  return !s.enforced || s.enabled.has(gate.trim().toLowerCase());
}

// ── listeners: the registry and the slot hosts re-ask when the answer changes ──────────────────────

const listeners = new Set<() => void>();

/** Called on every change of what is open; returns the unsubscribe. */
export function subscribePluginAccess(cb: () => void): () => void {
  listeners.add(cb);
  return () => void listeners.delete(cb);
}

function changed(): void {
  for (const cb of [...listeners]) {
    try {
      cb();
    } catch (err) {
      console.error("[plugins] a plugin access listener threw", err);
    }
  }
}

/** Put the server's answer in (and tell every listener when something changed). */
export function setPluginAccess(answer: PluginAccessAnswer, scope: string): void {
  const before = pluginAccessState();
  const enabled = new Set((answer.enabled ?? []).map((g) => String(g).toLowerCase()));
  const same =
    before.loaded &&
    before.scope === scope &&
    before.enforced === !!answer.enforced &&
    before.enabled.size === enabled.size &&
    [...enabled].every((g) => before.enabled.has(g));
  setState({ scope, loaded: true, enforced: !!answer.enforced, enabled, error: null });
  if (!same) changed();
}

/** Forget the answer: every gated plugin is hidden until the next one (a scope switch in flight, a
 *  failed ask). */
export function clearPluginAccess(scope: string | null = null, error: string | null = null): void {
  const before = pluginAccessState();
  setState({ scope, loaded: false, enforced: true, enabled: EMPTY, error });
  if (before.loaded) changed();
}

// ── asking the server ─────────────────────────────────────────────────────────────────────────────

type Fetcher = (scope: string) => Promise<PluginAccessAnswer>;
let fetcher: Fetcher | null = null;
let asked = 0;

/** How the answer is fetched (core's REST client at bootstrap; a fake in tests). */
export function setPluginAccessFetcher(fn: Fetcher | null): void {
  fetcher = fn;
}

/** Ask the server about `scope` (URL form) and keep the answer. Stale answers (a scope switched again
 *  meanwhile) are dropped. A failed ask hides every gated plugin. */
export async function refreshPluginAccess(scope: string): Promise<void> {
  const ticket = ++asked;
  if (pluginAccessState().scope !== scope) clearPluginAccess(scope);
  if (!fetcher) return;
  try {
    const answer = await fetcher(scope);
    if (ticket === asked) setPluginAccess(answer, scope);
  } catch (err) {
    if (ticket === asked) clearPluginAccess(scope, err instanceof Error ? err.message : String(err));
  }
}

// Nothing is asked until something gated is registered (core, the property sections or the commands
// say so here): a build whose plugins do not opt in makes no request at all.
let gatedSeen = false;
let onFirstGated: (() => void) | null = null;

/** Note that something gated was registered; the first note starts the asking (`onFirstGated`). */
export function noteGatedRegistration(): void {
  if (gatedSeen) return;
  gatedSeen = true;
  onFirstGated?.();
}

export const anyGatedRegistration = (): boolean => gatedSeen;

/** What to do when the first gated thing is registered (the bootstrap: ask for the open scope). Runs
 *  at once when one already was. */
export function whenGatedRegistered(fn: (() => void) | null): void {
  onFirstGated = fn;
  if (fn && gatedSeen) fn();
}

// ── `ctx.access` ──────────────────────────────────────────────────────────────────────────────────

/** `ctx.access` (1.41.0): whether this plugin is turned on where the user is. */
export interface PluginAccess {
  /** The gate this plugin is registered under, or null when it does not opt in (always on). */
  readonly gate: string | null;
  /** Whether it is on now, for the open scope or for the user. Always true for an ungated plugin. */
  enabled: () => boolean;
  /** Whether the answer for the open scope is in yet (a gated plugin reads as off until it is). */
  loaded: () => boolean;
  /** Called with `enabled()` whenever it may have changed (a scope switch, an answer); returns the
   *  unsubscribe. */
  subscribe: (cb: (enabled: boolean) => void) => () => void;
  /** Ask the server again for the open scope (after an admin changed a switch in another tab). */
  refresh: () => Promise<void>;
}

export function makePluginAccess(gate: () => string | null, scope: () => string): PluginAccess {
  return {
    get gate() {
      return gate();
    },
    enabled: () => isGateOpen(gate()),
    loaded: () => !gate() || pluginAccessState().loaded,
    subscribe: (cb) => subscribePluginAccess(() => cb(isGateOpen(gate()))),
    refresh: () => refreshPluginAccess(scope()),
  };
}

/** Clear everything -- unit tests only. */
export function resetPluginAccess(): void {
  asked = 0;
  fetcher = null;
  gatedSeen = false;
  onFirstGated = null;
  // Listeners stay: core's own registries subscribe once, at import.
  setState({ scope: null, loaded: false, enforced: true, enabled: EMPTY, error: null });
}
