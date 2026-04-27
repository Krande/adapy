// Centralised, typed access to the `window.*` globals that the SPA
// depends on. These are injected at build/serve time:
//
//   - /config.js (REST mode)        : COMMS_MODE, API_BASE, CONVERT_ENABLED
//   - embedded index.html           : WEBSOCKET_ID, WEBSOCKET_PORT, B64GLTF,
//                                     NODE_EDITOR_ONLY, DEACTIVATE_WS,
//                                     UNIQUE_VERSION_ID, TARGET_INSTANCE_ID
//   - host page (Jupyter)           : Jupyter
//
// Read everything through `runtime.*` so we can change the source of
// truth (env, query string, postMessage, etc.) in one place.

declare global {
    interface Window {
        COMMS_MODE?: "rest" | "ws" | string;
        API_BASE?: string;
        CONVERT_ENABLED?: boolean;
        WEBSOCKET_ID?: number | string;
        WEBSOCKET_PORT?: number | string;
        TARGET_INSTANCE_ID?: number | string;
        UNIQUE_VERSION_ID?: number;
        NODE_EDITOR_ONLY?: boolean;
        DEACTIVATE_WS?: boolean;
        B64GLTF?: string;
        Jupyter?: unknown;
    }
}

const w = (): Window => window;

export const runtime = {
    // Transport
    commsMode: (): string | undefined => w().COMMS_MODE,
    isRestMode: (): boolean => w().COMMS_MODE === "rest",
    apiBase: (): string => (w().API_BASE || "/api").replace(/\/+$/, ""),

    // REST conversion pipeline
    convertEnabled: (): boolean => Boolean(w().CONVERT_ENABLED),

    // WebSocket / desktop bundle
    websocketPort: (): number => Number(w().WEBSOCKET_PORT ?? 8765),
    websocketId: (): number | string | undefined => w().WEBSOCKET_ID,
    websocketDeactivated: (): boolean => w().DEACTIVATE_WS === true,
    targetInstanceId: (): number | string | undefined => w().TARGET_INSTANCE_ID,

    // Embedded payloads
    b64Gltf: (): string | undefined => w().B64GLTF,
    clearB64Gltf: (): void => {
        try {
            delete w().B64GLTF;
        } catch {
            w().B64GLTF = undefined;
        }
    },

    // Build / host metadata
    uniqueVersionId: (): number => Number(w().UNIQUE_VERSION_ID ?? 0),
    nodeEditorOnly: (): boolean => Boolean(w().NODE_EDITOR_ONLY),
    inJupyter: (): boolean => Boolean(w().Jupyter),
    jupyter: (): any => w().Jupyter,
};

export type Runtime = typeof runtime;
