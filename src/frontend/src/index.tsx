import {createRoot} from 'react-dom/client';
import App from './app';
import React from "react";
import {webSocketHandler} from "./utils/websocket_connector";
import {useWebSocketStore} from "./state/webSocketStore";
import {handleWebSocketMessage} from "./utils/handleWebSocketMessage";
import {loadGLTFfrombase64} from "./utils/scene/loadGLTFfrombase64";
import {useModelStore} from "./state/modelStore";
import {SceneOperations} from "./flatbuffers/wsock/scene-operations";

// start websocket here
const url = useWebSocketStore.getState().webSocketAddress;
webSocketHandler.connect(url, handleWebSocketMessage());
console.log("Checking if B64GLTF exists");
if ((window as any).B64GLTF) {
    console.log("B64GLTF exists, loading model");
    let blob_uri = loadGLTFfrombase64((window as any).B64GLTF);
    useModelStore.getState().setModelUrl(blob_uri, SceneOperations.REPLACE, null);
}
const container = document.getElementById('root');
// @ts-ignore
const root = createRoot(container); // create a root
root.render(<App/>);