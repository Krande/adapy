// `LayoutPersistence` behind a per-user preferences endpoint: the backend in
// hosted (REST) mode, so a user's layout follows them between browsers.
//
// Saves are debounced: a drag or a resize commits on every stop, and only the
// last prefs of a burst need to reach the server. `flush` sends a pending save
// at once (on page hide). Where the server keeps no preferences (no database,
// offline), or before the user has saved anything there, the `fallback` (the
// browser-local backend) serves instead. Dependency-free: the endpoint is
// passed in as a `PreferenceStore`.

import type { LayoutPersistence, LayoutPrefs } from "./layout";

/** The preference document the layout is kept under. */
export const LAYOUT_PREFERENCE_KEY = "layout";

export const LAYOUT_SAVE_DEBOUNCE_MS = 750;

/** One user's preference documents on the server. */
export interface PreferenceStore {
  /** The stored value, or `null` when there is none. */
  get(key: string): Promise<unknown>;
  /** `keepalive` asks the request to outlive the page. */
  put(key: string, value: unknown, opts?: { keepalive?: boolean }): Promise<void>;
}

export interface ServerLayoutPersistenceOptions {
  /** Used when the server cannot keep preferences, and read once while it holds none yet. */
  fallback?: LayoutPersistence;
  key?: string;
  debounceMs?: number;
}

export type ServerLayoutPersistence = LayoutPersistence & {
  /** Send a pending save now. */
  flush(opts?: { keepalive?: boolean }): Promise<void>;
};

export function serverLayoutPersistence(
  store: PreferenceStore,
  { fallback, key = LAYOUT_PREFERENCE_KEY, debounceMs = LAYOUT_SAVE_DEBOUNCE_MS }: ServerLayoutPersistenceOptions = {},
): ServerLayoutPersistence {
  // Once a request fails, the rest of the page's saves go to the fallback.
  let serverDown = false;
  let pending: LayoutPrefs | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const send = async (prefs: LayoutPrefs, keepalive: boolean) => {
    if (serverDown) return fallback?.save(prefs);
    try {
      await store.put(key, prefs, keepalive ? { keepalive } : undefined);
    } catch (err) {
      serverDown = true;
      console.warn("[layout] saving to the server failed; keeping the layout in this browser", err);
      await fallback?.save(prefs);
    }
  };

  const flush = async ({ keepalive = false }: { keepalive?: boolean } = {}) => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    const prefs = pending;
    pending = null;
    if (prefs) await send(prefs, keepalive);
  };

  return {
    async load() {
      let value: unknown;
      try {
        value = await store.get(key);
      } catch (err) {
        serverDown = true;
        console.warn("[layout] the server keeps no layout here; using this browser's", err);
        return fallback ? fallback.load() : null;
      }
      // Nothing on the server yet: start from what this browser had. The next
      // change saves it to the server.
      if (value === null || value === undefined) return fallback ? fallback.load() : null;
      return value as LayoutPrefs;
    },
    async save(prefs) {
      pending = prefs;
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => void flush(), debounceMs);
    },
    flush,
  };
}
