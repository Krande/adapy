// Core's own modal dialogs that core commands open (Help -> About, File -> Export…): which one is
// open, as a tiny external store. `CoreDialogsHost`, which a composed layout mounts, draws it and
// loads its code on first use. Dependency-free.

import {useSyncExternalStore} from "react";

export type ExportScope = "selection" | "model";

export type CoreDialogRequest = {readonly kind: "about"} | {readonly kind: "export"; readonly scope: ExportScope};

let current: CoreDialogRequest | null = null;
const listeners = new Set<() => void>();

function set(next: CoreDialogRequest | null): void {
    current = next;
    for (const l of listeners) l();
}

/** Open `request`'s dialog, in place of any open one. */
export function openCoreDialog(request: CoreDialogRequest): void {
    set(request);
}

export function closeCoreDialog(): void {
    if (current) set(null);
}

export function currentCoreDialog(): CoreDialogRequest | null {
    return current;
}

/** The open dialog, re-rendering when it changes. */
export function useCoreDialog(): CoreDialogRequest | null {
    return useSyncExternalStore(
        (cb) => {
            listeners.add(cb);
            return () => listeners.delete(cb);
        },
        () => current,
        () => null,
    );
}
