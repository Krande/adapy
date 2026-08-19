import { createRoot } from "react-dom/client";
import App from "./app";
import React from "react";
import { initWebSocket } from "./utils/websocket/initWebSocket";
import { load_base64_model } from "./utils/scene/handlers/update_scene_from_message";
import { runtime } from "@/runtime/config";
import ErrorBoundary from "./components/common/ErrorBoundary";
import { loadPlugins } from "@/plugins";
import { loadDevBuildFixtureIfRequested, loadDevFeaFixtureIfRequested, loadDevFixtureIfRequested } from "./dev/devFixture";

// Register built-in (build-time) plugins into the core registry before the UI
// mounts, so the slot hosts see a populated registry on first render. Plugins
// ship dormant (activation-gated), so this is a no-op for existing users.
loadPlugins();

// start websocket here
initWebSocket();

if (runtime.b64Gltf()) {
  load_base64_model();
} else if (!loadDevFixtureIfRequested()) {
  // Dev-only fallback: ?demo=1 loads the committed fixture so `npm run dev` has a model
  // without a backend. Compiled out of production builds.
  console.log("B64GLTF not attached.");
}
// ?fea=1 (dev:rest only) loads the baked FEA deck through the real streaming loader, so
// Results mode can be reviewed against actual mode shapes. Async and independent of the
// geometry fixture above — the two can be combined.
void loadDevFeaFixtureIfRequested();
// ?build=1 opens the procedural fixture in the cellbuilder (works without REST).
void loadDevBuildFixtureIfRequested();
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
