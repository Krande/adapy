// `LayoutPersistence` in the browser's localStorage: the backend where there is
// no per-user server store (desktop / websocket viewer, embeds), and the
// fallback of the server backend (`./serverPersistence`). Dependency-free: the storage is passed in (or read
// from the browser), and blocked storage keeps the prefs for the page only.

import type { LayoutPersistence, LayoutPrefs } from "./layout";

export const LAYOUT_PREFS_STORAGE_KEY = "ada:layout-prefs";

/** The part of `Storage` the backend uses. */
export interface PrefsStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function browserStorage(): PrefsStorage | null {
  try {
    return typeof globalThis.localStorage !== "undefined" ? globalThis.localStorage : null;
  } catch {
    return null;
  }
}

export function localLayoutPersistence(
  storage: PrefsStorage | null = browserStorage(),
  key: string = LAYOUT_PREFS_STORAGE_KEY,
): LayoutPersistence {
  return {
    async load() {
      let raw: string | null = null;
      try {
        raw = storage?.getItem(key) ?? null;
      } catch {
        return null;
      }
      if (!raw) return null;
      try {
        return JSON.parse(raw) as LayoutPrefs;
      } catch {
        return null;
      }
    },
    async save(prefs) {
      try {
        storage?.setItem(key, JSON.stringify(prefs));
      } catch {
        // Kept for this page only.
      }
    },
  };
}
