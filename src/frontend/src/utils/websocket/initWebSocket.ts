import {useWebSocketStore} from "@/state/webSocketStore";
import {useWebsocketStatusStore} from "@/state/websocketStatusStore";
import {runtime} from "@/runtime/config";
import {comms} from "../comms";
import {handleFlatbufferMessage} from "../fb_handling/handle_incoming_buffers";
import {requestServerInfo} from "./requestServerInfo";
import {requestConnectedClients} from "./requestConnectedClients";
import {request_list_of_files_from_server} from "../server_info/handlers/request_list_of_files_from_server";
import {bindTransportToOptions} from "@/services/transport";

function pickConnectUrl(): string {
    if (runtime.isRestMode()) {
        return runtime.apiBase();
    }
    return useWebSocketStore.getState().webSocketAddress;
}

export async function initWebSocket() {
    const statusStore = useWebsocketStatusStore.getState();

    // Register the dispatcher up-front so a later optionsStore toggle
    // does not have to re-register (would double-fire handlers).
    comms.onMessage(handleFlatbufferMessage);
    comms.onConnect(() => {
        requestServerInfo();
        requestConnectedClients();
        request_list_of_files_from_server();
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