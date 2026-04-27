// Transport lifecycle — owns the side-effects of toggling
// useOptionsStore.enableWebsocket. Kept out of the store itself so
// the store stays a pure state container with no comms coupling.

import {comms} from "@/utils/comms";
import {useOptionsStore} from "@/state/optionsStore";
import {useWebSocketStore} from "@/state/webSocketStore";

async function applyEnabled(enabled: boolean): Promise<void> {
    try {
        if (enabled) {
            const url = useWebSocketStore.getState().webSocketAddress;
            await comms.connect(url);
        } else {
            await comms.disconnect();
        }
    } catch (err) {
        console.error("transport: failed to apply enableWebsocket=" + enabled, err);
    }
}

let unsubscribe: (() => void) | null = null;

/** Subscribe to enableWebsocket changes and reflect them on the
 *  comms transport. Idempotent — calling twice is a no-op. */
export function bindTransportToOptions(): void {
    if (unsubscribe) return;
    unsubscribe = useOptionsStore.subscribe((state, prev) => {
        if (state.enableWebsocket !== prev.enableWebsocket) {
            void applyEnabled(state.enableWebsocket);
        }
    });
}

export function unbindTransportFromOptions(): void {
    unsubscribe?.();
    unsubscribe = null;
}
