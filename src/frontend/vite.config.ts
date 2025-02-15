import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// @ts-ignore
import path from 'path';
//import * as visualizer from 'rollup-plugin-visualizer';

// Detect Jupyter-specific build
const isJupyter = process.env.JUPYTER_BUILD === "true";

export default defineConfig({
    root: path.resolve(__dirname, 'src'), // Set the root directory to 'src'
    base: './',
    plugins: [react() ],// , visualizer({open: true, gzipSize: true, brotliSize: true})],
    build: {
        outDir: path.resolve(__dirname, isJupyter ? 'jupyter_react_widget/jupyter-dist': 'dist'), // Output directory outside of 'src'
        sourcemap: false,
        rollupOptions: {
            input: isJupyter
                ? path.resolve(__dirname, 'src/widget.ts')  // Jupyter Widget Entry
                : path.resolve(__dirname, 'src/index.html'), // Normal Frontend Entry
            output: {
                manualChunks: undefined
            }
        }
    },

});
