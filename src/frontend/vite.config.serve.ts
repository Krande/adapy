import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// @ts-ignore
import path from 'path';

// Serve config — split-chunk build for the cloud viewer image. Distinct
// from vite.config.ts (whose `manualChunks: undefined` is a contract with
// embed-script.cjs, which expects a single index-*.js to inline into
// `dist/index.html` for the Python package's offline viewer bundle).
//
// Here we let rollup chunk the heavy libs separately so the browser can
// parallel-load them and cache them across releases. three.js dominates;
// camera-controls and urdf-loader live next to it because they import
// three and would otherwise pull a partial copy into another chunk.
export default defineConfig({
    root: path.resolve(__dirname, 'src'),
    publicDir: path.resolve(__dirname, 'public'),
    base: '/',
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, 'src'),
        },
    },
    build: {
        outDir: path.resolve(__dirname, 'dist'),
        sourcemap: false,
        rollupOptions: {
            input: path.resolve(__dirname, 'src/index.html'),
            output: {
                manualChunks: {
                    three: ['three', 'camera-controls', 'urdf-loader'],
                    react: ['react', 'react-dom'],
                    flow: ['@xyflow/react'],
                },
            },
        },
    },
});
