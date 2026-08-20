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
// stored preference; otherwise the shell.
//
// CUTOVER: the shell is the product now. `?shell=0` still reaches the classic UI for one
// transition period so anyone who hits a regression has a way to keep working and a way
// to show what differs. It goes when the classic code does.

interface ShellPrefs {
    enabled: boolean;
    setEnabled: (on: boolean) => void;
}

export const useShellPrefs = create<ShellPrefs>()(
    persist(
        (set) => ({
            // Default ON as of the M8 cutover.
            //
            // Note this reads the STORED value first, so reviewers who explicitly chose
            // ?shell=0 during the transition stay on the classic UI until they clear it.
            // That is deliberate: an explicit choice should not be overridden by a
            // default changing underneath someone.
            enabled: true,
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
