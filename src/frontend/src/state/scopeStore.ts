import {create} from "zustand";
import {persist} from "zustand/middleware";

// One scope the user is browsing right now. Mirrors the backend's
// /api/me response.
export interface ScopeOption {
    kind: "shared" | "user" | "project" | "corpus";
    // 'me' for user-scope; null for shared; the corpus slug for corpus.
    id: string | null;
    name: string;
}

interface ScopeState {
    /** Selected scope. Persists across reloads (sessionStorage scoped
     * to tab) so a user picking a project sees it again on refresh. */
    current: ScopeOption | null;
    /** All scopes the current user can access. Filled from /api/me. */
    available: ScopeOption[];
    setCurrent: (s: ScopeOption) => void;
    setAvailable: (xs: ScopeOption[]) => void;
}

export const useScopeStore = create<ScopeState>()(
    persist(
        (set) => ({
            current: null,
            available: [],
            setCurrent: (s) => set({current: s}),
            setAvailable: (xs) =>
                set((prev) => {
                    // Reconcile: if the previously-selected scope is no
                    // longer available (project archived, etc.), fall
                    // back to the first available one. Same kind+id is
                    // strict equality.
                    if (prev.current) {
                        const stillThere = xs.find(
                            (x) => x.kind === prev.current!.kind && x.id === prev.current!.id,
                        );
                        if (stillThere) return {available: xs};
                    }
                    return {available: xs, current: xs[0] ?? null};
                }),
        }),
        {
            name: "ada-scope",
            // Use sessionStorage rather than localStorage — picking a
            // scope is a session decision, not a permanent preference,
            // and we don't want to leak project ids across logged-in
            // users on shared machines.
            storage: {
                getItem: (name) => {
                    const raw = sessionStorage.getItem(name);
                    return raw ? JSON.parse(raw) : null;
                },
                setItem: (name, value) =>
                    sessionStorage.setItem(name, JSON.stringify(value)),
                removeItem: (name) => sessionStorage.removeItem(name),
            },
            partialize: (s) =>
                ({current: s.current}) as unknown as ScopeState,
        },
    ),
);

/** URL form for the current scope, e.g. "user:me" or "project:abc-123". */
export function scopeUrlPart(s: ScopeOption | null): string {
    if (!s) return "shared";
    if (s.kind === "shared") return "shared";
    if (s.kind === "user") return "user:me"; // server resolves 'me' to the caller's sub
    if (s.kind === "corpus") return `corpus:${s.id}`; // admin-only, gated server-side
    return `project:${s.id}`;
}

/** Inverse of {@link scopeUrlPart}: parse a URL scope part (e.g. "project:abc")
 *  back into a ScopeOption. Used when a scope arrives via a URL param (a
 *  follower window adopting the driving tab's scope) before /api/me lands. */
export function scopeFromUrlPart(part: string): ScopeOption {
    if (!part || part === "shared") return {kind: "shared", id: null, name: "Shared"};
    const [kind, id] = part.split(":");
    if (kind === "user") return {kind: "user", id: "me", name: "My files"};
    if (kind === "corpus") return {kind: "corpus", id: id ?? null, name: id ?? "corpus"};
    if (kind === "project") return {kind: "project", id: id ?? null, name: id ?? "project"};
    return {kind: "shared", id: null, name: "Shared"};
}
