// Where the viewer keeps the user's layout: per user on the server in hosted
// (REST) mode, in this browser's localStorage otherwise (desktop / websocket).

import { runtime } from "@/runtime/config";
import { viewerApi } from "@/services/viewerApi";

import type { LayoutPersistence } from "./layout";
import { localLayoutPersistence } from "./localPersistence";
import { serverLayoutPersistence } from "./serverPersistence";

export function viewerLayoutPersistence(): LayoutPersistence {
  const local = localLayoutPersistence();
  if (!runtime.isRestMode()) return local;
  const server = serverLayoutPersistence(
    { get: (key) => viewerApi.getPreference(key), put: (key, value, opts) => viewerApi.setPreference(key, value, opts) },
    { fallback: local },
  );
  // A save still waiting for its debounce goes out as the page goes away.
  window.addEventListener("pagehide", () => void server.flush({ keepalive: true }));
  return server;
}
