import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
    plugins: [react()],
    root: path.resolve(__dirname, 'src'), // Set the root directory to 'src'
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
    base: './',
});
