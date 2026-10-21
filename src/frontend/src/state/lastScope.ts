// The scope a user last browsed, kept across browser restarts. The selected
// scope itself lives in sessionStorage (per tab), which a closed browser loses;
// this lets a fresh tab start where the user left off, so the scene recorded
// for that scope can be restored. Tied to the signed-in subject: another user
// on the same machine starts at their own default.

import {type ScopeOption, scopeUrlPart, useScopeStore} from "@/state/scopeStore";

const KEY = "ada.lastScope";

interface Stored {
    sub: string;
    scope: string;
}

function storage(): Storage | null {
    try {
        return globalThis.localStorage ?? null;
    } catch {
        return null;
    }
}

/** The scope `sub` last browsed, in URL form, or null. */
export function recallScope(sub: string | null | undefined): string | null {
    if (!sub) return null;
    try {
        const raw = storage()?.getItem(KEY);
        if (!raw) return null;
        const v = JSON.parse(raw) as Partial<Stored>;
        return v && v.sub === sub && typeof v.scope === "string" ? v.scope : null;
    } catch {
        return null;
    }
}

export function rememberScope(sub: string, scope: ScopeOption | null): void {
    if (!sub || !scope) return;
    try {
        storage()?.setItem(KEY, JSON.stringify({sub, scope: scopeUrlPart(scope)} satisfies Stored));
    } catch {
        /* storage full or blocked: the next session starts at the default */
    }
}

let following: (() => void) | null = null;

/** Remember every scope `sub` switches to from now on (and the current one). */
export function followScope(sub: string): void {
    following?.();
    rememberScope(sub, useScopeStore.getState().current);
    following = useScopeStore.subscribe((s, prev) => {
        if (s.current !== prev.current) rememberScope(sub, s.current);
    });
}
