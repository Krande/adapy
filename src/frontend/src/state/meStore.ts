import {create} from "zustand";

// Mirrors the subset of /api/me the UI cares about beyond the scope
// list (which lives in scopeStore). Populated by AuthGate after sign-in
// and read by the menu-bar admin button + AdminPanel. Kept tiny on
// purpose — no persistence; we always re-fetch /api/me on boot.
interface MeState {
    sub: string | null;
    email: string | null;
    displayName: string | null;
    isAdmin: boolean;
    set: (m: {sub: string; email: string; displayName: string; isAdmin: boolean}) => void;
    clear: () => void;
}

export const useMeStore = create<MeState>((set) => ({
    sub: null,
    email: null,
    displayName: null,
    isAdmin: false,
    set: (m) =>
        set({
            sub: m.sub,
            email: m.email,
            displayName: m.displayName,
            isAdmin: m.isAdmin,
        }),
    clear: () => set({sub: null, email: null, displayName: null, isAdmin: false}),
}));
