// Serves /config.js in the vite dev server so the SPA can be reviewed in REST mode
// without deploying the hosted stack.
//
// index.html always requests /config.js and removes the tag on 404 (that 404 is the
// normal dev path — the SPA then falls back to WS/desktop mode). This plugin answers
// that request ONLY when ADA_DEV_REST is set, so `npm run dev` keeps its default WS
// behaviour and `npm run dev:rest` gets REST mode. A static public/config.js could not
// do this: public/ is served at the root unconditionally, which would pin dev to REST.
//
// Auth is off and API_BASE points at a real backend you run yourself
// (`pixi run -e viewer-api viewer-api`, i.e. python -m ada.comms.rest). Set
// ADA_DEV_API_BASE to point elsewhere.

const DEFAULT_API_BASE = "http://localhost:8000/api";

export function adapyDevRestConfig() {
    const enabled = Boolean(process.env.ADA_DEV_REST);

    return {
        name: "adapy-dev-rest-config",
        apply: "serve",
        configureServer(server) {
            if (!enabled) return;

            const apiBase = process.env.ADA_DEV_API_BASE || DEFAULT_API_BASE;
            server.config.logger.info(
                `\n  adapy: REST dev mode ON — /config.js served, API_BASE=${apiBase}\n`,
            );

            server.middlewares.use("/config.js", (_req, res) => {
                // Mirrors the shape ada/comms/rest/app.py injects in a real deployment.
                // Kept minimal on purpose: anything the SPA treats as optional stays unset
                // so dev exercises the same "absent" branches a fresh deployment would.
                const body = [
                    `window.COMMS_MODE = "rest";`,
                    `window.API_BASE = ${JSON.stringify(apiBase)};`,
                    `window.AUTH_ENABLED = false;`,
                    `window.CONVERT_ENABLED = true;`,
                    `window.CONVERSION_MATRIX = [];`,
                    `window.ADAPY_VERSION = "dev";`,
                ].join("\n");

                res.setHeader("Content-Type", "application/javascript");
                res.setHeader("Cache-Control", "no-store");
                res.end(body + "\n");
            });
        },
    };
}
