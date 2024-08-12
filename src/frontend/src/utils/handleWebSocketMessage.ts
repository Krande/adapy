// handleWebSocketMessage.ts
import {webSocketHandler} from "./websocket_connector";

export enum SceneAction {
    NEW = "new",
    REPLACE = "replace",
    ADD = "add",
    REMOVE = "remove"
}

interface Message {
    data: string;
    look_at?: [number, number, number];
    camera_position?: [number, number, number];
    model_translation?: [number, number, number];
    scene_action?: SceneAction;
    scene_action_arg?: string;
}


const handleStringMessage = (setModelUpdate: (url: string | null, scene_action: SceneAction | null, scene_action_arg: string) => void, data: string) => {
    // Parse the JSON message
    const message: Message = JSON.parse(data);

    if (!message.data) {
        return;
    }

    console.log('Base64 encoded data received');

    const scene_action = message.scene_action ? message.scene_action : null;
    const scene_action_arg = message.scene_action_arg ? message.scene_action_arg : '';
    console.log('SceneAction: ', scene_action);

    // Decode Base64 string to bytes
    const decodedData = atob(message.data);
    if (message.look_at && message.camera_position) {
        console.log('Look at position: ', message.look_at);
        console.log('Camera position: ', message.camera_position);
    }

    // Convert decoded bytes to Uint8Array
    const byteNumbers = new Array(decodedData.length);
    for (let i = 0; i < decodedData.length; i++) {
        byteNumbers[i] = decodedData.charCodeAt(i);
    }
    const byteArray = new Uint8Array(byteNumbers);

    // Create a Blob from the Uint8Array
    const blob = new Blob([byteArray], {type: 'model/gltf-binary'});
    const url = URL.createObjectURL(blob);

    setModelUpdate(url, scene_action, scene_action_arg); // Set the URL for the model
};

const handleBlobMessage = (setModelUrl: (url: string | null, scene_action: SceneAction | null, scene_action_arg: string) => void, data: Blob) => {
    console.log('Blob received');
    const blob = new Blob([data], {type: 'model/gltf-binary'});
    const url = URL.createObjectURL(blob);
    setModelUrl(url, null, ""); // Set the URL for the model
};

export const handleWebSocketMessage = (setModelUpdate: (url: string | null, scene_action: SceneAction | null, scene_action_arg: string | null) => void) => (event: MessageEvent) => {
    if (typeof event.data === 'string') {
        // if string == 'ping' then send 'pong'
        if (event.data === 'ping') {
            console.log('Received ping from server');
            webSocketHandler.sendMessage('pong');
            return;
        }
        handleStringMessage(setModelUpdate, event.data);
    } else if (event.data instanceof Blob) {
        handleBlobMessage(setModelUpdate, event.data);
    } else {
        console.log('Message from server ', event.data);
    }
};