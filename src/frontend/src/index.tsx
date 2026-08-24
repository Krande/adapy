// The stylesheet belongs to the ENTRY, not to core's UI.
//
// It used to be imported by app.tsx. Since app.tsx became the built-in UI SHELL it is
// lazy-loaded, so an image whose default shell is a plugin-contributed UI never
// evaluated it — and with it went Tailwind's preflight, every utility class and the
// html/body/#root sizing rules. The alternative UI rendered unstyled, and `?ui=core`
// was the only way to see a styled viewer.
import "./app.css";
import { createRoot } from "react-dom/client";
import React from "react";
import { initWebSocket } from "./utils/websocket/initWebSocket";
import { load_base64_model } from "./utils/scene/handlers/update_scene_from_message";
import { runtime } from "@/runtime/config";
import ErrorBoundary from "./components/common/ErrorBoundary";
import { UiShellHost, loadPlugins } from "@/plugins";

// Register built-in (build-time) plugins into the core registry before the UI
// mounts, so the slot hosts see a populated registry on first render. Plugins
// ship dormant (activation-gated), so this is a no-op for existing users.
// This also registers the built-in UI shell that `UiShellHost` mounts below.
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
// The root is the UI-SHELL HOST, not core's `App`: which UI mounts is resolved
// from the shell registry (`?ui=` > localStorage > build-time default > core).
// A stock build has exactly one shell registered and mounts the same `App` as
// before; an image built with an alternative UI overlaid boots into that one.
root.render(
  <ErrorBoundary variant="fullscreen" label="Viewer">
    <UiShellHost />
  </ErrorBoundary>,
);
