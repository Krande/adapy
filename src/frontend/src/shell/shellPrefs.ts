import {create} from "zustand";
import {persist} from "zustand/middleware";

// Whether to boot the new shell or the classic UI.
//
// Its own store rather than a field on optionsStore for two reasons: optionsStore has no
// persist middleware (the choice would not survive a reload, making review tedious), and
// this is shell plumbing that disappears at cutover — keeping it separate means deleting
// one file rather than unpicking a field from a store the rewrite is not supposed to
// touch.
//
// Precedence: an explicit ?shell=0/1 in the URL wins and is remembered; otherwise the
// stored preference; otherwise the classic UI.

interface ShellPrefs {
    enabled: boolean;
    setEnabled: (on: boolean) => void;
}

export const useShellPrefs = create<ShellPrefs>()(
    persist(
        (set) => ({
            // Default OFF for the whole of the transition. The classic UI stays the
            // product until M8 cutover; the new shell is opt-in.
            enabled: false,
            setEnabled: (enabled) => set({enabled}),
        }),
        {name: "ada:shell:v1"},
    ),
);

/**
 * Read `?shell=` once at boot and fold it into the stored preference.
 *
 * Returns whether the new shell should mount. Called from app.tsx before render, so the
 * decision is made once and cannot flip mid-session.
 */
export function resolveShellEnabled(search: string = window.location.search): boolean {
    const raw = new URLSearchParams(search).get("shell");
    if (raw != null) {
        const on = raw !== "0" && raw !== "false";
        // Persist the explicit choice so a reload without the param keeps it — otherwise
        // every reload during review needs the query string retyped.
        useShellPrefs.getState().setEnabled(on);
        return on;
    }
    return useShellPrefs.getState().enabled;
}
