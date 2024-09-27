// handleWebSocketMessage.ts
import {handleFlatbufferMessage} from "./handle_fb";


export const handleWebSocketMessage = () => (event: MessageEvent) => {
    if (event.data instanceof Blob) {
        // Convert Blob to ArrayBuffer and then handle it as FlatBuffer
        event.data.arrayBuffer().then((buffer) => {
            handleFlatbufferMessage(buffer);
        }).catch(err => {
            console.error('Error handling FlatBuffer message:', err);
        });
    } else {
        console.log('Message from server ', event.data);
    }
};