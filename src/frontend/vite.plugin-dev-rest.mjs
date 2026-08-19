import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

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

            server.middlewares.use(apiBase, (req, res, next) => {
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
                        scopes: [{kind: "user", id: "me", name: "My files"}],
                        projects: [],
                    });
                }

                // File listing for the storage browser: just the fixture source, so the
                // FEA deck is reachable from the UI rather than only by deep link.
                const files = /^\/scopes\/[^/]+\/files$/.exec(route);
                if (files) {
                    return sendJson(res, {
                        files: hasFixture ? [{key: `${FIXTURE_SRC}.rmed`, size: 0}] : [],
                    });
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

                next();
            });
        },
    };
}
