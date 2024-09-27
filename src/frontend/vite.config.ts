import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// @ts-ignore
import path from 'path';
//import * as visualizer from 'rollup-plugin-visualizer';

export default defineConfig({
    root: path.resolve(__dirname, 'src'), // Set the root directory to 'src'
    base: './',
    plugins: [react() ],// , visualizer({open: true, gzipSize: true, brotliSize: true})],
    build: {
        outDir: path.resolve(__dirname, 'dist'), // Output directory outside of 'src'
        sourcemap: false,
        rollupOptions: {
            input: './src/index.html', // Adjusted the path since 'root' is already 'src'
            output: {
                manualChunks: undefined
            }
        }
    },

});
