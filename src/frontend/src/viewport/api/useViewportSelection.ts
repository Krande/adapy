// The selection of the viewport on screen, for React: a panel that shows or counts what is selected
// reads it here and re-renders when it changes, whichever viewport holds it. No three.js.
import {useMemo, useSyncExternalStore} from "react";

import {activeViewport, subscribeSelection, type ObjectRef} from "./viewportApi";

const token = (): unknown => activeViewport()?.selectionToken() ?? null;

/** A value that changes with the selection: a dependency for what is derived from it. */
export function useSelectionToken(): unknown {
    return useSyncExternalStore(subscribeSelection, token, token);
}

/** How many objects are selected. */
export function useSelectionCount(): number {
    useSelectionToken();
    return activeViewport()?.selectionCount() ?? 0;
}

/** The selected objects. */
export function useSelectionRefs(): ObjectRef[] {
    const changed = useSelectionToken();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return useMemo(() => activeViewport()?.selection() ?? [], [changed]);
}
