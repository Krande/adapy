import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// @ts-ignore
import path from 'path';

// Detect Jupyter-specific build
export default defineConfig({
    root: path.resolve(__dirname, 'src'), // Set the root directory to 'src'
    publicDir: path.resolve(__dirname, 'public'), // Set the public directory to 'public'
    base: './',
    plugins: [react() ],// , visualizer({open: true, gzipSize: true, brotliSize: true})],
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
