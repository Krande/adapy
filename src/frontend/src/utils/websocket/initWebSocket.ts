import {useWebSocketStore} from "../../state/webSocketStore";
import {useWebsocketStatusStore} from "../../state/websocketStatusStore";
import {comms} from "../comms";
import {handleFlatbufferMessage} from "../fb_handling/handle_incoming_buffers";
import {requestServerInfo} from "./requestServerInfo";
import {requestConnectedClients} from "./requestConnectedClients";
import {request_list_of_files_from_server} from "../server_info/comms/request_list_of_files_from_server";

function pickConnectUrl(): string {
    if ((window as any).COMMS_MODE === "rest") {
        return (window as any).API_BASE || "/api";
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

    if ((window as any).DEACTIVATE_WS === true) {
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