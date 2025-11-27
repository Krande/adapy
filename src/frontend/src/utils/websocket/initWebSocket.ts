import {useWebSocketStore} from "../../state/webSocketStore";
import {useWebsocketStatusStore} from "../../state/websocketStatusStore";
import {webSocketAsyncHandler} from "./websocket_connector_async";
import {handleWebSocketMessage} from "./handleWebSocketMessage";

export async function initWebSocket() {
    const url = useWebSocketStore.getState().webSocketAddress;
    const statusStore = useWebsocketStatusStore.getState();

    if ((window as any).DEACTIVATE_WS === true) {
        console.log("DEACTIVATE_WS is set to true, not connecting to websocket");
        return;
    }

    try {
        await webSocketAsyncHandler.connect(url);
        statusStore.setFrontendId(webSocketAsyncHandler.instance_id);

        // start listening for incoming messages
        for await (const event of webSocketAsyncHandler.messages()) {
            await handleWebSocketMessage(event);
        }
    } catch (err) {
        console.error('WebSocket connection failed:', err);
        statusStore.setConnected(false);
    }
}