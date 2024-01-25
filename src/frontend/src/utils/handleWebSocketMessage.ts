// handleWebSocketMessage.ts
interface Message {
    data: string;
    look_at?: [number, number, number];
    camera_position?: [number, number, number];
}

const handleStringMessage = (setModelUrl: (url: string | null) => void, data: string) => {
    // Parse the JSON message
    const message: Message = JSON.parse(data);

    if (!message.data) {
        return;
    }

    console.log('Base64 encoded data received');

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

    setModelUrl(url); // Set the URL for the model
};

const handleBlobMessage = (setModelUrl: (url: string | null) => void, data: Blob) => {
    console.log('Blob received');
    const blob = new Blob([data], {type: 'model/gltf-binary'});
    const url = URL.createObjectURL(blob);
    setModelUrl(url); // Set the URL for the model
};

export const handleWebSocketMessage = (setModelUrl: (url: string | null) => void) => (event: MessageEvent) => {
    if (typeof event.data === 'string') {
        handleStringMessage(setModelUrl, event.data);
    } else if (event.data instanceof Blob) {
        handleBlobMessage(setModelUrl, event.data);
    } else {
        console.log('Message from server ', event.data);
    }
};