import {createRoot} from 'react-dom/client';
import App from './app';
import React from "react";
import {initWebSocket} from "./utils/websocket/initWebSocket";
import {load_base64_model} from "./utils/scene/handlers/update_scene_from_message";
import {runtime} from "@/runtime/config";

// start websocket here
initWebSocket()

if (runtime.b64Gltf()) {
    load_base64_model()
} else {
    console.log("B64GLTF not attached.");
}
const container = document.getElementById('root');
// @ts-ignore
const root = createRoot(container); // create a root
root.render(<App/>);