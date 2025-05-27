import {createRoot} from 'react-dom/client';
import App from './app';
import React from "react";
import {initWebSocket} from "./utils/websocket/initWebSocket";
import {load_base64_model} from "./utils/scene/comms/update_scene_from_message";

// start websocket here
initWebSocket()

if ((window as any).B64GLTF) {
    load_base64_model()
} else {
    console.log("B64GLTF not attached.");
}
const container = document.getElementById('root');
// @ts-ignore
const root = createRoot(container); // create a root
root.render(<App/>);