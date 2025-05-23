import {createRoot} from 'react-dom/client';
import App from './app';
import React from "react";
import {loadGLTFfrombase64} from "./utils/scene/loadGLTFfrombase64";
import {useModelState} from "./state/modelState";
import {SceneOperations} from "./flatbuffers/scene/scene-operations";
import {initWebSocket} from "./utils/websocket/initWebSocket";

// start websocket here
initWebSocket()

if ((window as any).B64GLTF) {
    console.log("B64GLTF exists, loading model");
    let blob_uri = loadGLTFfrombase64((window as any).B64GLTF);
    useModelState.getState().setModelUrl(blob_uri, SceneOperations.REPLACE);
    // delete the B64GLTF from the window object
    delete (window as any).B64GLTF;
} else {
    console.log("B64GLTF not attached.");
}
const container = document.getElementById('root');
// @ts-ignore
const root = createRoot(container); // create a root
root.render(<App/>);