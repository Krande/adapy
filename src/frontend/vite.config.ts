import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// @ts-ignore
import path from 'path';
import {versionInjectPlugin} from './version-plugin';
import {adapyPluginsResolver} from './vite.plugin-resolver.mjs';
import {adapyDevRestConfig} from './vite.plugin-dev-rest.mjs';

export default defineConfig({
    root: path.resolve(__dirname, 'src'), // Set the root directory to 'src'
    publicDir: path.resolve(__dirname, 'public'), // Set the public directory to 'public'
    // Absolute base because the SPA is served from `/` by FastAPI
    // (see ada/comms/rest/app.py StaticFiles mount). With base: './',
    // the inlined entry script in index.html resolves chunk URLs like
    // ./StorageBrowser-*.js against the page URL `/`, missing the
    // `/assets/` prefix where the chunks actually live → 404 + blank page.
    base: '/',
    // adapyDevRestConfig is serve-only and self-disables unless ADA_DEV_REST is set,
    // so it costs nothing in either build path.
    plugins: [react(), versionInjectPlugin(), adapyPluginsResolver(), adapyDevRestConfig()],// , visualizer({open: true, gzipSize: true, brotliSize: true})],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, 'src'),
            // Build-time plugin packages resolve to their TS source so they are
            // transformed as first-party code (avoids a node_modules TSX
            // pre-bundle for the workspace symlink). Enabled set is in plugins.json.
        },
    },
    optimizeDeps: {
        esbuildOptions: {
            // Same reason as `build.target` below, but for the dev server's dependency
            // pre-bundle, which has its own target and otherwise defaults to the browser
            // list. esbuild is pinned to 0.28.1 (security fix, see package.json overrides)
            // and that version cannot lower destructuring, so pre-bundling @xyflow/react
            // and @tanstack/react-virtual fails with 417 errors and `npm run dev` never
            // starts. Nothing to lower at esnext.
            target: 'esnext',
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
