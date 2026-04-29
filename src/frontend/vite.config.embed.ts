import {defineConfig} from 'vite';

// @ts-ignore
import path from 'path';

// Embed bundle — produces a single self-contained ESM that exports
// `mountViewer` for paradoc to consume from its `vendor/ada-viewer/`.
// Bundles three.js inline (no externals) so paradoc can drop the artifact
// in without coordinating dep versions.
//
// Output: src/frontend/dist-embed/index.js + index.d.ts (typed via tsc).
export default defineConfig({
    publicDir: false,
    build: {
        outDir: path.resolve(__dirname, 'dist-embed'),
        emptyOutDir: true,
        sourcemap: false,
        target: 'es2022',
        lib: {
            entry: path.resolve(__dirname, 'embed/index.ts'),
            formats: ['es'],
            fileName: () => 'index.js',
        },
        rollupOptions: {
            // Bundle everything — no externals. paradoc just imports a
            // single file with no peer-dep choreography.
            external: [],
            output: {
                inlineDynamicImports: true,
            },
        },
    },
});
