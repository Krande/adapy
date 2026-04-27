import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// @ts-ignore
import path from 'path';

export default defineConfig({
    root: path.resolve(__dirname, 'src'), // Set the root directory to 'src'
    publicDir: path.resolve(__dirname, 'public'), // Set the public directory to 'public'
    // Absolute base because the SPA is served from `/` by FastAPI
    // (see ada/comms/rest/app.py StaticFiles mount). With base: './',
    // the inlined entry script in index.html resolves chunk URLs like
    // ./StorageBrowser-*.js against the page URL `/`, missing the
    // `/assets/` prefix where the chunks actually live → 404 + blank page.
    base: '/',
    plugins: [react() ],// , visualizer({open: true, gzipSize: true, brotliSize: true})],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, 'src'),
        },
    },
    build: {
        outDir: path.resolve(__dirname, 'dist'), // Output directory outside of 'src'
        sourcemap: false,
        rollupOptions: {
            input: path.resolve(__dirname, 'src/index.html'), // Normal Frontend Entry
            output: {
                manualChunks: undefined
            }
        }
    },

});
