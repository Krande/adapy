import {defineConfig, type Plugin} from 'vite';
import react from '@vitejs/plugin-react';

// @ts-ignore
import path from 'path';
// @ts-ignore
import fs from 'fs';

// Embed bundle — produces a single self-contained ESM that exports
// `mountViewer` for paradoc to consume from its `vendor/ada-viewer/`.
// Bundles three.js + React + adapy's selection-tree / info panels
// inline (no externals) so paradoc drops the artifact in without
// coordinating dep versions. CSS (the @tailwind base scanned across
// src/**/*.tsx) is inlined into the JS via the `inlineCssAtRuntime`
// plugin below — keeps the embed strictly single-file.
//
// Output: src/frontend/dist-embed/index.js + index.d.ts (typed via
// the separate `tsc --project tsconfig.embed.json` pass in package.json).

// Vite's library build extracts imported CSS to a sibling style.css.
// paradoc wants one file, so collect every emitted .css asset, fold
// the contents into an IIFE that injects a <style> tag on module
// load, and prepend that IIFE to the JS bundle. The data-* sentinel
// makes re-loading idempotent: a second mountViewer() doesn't pile
// on duplicate styles.
function inlineCssAtRuntime(): Plugin {
    // Vite in library mode with `cssCodeSplit:false` emits the CSS
    // through a side-channel that doesn't show up in Rollup's
    // `bundle` map at `generateBundle` time. Easiest reliable hook
    // is `writeBundle`: read the freshly-written .css files off
    // disk, prepend a style-injection IIFE to index.js, and rm the
    // sidecar so paradoc consumes a single artifact.
    return {
        name: 'inline-css-at-runtime',
        writeBundle(options) {
            const outDir = options.dir || path.resolve(__dirname, 'dist-embed');
            const entries = fs.readdirSync(outDir);
            const cssFiles = entries.filter((f: string) => f.endsWith('.css'));
            if (cssFiles.length === 0) return;
            const css = cssFiles
                .map((f: string) => fs.readFileSync(path.join(outDir, f), 'utf8'))
                .join('\n');
            const injection =
                `;(function(){` +
                `if(typeof document==='undefined')return;` +
                `if(document.querySelector('style[data-ada-viewer-embed]'))return;` +
                `var s=document.createElement('style');` +
                `s.setAttribute('data-ada-viewer-embed','');` +
                `s.textContent=${JSON.stringify(css)};` +
                `document.head.appendChild(s);` +
                `})();\n`;
            const jsPath = path.join(outDir, 'index.js');
            const js = fs.readFileSync(jsPath, 'utf8');
            fs.writeFileSync(jsPath, injection + js);
            for (const f of cssFiles) {
                try {
                    fs.unlinkSync(path.join(outDir, f));
                } catch {
                    /* already gone */
                }
            }

            // Copy the hand-maintained `embed/index.d.ts` (public
            // type contract paradoc consumes) next to `index.js`.
            // Hand-written because tsc would otherwise emit a tree
            // mirroring every imported src file or trip on `rootDir`.
            const dtsSrc = path.resolve(__dirname, 'embed/index.d.ts');
            const dtsDst = path.join(outDir, 'index.d.ts');
            try {
                fs.copyFileSync(dtsSrc, dtsDst);
            } catch (err) {
                console.warn('[inline-css-at-runtime] failed to copy index.d.ts:', err);
            }
        },
    };
}

export default defineConfig({
    publicDir: false,
    plugins: [react(), inlineCssAtRuntime()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, 'src'),
        },
    },
    build: {
        outDir: path.resolve(__dirname, 'dist-embed'),
        emptyOutDir: true,
        sourcemap: false,
        target: 'es2022',
        // We inline CSS into JS via the plugin above; this just keeps
        // any sidecar that slips through under one predictable name.
        cssCodeSplit: false,
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
