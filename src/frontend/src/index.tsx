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
if ((window as any).DEACTIVATE_WS===true) {
    console.log("DEACTIVATE_WS is set to true, not connecting to websocket");
}else {
    webSocketHandler.connect(url, handleWebSocketMessage());
}


if ((window as any).B64GLTF) {
    console.log("B64GLTF exists, loading model");
    let blob_uri = loadGLTFfrombase64((window as any).B64GLTF);
    useModelStore.getState().setModelUrl(blob_uri, SceneOperations.REPLACE, null);
    // delete the B64GLTF from the window object
    delete (window as any).B64GLTF;
} else {
    console.log("B64GLTF not attached.");
}
const container = document.getElementById('root');
// @ts-ignore
const root = createRoot(container); // create a root
root.render(<App/>);