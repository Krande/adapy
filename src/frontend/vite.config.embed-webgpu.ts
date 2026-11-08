import {defineConfig, type Plugin} from 'vite';

// @ts-ignore
import path from 'path';
// @ts-ignore
import fs from 'fs';

// The WebGPU-only embed (embed-webgpu/): one self-contained ESM for paradoc's 3D figures. No three.js,
// no React; adacpp's renderer (adacpp_render.js + .wasm, built from adacpp src/render with
// tools/build_render_wasm.sh) is inlined as base64, so the file works from file:// and inside
// paradoc's single-file report. ADACPP_RENDER_DIR names the directory holding the two files
// (default public/wasm); the build fails without them -- a WebGPU-only embed without its renderer
// could only ever show posters.
//
// Output: dist-embed-webgpu/index.js + index.d.ts.
const VIRTUAL = 'virtual:adacpp-render-inline';

function adacppRenderInline(): Plugin {
    const dir = path.resolve(__dirname, process.env.ADACPP_RENDER_DIR || 'public/wasm');
    return {
        name: 'adacpp-render-inline',
        resolveId(id) {
            return id === VIRTUAL ? '\0' + VIRTUAL : null;
        },
        load(id) {
            if (id !== '\0' + VIRTUAL) return null;
            const js = path.join(dir, 'adacpp_render.js');
            const wasm = path.join(dir, 'adacpp_render.wasm');
            if (!fs.existsSync(js) || !fs.existsSync(wasm)) {
                this.error(`adacpp_render.js/.wasm not found in ${dir} (set ADACPP_RENDER_DIR)`);
            }
            const b64 = (f: string) => fs.readFileSync(f).toString('base64');
            return `export default {js: ${JSON.stringify(b64(js))}, wasm: ${JSON.stringify(b64(wasm))}};`;
        },
    };
}

function copyTypes(): Plugin {
    return {
        name: 'copy-embed-webgpu-types',
        writeBundle(options) {
            const out = options.dir || path.resolve(__dirname, 'dist-embed-webgpu');
            fs.copyFileSync(path.resolve(__dirname, 'embed-webgpu/index.d.ts'), path.join(out, 'index.d.ts'));
        },
    };
}

export default defineConfig({
    plugins: [adacppRenderInline(), copyTypes()],
    publicDir: false, // the app's public/ (models, wheels, wasm) is not the embed's
    define: {'process.env.NODE_ENV': JSON.stringify('production')},
    resolve: {alias: {'@': path.resolve(__dirname, 'src')}},
    build: {
        outDir: path.resolve(__dirname, 'dist-embed-webgpu'),
        emptyOutDir: true,
        target: 'es2022',
        lib: {entry: path.resolve(__dirname, 'embed-webgpu/index.ts'), formats: ['es'], fileName: () => 'index.js'},
        rollupOptions: {external: [], output: {inlineDynamicImports: true}},
    },
});
