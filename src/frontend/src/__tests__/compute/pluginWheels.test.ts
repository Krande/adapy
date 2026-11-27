// What the Pyodide worker installs for a plugin (utils/pyodide/pluginWheels.ts):
// manifest parsing, the install plan (order and fallbacks), the lock file that
// points Pyodide at baked packages, where Pyodide loads from, and which plugins
// the background prewarm picks.
import assert from "node:assert/strict";
import {beforeEach, describe, it} from "node:test";

const W = await import("@/utils/pyodide/pluginWheels");
const R = await import("@/plugins/registry");
const A = await import("@/plugins/pluginAccess");
const P = await import("@/services/compute/pluginPrewarm");

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);
const SHA_C = "c".repeat(64);

const MANIFEST = W.parsePluginWheelsManifest({
    plugins: {
        engine: {
            wheel: "engine_pkg-1.0.0-py3-none-any.whl",
            register: "engine_pkg.viewer:register",
            deps: ["adapy", "adacpp", "pandas", "openpyxl", "numpy", "scipy", "ifcopenshell", "pydantic", "rich"],
            packages: [
                {name: "python-dateutil", file: "python_dateutil-2.9.0.post0-py2.py3-none-any.whl", sha256: SHA_A, source: "pyodide"},
                {name: "pandas", file: "pandas-2.3.3-cp313-cp313-pyemscripten_2025_0_wasm32.whl", sha256: SHA_B, source: "pyodide"},
                {name: "scipy", file: "scipy-1.14.1-cp313-cp313-pyemscripten_2025_0_wasm32.whl", sha256: SHA_C, source: "pyodide"},
                {name: "et-xmlfile", file: "et_xmlfile-2.0.0-py3-none-any.whl", sha256: SHA_A, source: "wheel"},
                {name: "openpyxl", file: "openpyxl-3.1.5-py2.py3-none-any.whl", sha256: SHA_B, source: "wheel"},
            ],
        },
        surface: {wheel: "surface_pkg-2.0.0-py3-none-any.whl", deps: ["adapy", "pydantic"]},
        job: {
            wheel: "job_pkg-3.0.0-py3-none-any.whl",
            entrypoint: "job_pkg.run:main",
            deps: ["adapy", "surface", "typer"],
            gate: "Job-Gate",
        },
    },
});

describe("parsePluginWheelsManifest", () => {
    it("keeps valid entries and normalises names, gates and hashes", () => {
        const engine = MANIFEST.plugins.engine;
        assert.equal(engine.packages?.length, 5);
        assert.equal(engine.packages?.[0].name, "python-dateutil");
        assert.equal(MANIFEST.plugins.job.gate, "job-gate");
    });

    it("drops an entry without a wheel and a package that is a path, a URL or a bad hash", () => {
        const warnings: string[] = [];
        const m = W.parsePluginWheelsManifest(
            {
                plugins: {
                    nowheel: {deps: ["adapy"]},
                    p: {
                        wheel: "p-1-py3-none-any.whl",
                        packages: [
                            "../escape-1.0-py3-none-any.whl",
                            "https://example.invalid/x-1.0-py3-none-any.whl",
                            {file: "y-1.0-py3-none-any.whl", sha256: "nothex"},
                            {file: "libfoo-1.0.zip"},
                            "z_pkg-1.0-py3-none-any.whl",
                        ],
                    },
                },
            },
            (m) => warnings.push(m),
        );
        assert.deepEqual(Object.keys(m.plugins), ["p"]);
        // A plain string is a wheel installed outside the lock; its name comes from the file.
        assert.deepEqual(m.plugins.p.packages, [{name: "z-pkg", file: "z_pkg-1.0-py3-none-any.whl", sha256: undefined, source: "wheel"}]);
        assert.equal(warnings.length, 5);
    });

    it("reads nothing from a missing or malformed document", () => {
        assert.deepEqual(W.parsePluginWheelsManifest(null), {plugins: {}});
        assert.deepEqual(W.parsePluginWheelsManifest({plugins: 3}), {plugins: {}});
    });

    it("names a distribution after the first field of its file", () => {
        assert.equal(W.distNameOfFile("python_dateutil-2.9.0-py2.py3-none-any.whl"), "python-dateutil");
        assert.equal(W.distNameOfFile("libopenblas-0.3.26.zip"), "libopenblas");
        assert.equal(W.normalizeName("Typing_Extensions"), "typing-extensions");
    });
});

describe("planPluginInstall", () => {
    it("loads baked Pyodide packages by name, baked wheels in order, and leaves only the unbaked to micropip", () => {
        const [plan] = W.planPluginInstall(MANIFEST, "engine");
        assert.equal(plan.id, "engine");
        assert.deepEqual(plan.runtime, ["adapy", "adacpp", "ifcopenshell"]);
        // Baked first (in the manifest's order), then index packages nothing baked.
        assert.deepEqual(plan.pyodidePackages, ["python-dateutil", "pandas", "scipy", "numpy", "pydantic"]);
        assert.deepEqual(plan.bakedWheels.map((p) => p.file), ["et_xmlfile-2.0.0-py3-none-any.whl", "openpyxl-3.1.5-py2.py3-none-any.whl"]);
        // openpyxl is baked, so only rich is left for micropip.
        assert.deepEqual(plan.micropip, ["rich"]);
        assert.equal(plan.register, "engine_pkg.viewer:register");
    });

    it("keeps today's behaviour when nothing is baked", () => {
        const m = W.parsePluginWheelsManifest({
            plugins: {x: {wheel: "x-1-py3-none-any.whl", deps: ["adapy", "pandas", "scipy", "Pillow"]}},
        });
        const [plan] = W.planPluginInstall(m, "x");
        assert.deepEqual(plan.runtime, ["adapy"]);
        assert.deepEqual(plan.pyodidePackages, ["scipy", "Pillow"]);
        assert.deepEqual(plan.micropip, ["pandas"]);
        assert.deepEqual(plan.bakedWheels, []);
    });

    it("installs the plugins a plugin depends on first", () => {
        const plans = W.planPluginInstall(MANIFEST, "job");
        assert.deepEqual(plans.map((p) => p.id), ["surface", "job"]);
        assert.deepEqual(plans[1].micropip, ["typer"]);
        assert.equal(plans[1].entrypoint, "job_pkg.run:main");
    });

    it("lists each plugin once across several", () => {
        const plans = W.planPluginsInstall(MANIFEST, ["job", "surface", "engine"]);
        assert.deepEqual(plans.map((p) => p.id), ["surface", "job", "engine"]);
    });

    it("refuses an unknown plugin and a dependency cycle", () => {
        assert.throws(() => W.planPluginInstall(MANIFEST, "ghost"), /No browser wheel/);
        const cyclic = W.parsePluginWheelsManifest({
            plugins: {a: {wheel: "a-1-py3-none-any.whl", deps: ["b"]}, b: {wheel: "b-1-py3-none-any.whl", deps: ["a"]}},
        });
        assert.throws(() => W.planPluginInstall(cyclic, "a"), /Circular plugin wheel dependency: a -> b -> a/);
    });
});

describe("patchPyodideLock", () => {
    const lock = {
        info: {python: "3.13.2"},
        packages: {
            "python-dateutil": {file_name: "python_dateutil-2.9.0.post0-py2.py3-none-any.whl", sha256: SHA_A, depends: ["six"]},
            pandas: {file_name: "pandas-2.3.3-cp313-cp313-pyemscripten_2025_0_wasm32.whl", sha256: SHA_B},
            scipy: {file_name: "scipy-1.14.1-cp313-cp313-pyemscripten_2025_0_wasm32.whl", sha256: SHA_A},
            numpy: {file_name: "numpy-2.2.5-cp313-cp313-pyemscripten_2025_0_wasm32.whl", sha256: SHA_C},
        },
    };

    it("points matching packages at /wheels/ and leaves the rest on the index", () => {
        const baked = W.bakedPyodidePackages(MANIFEST);
        const patch = W.patchPyodideLock(lock, baked, "https://viewer.example/wheels");
        assert.deepEqual(patch.redirected, ["python-dateutil", "pandas"]);
        assert.deepEqual(patch.skipped, [{name: "scipy", reason: "sha256 differs from Pyodide's lock file"}]);
        assert.equal(
            patch.lock.packages.pandas.file_name,
            "https://viewer.example/wheels/pandas-2.3.3-cp313-cp313-pyemscripten_2025_0_wasm32.whl",
        );
        // Dependencies and everything else in an entry are kept.
        assert.deepEqual(patch.lock.packages["python-dateutil"].depends, ["six"]);
        assert.equal(patch.lock.packages.numpy.file_name, lock.packages.numpy.file_name);
        assert.deepEqual(patch.lock.info, lock.info);
        // The input is not modified.
        assert.equal(lock.packages.pandas.file_name, "pandas-2.3.3-cp313-cp313-pyemscripten_2025_0_wasm32.whl");
    });

    it("skips a package the lock does not have or the manifest gives no hash for", () => {
        const patch = W.patchPyodideLock(
            lock,
            [
                {name: "ghost", file: "ghost-1-py3-none-any.whl", sha256: SHA_A, source: "pyodide"},
                {name: "numpy", file: "numpy-2.2.5-cp313-cp313-pyemscripten_2025_0_wasm32.whl", source: "pyodide"},
            ],
            "/wheels/",
        );
        assert.deepEqual(patch.redirected, []);
        assert.deepEqual(patch.skipped.map((s) => s.name), ["ghost", "numpy"]);
    });
});

describe("resolvePyodideSource", () => {
    const page = "https://viewer.example/app/index.html";

    it("prefers the deployment's setting, made absolute", () => {
        const s = W.resolvePyodideSource("/pyodide/full", {plugins: {}}, page);
        assert.deepEqual(s, {indexURL: "https://viewer.example/pyodide/full/", origin: "runtime"});
    });

    it("uses a runtime staged next to the wheels only for the worker's Pyodide version", () => {
        const staged = {plugins: {}, pyodide: {version: W.PYODIDE_VERSION, index_url: "/wheels/pyodide/"}};
        assert.deepEqual(W.resolvePyodideSource("", staged, page), {
            indexURL: "https://viewer.example/wheels/pyodide/",
            origin: "staged",
        });
        const warnings: string[] = [];
        const old = {plugins: {}, pyodide: {version: "0.1.0", index_url: "/wheels/pyodide/"}};
        assert.equal(W.resolvePyodideSource(null, old, page, (m) => warnings.push(m)).origin, "default");
        assert.equal(warnings.length, 1);
    });

    it("defaults to jsDelivr at the pinned version", () => {
        const s = W.resolvePyodideSource(undefined, {plugins: {}}, page);
        assert.equal(s.indexURL, `https://cdn.jsdelivr.net/pyodide/v${W.PYODIDE_VERSION}/full/`);
        assert.equal(s.origin, "default");
    });
});

describe("selectPrewarmPlugins", () => {
    it("takes enabled plugins whose order runs Pyodide before the worker or without it", () => {
        const orders: Record<string, string[]> = {engine: ["pyodide", "worker"], surface: ["worker", "pyodide"], job: ["pyodide"]};
        const ids = W.selectPrewarmPlugins(MANIFEST, {isEnabled: () => true, order: (id) => orders[id]});
        assert.deepEqual(ids, ["engine", "job"]);
        const none = W.selectPrewarmPlugins(MANIFEST, {isEnabled: () => true, order: () => ["native", "worker"]});
        assert.deepEqual(none, []);
        const off = W.selectPrewarmPlugins(MANIFEST, {isEnabled: (id) => id !== "job", order: () => ["pyodide"]});
        assert.deepEqual(off, ["engine", "surface"]);
    });
});

describe("isManifestPluginEnabled", () => {
    beforeEach(() => {
        R.resetRegistry();
        A.resetPluginAccess();
    });

    it("follows a registered plugin's gate, else the entry's own gate, else is on", () => {
        R.registerPlugin({id: "surface", access: "scope"} as never);
        const surface = MANIFEST.plugins.surface;
        const job = MANIFEST.plugins.job;
        const engine = MANIFEST.plugins.engine;
        // Before the scope's answer every gate reads closed.
        assert.equal(P.isManifestPluginEnabled("surface", surface), false);
        assert.equal(P.isManifestPluginEnabled("job", job), false);
        assert.equal(P.isManifestPluginEnabled("engine", engine), true);
        A.setPluginAccess({scope: "shared", enforced: true, enabled: ["job-gate"]}, "shared");
        assert.equal(P.isManifestPluginEnabled("surface", surface), false);
        assert.equal(P.isManifestPluginEnabled("job", job), true);
        A.setPluginAccess({scope: "shared", enforced: true, enabled: ["surface"]}, "shared");
        assert.equal(P.isManifestPluginEnabled("surface", surface), true);
        R.disablePlugin("surface", "threw");
        assert.equal(P.isManifestPluginEnabled("surface", surface), false);
    });
});
