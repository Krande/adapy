import {mergeConfig} from 'vite';
import base from './vite.config';

// Dev server for `ada desktop --dev` (see src/desktop/README.md): the regular config plus a proxy to
// the local REST viewer, so the page loaded from Vite (with HMR) talks to the same-origin /api,
// /config.js and /healthz it would find when served by FastAPI. `ada desktop --dev` sets
// ADA_DESKTOP_BACKEND to the viewer's URL.
const backend = process.env.ADA_DESKTOP_BACKEND;
if (!backend) {
    throw new Error('ADA_DESKTOP_BACKEND is not set (e.g. http://127.0.0.1:38500/); start this through `ada desktop --dev`');
}

export default mergeConfig(base, {
    server: {
        proxy: {
            '/api': {target: backend, changeOrigin: false, ws: true},
            '/config.js': {target: backend},
            '/healthz': {target: backend},
        },
    },
});
