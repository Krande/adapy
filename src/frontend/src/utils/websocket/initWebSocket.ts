import {useWebSocketStore} from "../../state/webSocketStore";
import {useWebsocketStatusStore} from "../../state/websocketStatusStore";
import {comms} from "../comms";
import {handleFlatbufferMessage} from "../fb_handling/handle_incoming_buffers";
import {requestServerInfo} from "./requestServerInfo";
import {requestConnectedClients} from "./requestConnectedClients";

export async function initWebSocket() {
    const url = useWebSocketStore.getState().webSocketAddress;
    const statusStore = useWebsocketStatusStore.getState();

    // Register the dispatcher up-front so a later optionsStore toggle
    // does not have to re-register (would double-fire handlers).
    comms.onMessage(handleFlatbufferMessage);
    comms.onConnect(() => {
        requestServerInfo();
        requestConnectedClients();
    });

    if ((window as any).DEACTIVATE_WS === true) {
        console.log("DEACTIVATE_WS is set to true, not connecting to websocket");
        return;
    }

    try {
        await comms.connect(url);
        statusStore.setFrontendId(comms.getInstanceId());
    } catch (err) {
        console.error('WebSocket connection failed:', err);
        statusStore.setConnected(false);
    }
}