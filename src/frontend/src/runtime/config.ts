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
        // OIDC bootstrap (REST mode). When AUTH_ENABLED is false the
        // others are unused; the SPA renders without an auth gate.
        AUTH_ENABLED?: boolean;
        AUTH_ISSUER?: string;
        AUTH_CLIENT_ID?: string;
        AUTH_AUDIENCE?: string;
        // Image tags reported by the viewer pod (env-baked at image
        // build) and the worker pod (published to NATS KV on
        // startup, surfaced here via /config.js). Either may be null
        // for dev builds — Options panel hides the row when both are
        // empty so the offline / desktop bundle stays clean.
        VIEWER_IMAGE_TAG?: string | null;
        WORKER_IMAGE_TAG?: string | null;
        // Union of source-file extensions every currently-registered
        // worker advertises on top of adapy's base set. The upload
        // picker merges this with its built-in list so capability
        // workers' formats show up automatically without anything in
        // adapy needing to know what those formats are.
        EXTRA_SOURCE_EXTS?: readonly string[];
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

    // Image identity (REST mode only). Either may be empty in dev or
    // when the worker hasn't reported in yet.
    viewerImageTag: (): string => (w().VIEWER_IMAGE_TAG || "").trim(),
    workerImageTag: (): string => (w().WORKER_IMAGE_TAG || "").trim(),
    extraSourceExts: (): readonly string[] => w().EXTRA_SOURCE_EXTS ?? [],

    // OIDC bootstrap. authEnabled() drives whether the SPA puts up an
    // auth gate at all; in dev / desktop it's false and the rest of
    // these go unread.
    authEnabled: (): boolean => Boolean(w().AUTH_ENABLED),
    authIssuer: (): string => (w().AUTH_ISSUER || "").replace(/\/+$/, ""),
    authClientId: (): string => w().AUTH_CLIENT_ID || "",
    authAudience: (): string => w().AUTH_AUDIENCE || w().AUTH_CLIENT_ID || "",

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
