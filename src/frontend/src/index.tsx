import { createRoot } from "react-dom/client";
import App from "./app";
import React from "react";
import { initWebSocket } from "./utils/websocket/initWebSocket";
import { load_base64_model } from "./utils/scene/handlers/update_scene_from_message";
import { runtime } from "@/runtime/config";
import ErrorBoundary from "./components/common/ErrorBoundary";
import { loadPlugins } from "@/plugins";

// Register built-in (build-time) plugins into the core registry before the UI
// mounts, so the slot hosts see a populated registry on first render. Plugins
// ship dormant (activation-gated), so this is a no-op for existing users.
loadPlugins();

// start websocket here
initWebSocket();

if (runtime.b64Gltf()) {
  load_base64_model();
} else {
  console.log("B64GLTF not attached.");
}
const container = document.getElementById("root");
// @ts-ignore
const root = createRoot(container); // create a root
// Last-resort boundary: a throw that escapes every inner panel boundary shows a
// reload card instead of a blank page.
root.render(
  <ErrorBoundary variant="fullscreen" label="Viewer">
    <App />
  </ErrorBoundary>,
);
