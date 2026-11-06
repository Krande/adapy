import {mergeConfig} from 'vite';
import base from './vite.config';
import {rewriteConfigJs} from './vite.desktop-proxy.mjs';

// Dev server for `ada desktop --dev` (see src/desktop/README.md): the regular config plus a proxy to
// a REST viewer, so the page loaded from Vite (with HMR) talks to the same-origin /api, /config.js
// and /healthz it would find when served by FastAPI. `ada desktop --dev` sets ADA_DESKTOP_BACKEND to
// the local viewer's URL; `ada desktop --dev --backend` sets it to a hosted server and
// ADA_DESKTOP_REMOTE=1 (TLS verified, Host/Origin presented as the server's own, config.js fetched and
// rewritten so absolute addresses of the server point back at the dev origin).
const backend = process.env.ADA_DESKTOP_BACKEND;
if (!backend) {
    throw new Error('ADA_DESKTOP_BACKEND is not set (e.g. http://127.0.0.1:38500/); start this through `ada desktop --dev`');
}
const remote = process.env.ADA_DESKTOP_REMOTE === '1';
const devOrigin = process.env.ADA_DESKTOP_DEV_ORIGIN; // e.g. http://localhost:39000
const backendOrigin = new URL(backend).origin;

const proxy: Record<string, any> = {
    '/api': {
        target: backend,
        // A hosted server routes by Host and may check the Origin of writes and websocket upgrades;
        // the page's own origin (localhost) would be refused there.
        changeOrigin: remote,
        secure: true,
        ws: true,
        configure: remote
            ? (p: any) => {
                  p.on('proxyReq', (req: any) => req.setHeader('origin', backendOrigin));
                  p.on('proxyReqWs', (req: any) => req.setHeader('origin', backendOrigin));
              }
            : undefined,
    },
    '/healthz': {target: backend, changeOrigin: remote, secure: true},
};
if (!remote) proxy['/config.js'] = {target: backend};

const remoteConfigJs = {
    name: 'ada-desktop-remote-config-js',
    configureServer(server: any) {
        server.middlewares.use('/config.js', async (req: any, res: any) => {
            try {
                const headers: Record<string, string> = {};
                if (req.headers.cookie) headers.cookie = req.headers.cookie;
                const r = await fetch(new URL('/config.js', backend), {headers});
                const text = rewriteConfigJs(await r.text(), backend, devOrigin ?? `http://${req.headers.host}`);
                res.statusCode = r.status;
                res.setHeader('Content-Type', 'application/javascript');
                res.setHeader('Cache-Control', 'no-store');
                res.end(text);
            } catch (e) {
                res.statusCode = 502;
                res.end(`// ada desktop: could not fetch /config.js from ${backendOrigin}: ${e}`);
            }
        });
    },
};

export default mergeConfig(base, {
    plugins: remote ? [remoteConfigJs] : [],
    server: {proxy},
});
