// Install a locally built adacpp_fea wasm module where the dev server serves it.
//
// The viewer loads /wasm/adacpp_fea.js at runtime (workers/feaEngine.worker.ts). In the image it
// comes from the pinned adacpp wasm base (deploy/Dockerfile.viewer); for development, build it in
// an adacpp checkout (`pixi run -e wasm wbuild-fea`) and copy it into public/wasm/ (gitignored):
//
//   node scripts/install-fea-wasm.mjs <dir holding adacpp_fea.js + adacpp_fea.wasm>
//   ADACPP_FEA_WASM_DIR=<dir> node scripts/install-fea-wasm.mjs
//
// Without the module the viewer materialises every load combination on the server.

import {copyFileSync, existsSync, mkdirSync} from "node:fs";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = process.argv[2] || process.env.ADACPP_FEA_WASM_DIR;
if (!src) {
    console.error("usage: node scripts/install-fea-wasm.mjs <dir with adacpp_fea.js / .wasm>");
    process.exit(2);
}
const dest = resolve(here, "..", "public", "wasm");
mkdirSync(dest, {recursive: true});
for (const name of ["adacpp_fea.js", "adacpp_fea.wasm", "adacpp_fea.d.ts"]) {
    const from = join(src, name);
    if (!existsSync(from)) {
        if (name.endsWith(".d.ts")) continue;
        console.error(`missing ${from}`);
        process.exit(1);
    }
    copyFileSync(from, join(dest, name));
    console.log(`installed ${name} -> ${dest}`);
}
