import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import * as flatbuffers from "flatbuffers";
import {Message} from "./src/flatbuffers/wsock/message";
import {Server} from "./src/flatbuffers/server/server";
import {ServerReply} from "./src/flatbuffers/server/server-reply";
import {FileObject} from "./src/flatbuffers/base/file-object";
import {FileType} from "./src/flatbuffers/base/file-type";
import {CommandType} from "./src/flatbuffers/commands/command-type";
import {TargetType} from "./src/flatbuffers/commands/target-type";

// Serves /config.js — and a small offline slice of the REST API — inside the vite dev
// server, so the hosted-mode UI can be reviewed without deploying the stack.
//
// index.html always requests /config.js and removes the tag on 404 (that 404 is the
// normal dev path — the SPA then falls back to WS/desktop mode). This plugin answers
// that request ONLY when ADA_DEV_REST is set, so `npm run dev` keeps its default WS
// behaviour and `npm run dev:rest` gets REST mode. A static public/config.js could not
// do this: public/ is served at the root unconditionally, which would pin dev to REST.
//
// The API slice exists for one reason: Results mode is unreviewable against an empty
// scene, and the FEA viewer only loads through the REST blob endpoints. Rather than
// relax the REST gate in load_fea_streaming.ts (a fenced business-logic file), this
// serves the baked fixture at exactly the URLs the real fetcher builds — so the review
// exercises the REAL streaming path, range requests included, not a shortcut around it.
//
// Set ADA_DEV_API_BASE to point at a genuine backend instead
// (`pixi run -e viewer-api viewer-api`); the fixture routes then simply go unused.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "public/dev/fea");

/** Source key the fixture is baked under; mirrors makeViewerApiFetcher's prefix. */
const FIXTURE_SRC = "dev-cantilever";

function sendJson(res, body, status = 200) {
    const text = JSON.stringify(body);
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", "no-store");
    res.end(text);
}

/**
 * Serve a fixture file, honouring Range.
 *
 * Range support is not optional here: the FEA loader pulls ONE STEP out of a multi-step
 * blob with a range request and treats a 200 as "the server ignored my range, fall back
 * to the whole blob". Replying 200 to everything would silently exercise the fallback
 * path and never test the one that production uses.
 */
function sendFile(req, res, filePath) {
    let stat;
    try {
        stat = fs.statSync(filePath);
    } catch {
        res.statusCode = 404;
        res.end("fixture not found");
        return;
    }

    const url = new URL(req.url, "http://localhost");
    const qStart = url.searchParams.get("range_start");
    const qEnd = url.searchParams.get("range_end");
    const header = /^bytes=(\d+)-(\d+)?$/.exec(req.headers.range ?? "");

    // The client sends the range BOTH as query params and as a Range header (some
    // ingresses strip the header); accept either.
    let start = null;
    let end = null;
    if (qStart != null && qEnd != null) {
        start = Number(qStart);
        end = Number(qEnd);
    } else if (header) {
        start = Number(header[1]);
        end = header[2] != null ? Number(header[2]) : stat.size - 1;
    }

    res.setHeader("Accept-Ranges", "bytes");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Type", "application/octet-stream");

    if (start != null && Number.isFinite(start) && Number.isFinite(end)) {
        const last = Math.min(end, stat.size - 1);
        res.statusCode = 206;
        res.setHeader("Content-Range", `bytes ${start}-${last}/${stat.size}`);
        res.setHeader("Content-Length", String(last - start + 1));
        fs.createReadStream(filePath, {start, end: last}).pipe(res);
        return;
    }

    res.statusCode = 200;
    res.setHeader("Content-Length", String(stat.size));
    fs.createReadStream(filePath).pipe(res);
}

/**
 * The files the fixture pretends the scope holds.
 *
 * A deliberately mixed set: two source formats, a converted derivative and the baked FEA
 * deck. StorageBrowser groups by folder and badges by type, so a list of identical files
 * would show none of that.
 */
const FIXTURE_FILES: {name: string; type: FileType; modified: string}[] = [
    {name: "cantilever.step", type: FileType.STEP, modified: "2026-08-14T09:12:00Z"},
    {name: "topside.ifc", type: FileType.IFC, modified: "2026-08-15T14:03:00Z"},
    {name: "dev-cantilever.rmed", type: FileType.IFC, modified: "2026-08-18T08:41:00Z"},
    {name: "_derived/cantilever.step.glb", type: FileType.GLB, modified: "2026-08-14T09:14:00Z"},
];

/**
 * Build the flatbuffer reply for LIST_FILE_OBJECTS.
 *
 * StorageBrowser's file list does NOT come from a JSON endpoint — it goes through the
 * flatbuffer RPC at POST /rpc, the same envelope the websocket transport uses. Serving
 * JSON here would leave the panel permanently empty, so the fixture speaks the real
 * protocol: this is the one place the dev server has to build a Message rather than
 * hand back a file.
 */
function buildFileListReply(instanceId: number): Uint8Array {
    const b = new flatbuffers.Builder(1024);

    const fileOffsets = FIXTURE_FILES.map((f) => {
        const name = b.createString(f.name);
        const filepath = b.createString(`/dev-fixture/${f.name}`);
        const lastModified = b.createString(f.modified);
        FileObject.startFileObject(b);
        FileObject.addName(b, name);
        FileObject.addFileType(b, f.type);
        FileObject.addFilepath(b, filepath);
        FileObject.addLastModified(b, lastModified);
        return FileObject.endFileObject(b);
    });

    const vec = Server.createAllFileObjectsVector(b, fileOffsets);
    Server.startServer(b);
    Server.addAllFileObjects(b, vec);
    const server = Server.endServer(b);

    // The envelope shape matters and is not obvious: the client dispatches on
    // commandType === SERVER_REPLY and then on serverReply().replyTo(). A message typed
    // LIST_FILE_OBJECTS directly is silently ignored — it matches no case, so the list
    // stays empty with no error anywhere.
    ServerReply.startServerReply(b);
    ServerReply.addReplyTo(b, CommandType.LIST_FILE_OBJECTS);
    const reply = ServerReply.endServerReply(b);

    Message.startMessage(b);
    Message.addInstanceId(b, instanceId);
    Message.addCommandType(b, CommandType.SERVER_REPLY);
    Message.addTargetGroup(b, TargetType.WEB);
    Message.addClientType(b, TargetType.SERVER);
    Message.addServer(b, server);
    Message.addServerReply(b, reply);
    b.finish(Message.endMessage(b));

    return b.asUint8Array();
}

/** Read a whole request body. */

// Code-defined type catalogues, as the backend's built-in half returns them.
//
// Everything here is tagged origin "code": the real API tags DB-backed entries
// "catalog", and the UI shows that origin in every picker label ("Door (code)"). A
// fixture that claimed "catalog" would be inventing rows in a database that does not
// exist here, and the label would lie about where the type came from.

const OPENING_TYPES = [
    {slug: "door-single", name: "Door, single leaf", origin: "code", subtype: "door", size: [0.1, 0.9, 2.1]},
    {slug: "door-double", name: "Door, double leaf", origin: "code", subtype: "door", size: [0.1, 1.8, 2.1]},
    {slug: "window", name: "Window", origin: "code", subtype: "window", size: [0.1, 1.2, 1.0]},
    {slug: "hatch", name: "Hatch", origin: "code", subtype: "opening", size: [0.1, 0.8, 0.8]},
    {slug: "penetration", name: "Pipe penetration", origin: "code", subtype: "opening", size: [0.1, 0.3, 0.3]},
];

const EQUIPMENT_TYPES = [
    {
        slug: "pump-centrifugal",
        name: "Centrifugal pump",
        origin: "code",
        ports: [
            {name: "suction", direction: "in", category: "piping", position: [-0.6, 0, 0.3], direction_vector: [-1, 0, 0]},
            {name: "discharge", direction: "out", category: "piping", position: [0, 0, 0.9], direction_vector: [0, 0, 1]},
        ],
    },
    {
        slug: "vessel-vertical",
        name: "Vertical vessel",
        origin: "code",
        ports: [
            {name: "inlet", direction: "in", category: "piping", position: [0, 0, 2.4], direction_vector: [0, 0, 1]},
            {name: "outlet", direction: "out", category: "piping", position: [0, 0, -0.2], direction_vector: [0, 0, -1]},
        ],
    },
    {
        slug: "heat-exchanger",
        name: "Shell & tube exchanger",
        origin: "code",
        ports: [
            {name: "shell-in", direction: "in", category: "piping", position: [-1.5, 0, 0.4], direction_vector: [-1, 0, 0]},
            {name: "shell-out", direction: "out", category: "piping", position: [1.5, 0, 0.4], direction_vector: [1, 0, 0]},
        ],
    },
    {
        slug: "switchboard",
        name: "Switchboard",
        origin: "code",
        ports: [{name: "feed", direction: "in", category: "electrical", position: [0, -0.4, 1.6], direction_vector: [0, -1, 0]}],
    },
    {slug: "generic-skid", name: "Generic skid", origin: "code", ports: []},
];

const CELL_TYPES = [
    {slug: "room", name: "Room", origin: "code", size: [6, 4, 3]},
    {slug: "corridor", name: "Corridor", origin: "code", size: [12, 2, 3]},
    {slug: "equipment-room", name: "Equipment room", origin: "code", size: [8, 6, 4]},
    {slug: "deck-area", name: "Open deck area", origin: "code", size: [12, 12, 6]},
];

const SYSTEM_TYPES = [
    {slug: "process-piping", name: "Process piping", origin: "code", type: "piping", medium: "hydrocarbon"},
    {slug: "utility-water", name: "Utility water", origin: "code", type: "piping", medium: "water"},
    {slug: "hvac-supply", name: "HVAC supply", origin: "code", type: "duct", medium: "air"},
    {slug: "power-lv", name: "LV power", origin: "code", type: "electrical", voltage: 400},
    {slug: "instrument-cable", name: "Instrument cable", origin: "code", type: "cable"},
];

const DESIGN_RULESETS = [
    {slug: "default", name: "Default", description: "Built-in spacing and clearance rules.", origin: "code"},
    {slug: "offshore-topside", name: "Offshore topside", description: "Escape-route widths and blast clearances.", origin: "code"},
];

const ENGINE = {
    id: "adapy-default",
    name: "adapy-default",
    slug: "adapy-default",
    origin: "code",
    kind: "server",
    version: "dev",
};

function readBody(req: {on: (ev: string, cb: (c?: Buffer) => void) => void}): Promise<Buffer> {
    return new Promise((resolve) => {
        const chunks: Buffer[] = [];
        req.on("data", (c) => c && chunks.push(c));
        req.on("end", () => resolve(Buffer.concat(chunks)));
    });
}

export function adapyDevRestConfig() {
    const enabled = Boolean(process.env.ADA_DEV_REST);

    return {
        name: "adapy-dev-rest-config",
        apply: "serve",
        configureServer(server) {
            if (!enabled) return;

            const apiBase = process.env.ADA_DEV_API_BASE || "/api";
            const hasFixture = fs.existsSync(path.join(FIXTURE_DIR, "fea.manifest.json"));
            server.config.logger.info(
                `\n  adapy: REST dev mode ON — API_BASE=${apiBase}` +
                    (hasFixture
                        ? `\n  adapy: FEA fixture available as source "${FIXTURE_SRC}" (run scripts/make-fea-fixture.py to rebuild)\n`
                        : `\n  adapy: no FEA fixture — run: .pixi/envs/fem/python.exe src/frontend/scripts/make-fea-fixture.py\n`),
            );

            server.middlewares.use("/config.js", (_req, res) => {
                // Mirrors the shape ada/comms/rest/app.py injects in a real deployment.
                // Kept minimal on purpose: anything the SPA treats as optional stays
                // unset so dev exercises the same "absent" branches a fresh deployment
                // would.
                const body = [
                    `window.COMMS_MODE = "rest";`,
                    `window.API_BASE = ${JSON.stringify(apiBase)};`,
                    `window.AUTH_ENABLED = false;`,
                    `window.CONVERT_ENABLED = false;`,
                    `window.CONVERSION_MATRIX = [];`,
                    `window.ADAPY_VERSION = "dev";`,
                ].join("\n");
                res.setHeader("Content-Type", "application/javascript");
                res.setHeader("Cache-Control", "no-store");
                res.end(body + "\n");
            });

            // Only stand in for the API when it is served from this origin. Pointing
            // ADA_DEV_API_BASE at a real backend must not be shadowed by these stubs.
            if (!apiBase.startsWith("/")) return;

            // Created models live for the life of the dev server only. Persisting them
            // would mean inventing a storage format the real backend does not have.
            const PROCEDURAL = new Map<string, Record<string, unknown>>();

            const readJson = (req: {on: (e: string, cb: (c?: unknown) => void) => void}) =>
                new Promise<unknown>((resolve) => {
                    let raw = "";
                    req.on("data", (c) => (raw += c));
                    req.on("end", () => {
                        try {
                            resolve(JSON.parse(raw || "{}"));
                        } catch {
                            resolve({});
                        }
                    });
                });

            server.middlewares.use(apiBase, (req, res) => {
                const url = new URL(req.url ?? "/", "http://localhost");
                const route = url.pathname;

                // Identity + scopes. AuthGate populates meStore and the scope list from
                // this before anything else can load.
                if (route === "/me") {
                    return sendJson(res, {
                        sub: "dev",
                        email: "dev@localhost",
                        displayName: "Dev User",
                        isAdmin: true,
                        // Two scopes so the title-bar picker actually renders — it
                        // hides itself when there is nothing to choose between, which is
                        // correct behaviour but leaves the control unreviewable.
                        scopes: [
                            {kind: "shared", id: null, name: "Shared"},
                            {kind: "user", id: "me", name: "My files"},
                        ],
                        projects: [],
                    });
                }

                // JSON file listing — used by the file pickers, the corpus tab and the
                // admin storage tab (StorageBrowser uses the flatbuffer RPC below).
                //
                // Same path serves two shapes: `?include_derived=1` returns AdminFileEntry
                // (format, available_targets, derived), which the Convert page reads. That
                // panel accesses `entry.available_targets.length` unguarded, so returning
                // the thin shape here crashes it — which is how this was found. Against a
                // real backend the fields are always present; the fixture has to match the
                // contract, not approximate it.
                const files = /^\/scopes\/[^/]+\/files$/.exec(route);
                if (files) {
                    const withDerived = url.searchParams.get("include_derived") === "1";
                    if (!withDerived) {
                        return sendJson(res, {files: FIXTURE_FILES.map((f) => ({key: f.name, size: 0}))});
                    }
                    return sendJson(res, {
                        files: FIXTURE_FILES.filter((f) => !f.name.startsWith("_derived/")).map((f) => ({
                            key: f.name,
                            size: 0,
                            last_modified: f.modified,
                            format: f.name.split(".").pop() ?? "",
                            // Empty: no workers are registered in the fixture, so nothing
                            // is convertible. The panel renders that state correctly.
                            available_targets: [],
                            derived: [],
                        })),
                    });
                }

                // The flatbuffer RPC. Only LIST_FILE_OBJECTS is answered; everything else
                // gets 204 (no Message to dispatch), which the client treats as a silent
                // no-op rather than an error — the honest response for a command this
                // fixture does not implement.
                if (route === "/rpc" && req.method === "POST") {
                    return void readBody(req as never).then((body) => {
                        let command: number | null = null;
                        let instanceId = 0;
                        try {
                            const msg = Message.getRootAsMessage(
                                new flatbuffers.ByteBuffer(new Uint8Array(body)),
                            );
                            command = msg.commandType();
                            instanceId = Number(msg.instanceId() ?? 0);
                        } catch {
                            /* unparseable: fall through to 204 */
                        }

                        if (command === CommandType.LIST_FILE_OBJECTS) {
                            const reply = buildFileListReply(instanceId);
                            res.statusCode = 200;
                            res.setHeader("Content-Type", "application/octet-stream");
                            res.setHeader("Cache-Control", "no-store");
                            return res.end(Buffer.from(reply));
                        }
                        res.statusCode = 204;
                        res.end();
                    });
                }

                // ── Procedural models ────────────────────────────────────────────
                //
                // Enough of the API to CREATE and open a model, so the Build mode is
                // reviewable without a backend. Everything the cellbuilder does after
                // that (commit, compile, catalogs) still 404s and still says so.
                //
                // Added because "New procedural model…" reported "404 Not Found", which
                // reads as a broken feature rather than as an unimplemented stub — and a
                // fixture that makes a whole mode unreachable is a fixture that stops
                // people reviewing that mode.
                const procList = /^\/scopes\/[^/]+\/procedural-models\/?$/.exec(route);
                if (procList && req.method === "POST") {
                    return readJson(req).then((body) => {
                        const name = String((body as {name?: string}).name ?? "Untitled");
                        const id = `dev-${Date.now().toString(36)}`;
                        const model = {
                            id,
                            name,
                            revision: 1,
                            created_by: "dev",
                            created_at: new Date().toISOString(),
                            updated_at: new Date().toISOString(),
                            latest_glb_key: null,
                            // An empty document: no cells, no equipment. That is what a
                            // real create returns, and it is what makes the empty-state
                            // copy in the Build panel reviewable.
                            doc: {spaces: [], equipments: [], systems: [], groups: []},
                        };
                        PROCEDURAL.set(id, model);
                        return sendJson(res, model);
                    });
                }
                if (procList && req.method === "GET") {
                    // {models: […]} — the key the real API uses and viewerApi reads. Getting this
                    // wrong returned a 200 whose body was the right SHAPE but the wrong
                    // NAME, so the caller got undefined and the whole Files panel crashed
                    // on .length. A fixture that lies is worse than one that 404s.
                    return sendJson(res, {models: [...PROCEDURAL.values()].map(({doc, ...rest}) => rest)});
                }
                const procOne = /^\/scopes\/[^/]+\/procedural-models\/([^/]+)\/?$/.exec(route);
                if (procOne && req.method === "GET") {
                    const model = PROCEDURAL.get(procOne[1]);
                    if (model) return sendJson(res, model);
                }

                // Type catalogues.
                //
                // Every one of these 404'd, which emptied the + Opening and + Equipment
                // pickers and printed eight scary warnings on every model open. That is
                // not a UI bug — the same store code runs on main — but "No opening
                // types" is indistinguishable from a broken picker, so the fixture has
                // to answer.
                //
                // Faking these is honest in a way that faking compile is not: the real
                // endpoints return the union of code-defined archetypes and this scope's
                // DB entries, and the code half is a static list. These are that half,
                // tagged origin "code" exactly as the backend tags them, so nothing here
                // claims to be a catalog entry the dev server does not have.
                const catalogue = /^\/scopes\/[^/]+\/procedural-models\/([a-z-]+)$/.exec(route);
                if (catalogue && req.method === "GET") {
                    switch (catalogue[1]) {
                        case "opening-types":
                            return sendJson(res, {opening_types: OPENING_TYPES});
                        case "equipment-types":
                            return sendJson(res, {equipment_types: EQUIPMENT_TYPES});
                        case "cell-types":
                            return sendJson(res, {cell_types: CELL_TYPES});
                        case "system-types":
                            return sendJson(res, {system_types: SYSTEM_TYPES});
                        case "design-rulesets":
                            return sendJson(res, {design_rulesets: DESIGN_RULESETS});
                        case "blueprints":
                            // Blueprints seed a whole structure from a worker. Nothing
                            // static is honest here, so: none available, not an error.
                            return sendJson(res, {blueprints: []});
                        case "detailing-engines":
                            return sendJson(res, {detailing_engines: []});
                    }
                }
                // Resync pulls code archetypes into the scope's DB catalog. There is no
                // DB here, so nothing changes — which is a truthful answer, not a stub.
                if (/^\/scopes\/[^/]+\/procedural-models\/equipment-types\/resync$/.exec(route)) {
                    return sendJson(res, {updated: [], created: []});
                }
                if (/^\/scopes\/[^/]+\/procedural-engines\/?$/.exec(route) && req.method === "GET") {
                    return sendJson(res, {procedural_engines: [ENGINE]});
                }

                // Compile / preview. Deliberately NOT faked.
                //
                // Compiling runs a worker and produces a GLB; there is nothing honest a
                // static fixture can return. So it answers 501 with the reason in the
                // STATUS TEXT rather than a body: ApiError's message is built from
                // "${what} failed: ${status} ${statusText}" and drops the JSON detail,
                // so the status line is the only channel that reaches the user.
                //
                // Without this the UI showed "404 Not Found", which reads as a broken
                // feature. It is not broken — there is simply no worker here.
                const procCompile =
                    /^\/scopes\/[^/]+\/procedural-models\/[^/]+\/(compile|compile-preview)$/.exec(route);
                if (procCompile) {
                    res.statusCode = 501;
                    res.statusMessage = "compiling needs a worker - the dev REST stub has none";
                    res.setHeader("Content-Type", "application/json");
                    return res.end(JSON.stringify({
                        detail: "The dev fixture cannot compile. Run a real backend (pixi run -e viewer-api viewer-api) with a procedural worker.",
                    }));
                }

                // Blob reads. The FEA fetcher builds
                //   {apiBase}/scopes/{scope}/blobs/{encoded _derived/<src>.fea/<file>}
                const blob = /^\/scopes\/[^/]+\/blobs\/(.+)$/.exec(route);
                if (blob) {
                    const key = decodeURIComponent(blob[1]);
                    const fea = /^_derived\/.+\.fea\/(.+)$/.exec(key);
                    if (fea) {
                        // basename only — never let a crafted key escape the fixture dir.
                        return sendFile(req, res, path.join(FIXTURE_DIR, path.basename(fea[1])));
                    }
                    res.statusCode = 404;
                    return res.end(`no dev fixture for key ${key}`);
                }

                // Anything else under /api gets a clean JSON 404. Falling through to
                // vite's SPA fallback would return the index.html shell, and callers that
                // expect JSON then fail with "Unexpected token '<'" — an error that says
                // nothing about the actual problem (this fixture does not implement that
                // endpoint). The cellbuilder catalog fetches hit this constantly.
                res.statusCode = 404;
                res.setHeader("Content-Type", "application/json");
                return res.end(JSON.stringify({detail: `dev fixture does not implement ${route}`}));
            });
        },
    };
}
