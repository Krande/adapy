import {useWebSocketStore} from "@/state/webSocketStore";
import {useWebsocketStatusStore} from "@/state/websocketStatusStore";
import {runtime} from "@/runtime/config";
import {comms} from "../comms";
import {handleFlatbufferMessage} from "../fb_handling/handle_incoming_buffers";
import {requestServerInfo} from "./requestServerInfo";
import {requestConnectedClients} from "./requestConnectedClients";
import {request_list_of_files_from_server} from "../server_info/handlers/request_list_of_files_from_server";
import {bindTransportToOptions} from "@/services/transport";
import {isAuthEnabled, isSignedIn} from "@/services/auth/oidc";

function pickConnectUrl(): string {
    if (runtime.isRestMode()) {
        return runtime.apiBase();
    }
    return useWebSocketStore.getState().webSocketAddress;
}

/** Fan-out of the legacy server-state RPCs that ship on connect:
 * server info, connected-client list, file listing. Pulled out so
 * AuthGate can re-fire them once auth completes — the on-connect
 * handler below skips them when no bearer token is in hand yet,
 * since REST is connectionless and onConnect fires synchronously
 * during ``initWebSocket`` (well before AuthGate's bootstrap
 * resolves). */
export function loadInitialServerState(): void {
    requestServerInfo();
    // LIST_WEB_CLIENTS is a websocket-era concept (per-connection
    // client tracking). The REST backend rejects the command with a
    // "not supported in REST mode" error message — annoying log
    // noise for no value. Skip it under REST.
    if (!runtime.isRestMode()) {
        requestConnectedClients();
    }
    request_list_of_files_from_server();
}

export async function initWebSocket() {
    const statusStore = useWebsocketStatusStore.getState();

    // Register the dispatcher up-front so a later optionsStore toggle
    // does not have to re-register (would double-fire handlers).
    comms.onMessage(handleFlatbufferMessage);
    comms.onConnect(() => {
        // Auth-enabled, no token yet → /api/rpc would 401 three times
        // and the user gets a console-error spew on every page load.
        // Defer; AuthGate fires loadInitialServerState() once
        // bootstrap puts a token in place.
        if (isAuthEnabled() && !isSignedIn()) return;
        loadInitialServerState();
    });

    // Reflect enableWebsocket toggles onto the transport. Subscription
    // lives outside the store so the store stays comms-free.
    bindTransportToOptions();

    if (runtime.websocketDeactivated()) {
        console.log("DEACTIVATE_WS is set to true, not connecting");
        return;
    }

    try {
        await comms.connect(pickConnectUrl());
        statusStore.setFrontendId(comms.getInstanceId());
    } catch (err) {
        console.error('Comms connection failed:', err);
        statusStore.setConnected(false);
    }
}