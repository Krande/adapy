// Centralised, typed access to the `window.*` globals that the SPA
// depends on. These are injected at build/serve time:

// Per-job knob declared at the @converter decorator site on the
// worker (one schema entry per name). Wire-shape lives in the
// matrix entry's ``options[<target>]`` array.
export interface ConversionOption {
    name: string;
    type: "bool" | "string" | "int" | "enum";
    default?: boolean | string | number | null;
    description?: string;
    enum?: readonly string[];
}

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
        // Subset of stream-readable extensions that the legacy /convert
        // pipeline cannot handle. Computed server-side as
        // EXTRA_SOURCE_EXTS minus the legacy-convertable set (.sif is
        // both, and falls out of this subset so it keeps its eager
        // GLB-preview path). Drives the picker between /convert and
        // /fea/manifest on upload.
        STREAMING_ONLY_EXTS?: readonly string[];
        // Merged conversion matrix advertised by every live worker.
        // Each entry says "source extension `from` can be converted
        // to any of the `to` targets". The /convert page reads this
        // to populate the target dropdown per uploaded file. Empty
        // when the queue is disabled (dev / desktop mode) or no
        // worker has registered yet.
        //
        // `options` is the per-(from, target) per-job knob schema —
        // one list of {name, type, default, description, ...} per
        // target. Empty list when the pair has no per-job knobs;
        // the field is always present so callers can render
        // unconditionally without a key check.
        CONVERSION_MATRIX?: readonly {
            from: string;
            to: readonly string[];
            options?: Readonly<Record<string, readonly ConversionOption[]>>;
        }[];
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
    streamingOnlyExts: (): readonly string[] => w().STREAMING_ONLY_EXTS ?? [],
    conversionMatrix: () => w().CONVERSION_MATRIX ?? [],
    /** Targets advertised for a given source extension. Lower-cases
     * and dot-normalises the input so callers don't have to. Empty
     * array when the source extension isn't in the matrix (either
     * because no worker advertises it or the queue is disabled). */
    conversionTargetsFor: (ext: string): readonly string[] => {
        const normFrom = (ext.startsWith(".") ? ext : `.${ext}`).toLowerCase();
        const matrix = w().CONVERSION_MATRIX ?? [];
        for (const entry of matrix) {
            if ((entry.from || "").toLowerCase() === normFrom) {
                return entry.to || [];
            }
        }
        return [];
    },
    /** Option schema for a given (source extension, target format)
     * pair. Empty array when the pair has no per-job knobs. Frontend
     * uses this to render one widget per option on the /convert
     * page row (checkbox / input / select depending on `type`). */
    conversionOptionsFor: (ext: string, target: string): readonly ConversionOption[] => {
        const normFrom = (ext.startsWith(".") ? ext : `.${ext}`).toLowerCase();
        const normTo = target.replace(/^\./, "").toLowerCase();
        const matrix = w().CONVERSION_MATRIX ?? [];
        for (const entry of matrix) {
            if ((entry.from || "").toLowerCase() !== normFrom) continue;
            const opts = entry.options;
            if (opts && opts[normTo]) return opts[normTo];
            return [];
        }
        return [];
    },

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
