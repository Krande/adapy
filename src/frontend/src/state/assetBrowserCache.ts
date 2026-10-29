// The last asset tree a viewer opened in a scope: its collection list, the open
// collection and that collection's index, kept in browser storage so the tab can
// draw it at once and revalidate behind it.

import type { WireAssetIndex } from "@/assets/types";

export interface CachedAssetTree {
  collections: string[];
  collection: string;
  index: WireAssetIndex;
}

export interface AssetTreeCache {
  read(scope: string): CachedAssetTree | null;
  /** `indexJson` is the collection index as JSON text, already serialised by the caller. */
  write(scope: string, collections: readonly string[], collection: string, indexJson: string): void;
  forget(scope: string): void;
}

const KEY_PREFIX = "ada.assets.lastTree.v1.";
/** In UTF-16 units. Above this an entry is dropped rather than crowd out other settings. */
const MAX_ENTRY = 2_000_000;

function isTree(v: unknown): v is CachedAssetTree {
  if (!v || typeof v !== "object") return false;
  const t = v as Record<string, unknown>;
  const index = t.index as Record<string, unknown> | null;
  return (
    Array.isArray(t.collections) &&
    t.collections.every((c) => typeof c === "string") &&
    typeof t.collection === "string" &&
    !!index &&
    typeof index === "object" &&
    !!index.collections &&
    typeof index.collections === "object" &&
    Array.isArray(index.malformed)
  );
}

/** Personal scopes share one name across every user of a browser, so they are not kept. */
function storage(scope: string): Storage | null {
  if (scope.startsWith("user:")) return null;
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function dropAll(store: Storage): void {
  for (let i = store.length - 1; i >= 0; i--) {
    const k = store.key(i);
    if (k?.startsWith(KEY_PREFIX)) store.removeItem(k);
  }
}

export const localAssetTreeCache: AssetTreeCache = {
  read(scope) {
    try {
      const raw = storage(scope)?.getItem(KEY_PREFIX + scope);
      if (!raw) return null;
      const parsed: unknown = JSON.parse(raw);
      return isTree(parsed) ? parsed : null;
    } catch {
      return null;
    }
  },
  write(scope, collections, collection, indexJson) {
    const store = storage(scope);
    if (!store) return;
    const key = KEY_PREFIX + scope;
    const entry = `{"collections":${JSON.stringify(collections)},"collection":${JSON.stringify(collection)},"index":${indexJson}}`;
    try {
      if (entry.length > MAX_ENTRY) {
        store.removeItem(key);
        return;
      }
      store.setItem(key, entry);
    } catch {
      // Over quota: other scopes' entries make room, once.
      try {
        dropAll(store);
        store.setItem(key, entry);
      } catch {
        try {
          store.removeItem(key);
        } catch {
          // Storage unusable; the tab reads from the server as before.
        }
      }
    }
  },
  forget(scope) {
    try {
      storage(scope)?.removeItem(KEY_PREFIX + scope);
    } catch {
      // Nothing kept, or nothing reachable.
    }
  },
};
