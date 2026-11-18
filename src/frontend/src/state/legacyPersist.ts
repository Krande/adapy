// Storage for a zustand `persist` store whose setting was kept by hand before, as a bare string in
// localStorage ("off", "tree", ...): `persist` writes `{state, version}` JSON, and this reads the old
// bare value too, as the version-0 state `legacy` makes of it, so a user's choice outlives the
// switch. Storage that is blocked or missing (a private window, node) reads as nothing set.

import type {PersistStorage, StorageValue} from "zustand/middleware";

export function legacyAwareStorage<S>(legacy: (raw: string) => S | null): PersistStorage<S> {
    const local = (): Storage | null => {
        try {
            return typeof window === "undefined" ? null : window.localStorage;
        } catch {
            return null;
        }
    };
    return {
        getItem(name): StorageValue<S> | null {
            let raw: string | null = null;
            try {
                raw = local()?.getItem(name) ?? null;
            } catch {
                return null;
            }
            if (raw === null) return null;
            try {
                const parsed = JSON.parse(raw) as unknown;
                if (parsed && typeof parsed === "object" && "state" in parsed) return parsed as StorageValue<S>;
            } catch {
                // Not JSON: the value as it was kept before.
            }
            const state = legacy(raw);
            return state === null ? null : {state, version: 0};
        },
        setItem(name, value) {
            try {
                local()?.setItem(name, JSON.stringify(value));
            } catch {
                // Private window or blocked storage: the choice lasts this page.
            }
        },
        removeItem(name) {
            try {
                local()?.removeItem(name);
            } catch {
                // As above.
            }
        },
    };
}
