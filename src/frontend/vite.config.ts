import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// @ts-ignore
import path from 'path';
import {versionInjectPlugin} from './version-plugin';

export default defineConfig({
    root: path.resolve(__dirname, 'src'), // Set the root directory to 'src'
    publicDir: path.resolve(__dirname, 'public'), // Set the public directory to 'public'
    // Absolute base because the SPA is served from `/` by FastAPI
    // (see ada/comms/rest/app.py StaticFiles mount). With base: './',
    // the inlined entry script in index.html resolves chunk URLs like
    // ./StorageBrowser-*.js against the page URL `/`, missing the
    // `/assets/` prefix where the chunks actually live → 404 + blank page.
    base: '/',
    plugins: [react(), versionInjectPlugin()],// , visualizer({open: true, gzipSize: true, brotliSize: true})],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, 'src'),
        },
    },
    build: {
        outDir: path.resolve(__dirname, 'dist'), // Output directory outside of 'src'
        sourcemap: false,
        // esnext: skip esbuild's syntax-lowering. Required since esbuild was
        // pinned to 0.28.1 (security fix, see package.json overrides) — 0.28
        // fails to transform the worker bundle's destructuring for the default
        // browser-list target. The viewer already requires a modern WebGL2
        // browser, so shipping un-lowered modern JS is a non-issue.
        target: 'esnext',
        rollupOptions: {
            input: path.resolve(__dirname, 'src/index.html'), // Normal Frontend Entry
            output: {
                // Single-chunk output for the offline / embedded bundle. The
                // Python package serves this as one inlined HTML (see
                // embed-script.cjs); any dynamic-import chunk that rollup
                // emits would land at /assets/foo-XXX.js, which is *not*
                // inlined — runtime imports then 404 and the SPA never
                // boots. `manualChunks: undefined` alone only suppresses
                // manual splits; `inlineDynamicImports` is what folds
                // every `await import(...)` back into the entry.
                manualChunks: undefined,
                inlineDynamicImports: true,
            }
        }
    },

});
