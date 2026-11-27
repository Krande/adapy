// What the Pyodide worker installs for a plugin, decided from
// `/wheels/plugin-wheels-manifest.json` alone -- pure, so it can be tested.
//
// The worker (pyodide_worker.js) is a classic worker that the dev server serves
// unbundled, so it cannot import anything. Everything that needs deciding is
// decided here, on the main thread, and handed to the worker in its messages:
// the Pyodide source and lock file at boot, and an install plan per plugin job.
//
// Manifest entry (all but `wheel` optional):
//   {
//     "wheel": "<plugin>-1.0-py3-none-any.whl",
//     "entrypoint": "pkg.module:run",        // plugin job callable
//     "register": "pkg.module:register",     // run once after install
//     "deps": ["adapy", "adacpp", "pandas"], // what the plugin imports in the browser
//     "packages": [                          // dependency files baked next to the wheel,
//       {"name": "pandas", "file": "pandas-....whl", "sha256": "<hex>", "source": "pyodide"},
//       {"name": "openpyxl", "file": "openpyxl-....whl", "sha256": "<hex>", "source": "wheel"}
//     ],
//     "gate": "<plugin access gate>"         // prewarm only where this gate is open
//   }
// plus, once per manifest, an optional staged Pyodide runtime:
//   "pyodide": {"version": "0.29.4", "index_url": "/wheels/pyodide/"}
// and optional baked wheels for the worker's own pure-Python stacks:
//   "runtime": {"trimesh": {"file": "trimesh-....whl", "sha256": "<hex>"}, "pyquaternion": {...}}
//
// `source: "pyodide"` files are Pyodide's own packages (same file, same sha256 as
// its lock file). The worker loads them BY NAME with the lock pointed at the
// baked file, so Pyodide still resolves their dependencies and shared libraries
// and checks the hash. `source: "wheel"` files are other wheels, installed in
// the listed order with micropip (deps=False) after a sha256 check. Whatever a
// plugin lists in `deps` and nothing bakes comes from Pyodide's index or PyPI,
// as before: a deployment that bakes nothing behaves exactly as without this.

/** The Pyodide release the worker runs. 0.29.4: Python 3.13 + emscripten 4.0.9
 * + native wasm-EH, the ABI the adacpp wheel (cp313/pyodide_2025_0) and the
 * ifcopenshell wasm wheel target. Keep in lockstep with adacpp's
 * tools/build_wheel.py tags + pixi.toml. Build tooling that bakes Pyodide
 * packages reads this constant. */
export const PYODIDE_VERSION = "0.29.4";

export const DEFAULT_PYODIDE_INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

export const PLUGIN_WHEELS_DIR = "/wheels/";
export const PLUGIN_WHEELS_MANIFEST_URL = "/wheels/plugin-wheels-manifest.json";

export type BakedSource = "pyodide" | "wheel";

export interface BakedPackage {
    /** Normalised distribution name (PEP 503). */
    name: string;
    /** File name under /wheels/. */
    file: string;
    sha256?: string;
    source: BakedSource;
}

export interface PluginWheelEntry {
    wheel: string;
    entrypoint?: string;
    register?: string;
    deps?: string[];
    packages?: BakedPackage[];
    sha256?: string;
    gate?: string;
}

export interface StagedPyodide {
    version?: string;
    index_url: string;
}

export interface PluginWheelsManifest {
    plugins: Record<string, PluginWheelEntry>;
    pyodide?: StagedPyodide;
    /** Baked wheels for the worker's own pure-Python stacks (see BAKEABLE_RUNTIME_DEPS). */
    runtime?: Partial<Record<BakeableRuntimeDep, BakedPackage>>;
}

/** Deps the worker installs through its own stacks rather than by name. */
export type RuntimeDep = "adapy" | "adacpp" | "ifcopenshell" | "trimesh" | "pyquaternion";

const RUNTIME_DEPS: Record<string, RuntimeDep> = {
    adapy: "adapy",
    "ada-py": "adapy",
    adacpp: "adacpp",
    "ada-cpp": "adacpp",
    // gmsh has no wheel of its own in the browser: the adacpp Pyodide wheel links
    // gmsh into its extension (one OCCT for both) and ships gmsh's Python API as
    // the top-level `gmsh` module. A plugin that meshes names `gmsh`, and gets the
    // adacpp stack; PyPI's gmsh is a native build Pyodide cannot load.
    gmsh: "adacpp",
    ifcopenshell: "ifcopenshell",
    trimesh: "trimesh",
    pyquaternion: "pyquaternion",
};

/** The worker's stacks that are a single pure wheel, which a build may bake next
 * to the plugin wheels (manifest `runtime`: {"trimesh": {"file", "sha256"}}).
 * Baked, the worker installs that file (deps=False; numpy, their only
 * dependency, is loaded at boot) instead of asking PyPI at run time. */
export const BAKEABLE_RUNTIME_DEPS = ["trimesh", "pyquaternion"] as const;
export type BakeableRuntimeDep = (typeof BAKEABLE_RUNTIME_DEPS)[number];

/** Deps that are loaded from Pyodide's own index by name when nothing bakes them
 * (everything else unbaked goes through micropip, which also knows the index). */
const PYODIDE_INDEX_DEPS = new Set(["pydantic", "h5py", "sqlite3", "scipy", "pillow", "numpy"]);

/** PEP 503 name normalisation; Pyodide's lock keys use the same form. */
export function normalizeName(name: string): string {
    return String(name).trim().toLowerCase().replace(/[-_.]+/g, "-");
}

/** The distribution name a package file carries: the first `-` field of a wheel
 * (PEP 427) or of a Pyodide shared-library zip (`libopenblas-0.3.26.zip`). */
export function distNameOfFile(file: string): string {
    const base = String(file).split("/").pop() ?? "";
    return normalizeName(base.split("-")[0] ?? "");
}

const SAFE_FILE = /^[A-Za-z0-9][A-Za-z0-9._+-]*\.(whl|zip|tar)$/;
const SHA256 = /^[0-9a-f]{64}$/;

/** One `packages` item: a file name (installed as a wheel) or an object. Files
 * must be plain names under /wheels/ -- no paths, no URLs. */
export function parseBakedPackage(raw: unknown): BakedPackage {
    const obj = typeof raw === "string" ? {file: raw} : (raw as Record<string, unknown> | null);
    if (!obj || typeof obj !== "object") throw new Error(`baked package ${JSON.stringify(raw)} is not a file name or object`);
    const file = String(obj.file ?? "").trim();
    if (!SAFE_FILE.test(file)) throw new Error(`baked package file ${JSON.stringify(obj.file)} is not a plain file name`);
    const sha = obj.sha256 === undefined || obj.sha256 === null ? undefined : String(obj.sha256).trim().toLowerCase();
    if (sha !== undefined && !SHA256.test(sha)) throw new Error(`baked package ${file}: sha256 is not 64 hex digits`);
    const source: BakedSource = obj.source === "pyodide" ? "pyodide" : "wheel";
    if (source === "wheel" && !file.endsWith(".whl")) throw new Error(`baked package ${file}: only wheels install outside Pyodide's lock`);
    const name = normalizeName(typeof obj.name === "string" && obj.name.trim() ? obj.name : distNameOfFile(file));
    return {name, file, sha256: sha, source};
}

/** Validate a parsed manifest. Bad entries are dropped with a reason rather
 * than failing every plugin; a bad `packages` item drops only that item. */
export function parsePluginWheelsManifest(
    raw: unknown,
    warn: (msg: string) => void = () => {},
): PluginWheelsManifest {
    const out: PluginWheelsManifest = {plugins: {}};
    const doc = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    const plugins = doc.plugins && typeof doc.plugins === "object" ? (doc.plugins as Record<string, unknown>) : {};
    for (const [id, value] of Object.entries(plugins)) {
        const e = value && typeof value === "object" ? (value as Record<string, unknown>) : null;
        if (!e || typeof e.wheel !== "string" || !e.wheel.trim()) {
            warn(`plugin-wheels-manifest: "${id}" has no wheel; ignored`);
            continue;
        }
        const entry: PluginWheelEntry = {wheel: e.wheel.trim()};
        if (typeof e.entrypoint === "string" && e.entrypoint) entry.entrypoint = e.entrypoint;
        if (typeof e.register === "string" && e.register) entry.register = e.register;
        if (typeof e.gate === "string" && e.gate.trim()) entry.gate = e.gate.trim().toLowerCase();
        if (typeof e.sha256 === "string" && SHA256.test(e.sha256.toLowerCase())) entry.sha256 = e.sha256.toLowerCase();
        if (Array.isArray(e.deps)) entry.deps = e.deps.filter((d): d is string => typeof d === "string" && !!d.trim());
        if (Array.isArray(e.packages)) {
            const pkgs: BakedPackage[] = [];
            for (const item of e.packages) {
                try {
                    pkgs.push(parseBakedPackage(item));
                } catch (err) {
                    warn(`plugin-wheels-manifest: "${id}": ${err instanceof Error ? err.message : String(err)}; ignored`);
                }
            }
            entry.packages = pkgs;
        }
        out.plugins[id] = entry;
    }
    const p = doc.pyodide && typeof doc.pyodide === "object" ? (doc.pyodide as Record<string, unknown>) : null;
    if (p && typeof p.index_url === "string" && p.index_url.trim()) {
        out.pyodide = {index_url: p.index_url.trim(), ...(typeof p.version === "string" ? {version: p.version} : {})};
    }
    const rt = doc.runtime && typeof doc.runtime === "object" ? (doc.runtime as Record<string, unknown>) : null;
    if (rt) {
        const runtime: Partial<Record<BakeableRuntimeDep, BakedPackage>> = {};
        for (const [name, item] of Object.entries(rt)) {
            const dep = BAKEABLE_RUNTIME_DEPS.find((d) => d === normalizeName(name));
            if (!dep) {
                warn(`plugin-wheels-manifest: runtime "${name}" is not a stack the worker can take baked; ignored`);
                continue;
            }
            try {
                const obj = typeof item === "string" ? {file: item} : (item as Record<string, unknown>);
                const pkg = parseBakedPackage({file: obj?.file, sha256: obj?.sha256, source: "wheel"});
                if (pkg.name !== dep) throw new Error(`${pkg.file} is not a ${dep} wheel`);
                runtime[dep] = pkg;
            } catch (err) {
                warn(`plugin-wheels-manifest: runtime "${name}": ${err instanceof Error ? err.message : String(err)}; ignored`);
            }
        }
        if (Object.keys(runtime).length > 0) out.runtime = runtime;
    }
    return out;
}

/** The baked runtime wheels the worker boots with: {name: {file, sha256?}}. */
export function bakedRuntimeWheels(manifest: PluginWheelsManifest): Record<string, {file: string; sha256?: string}> {
    const out: Record<string, {file: string; sha256?: string}> = {};
    for (const [name, pkg] of Object.entries(manifest.runtime ?? {})) {
        if (pkg) out[name] = {file: pkg.file, ...(pkg.sha256 ? {sha256: pkg.sha256} : {})};
    }
    return out;
}

let cachedManifest: Promise<PluginWheelsManifest> | null = null;

/** Fetch and cache the manifest. `{plugins: {}}` when nothing is baked (a
 * standalone viewer answers 404) or it cannot be read. */
export function loadPluginWheelsManifest(
    fetchImpl: typeof fetch = fetch,
    forceReload = false,
): Promise<PluginWheelsManifest> {
    if (!cachedManifest || forceReload) {
        cachedManifest = (async () => {
            try {
                const resp = await fetchImpl(PLUGIN_WHEELS_MANIFEST_URL, {cache: "no-store"});
                if (!resp.ok) return {plugins: {}};
                return parsePluginWheelsManifest(await resp.json(), (m) => console.warn(m));
            } catch {
                return {plugins: {}};
            }
        })();
    }
    return cachedManifest;
}

/** What the worker does to make one plugin importable, in this order. */
export interface PluginInstallPlan {
    id: string;
    wheel: string;
    wheelSha256?: string;
    entrypoint?: string;
    register?: string;
    /** The worker's own stacks (adapy runtime, adacpp, ifcopenshell, ...). */
    runtime: RuntimeDep[];
    /** Loaded by name from Pyodide's index; baked ones are redirected to /wheels/ by the lock. */
    pyodidePackages: string[];
    /** Baked non-Pyodide wheels, installed in order with deps=False. */
    bakedWheels: BakedPackage[];
    /** Unbaked deps for micropip (Pyodide's index, then PyPI). */
    micropip: string[];
}

function planOne(id: string, entry: PluginWheelEntry, siblings: (dep: string) => boolean): {
    plan: PluginInstallPlan;
    siblingDeps: string[];
} {
    const baked = entry.packages ?? [];
    const bakedNames = new Set(baked.map((p) => p.name));
    const runtime: RuntimeDep[] = [];
    const pyodidePackages: string[] = [];
    const micropip: string[] = [];
    const siblingDeps: string[] = [];
    const push = <T>(list: T[], v: T) => {
        if (!list.includes(v)) list.push(v);
    };
    for (const pkg of baked) if (pkg.source === "pyodide") push(pyodidePackages, pkg.name);
    for (const dep of entry.deps ?? []) {
        const n = normalizeName(dep);
        if (RUNTIME_DEPS[n]) push(runtime, RUNTIME_DEPS[n]);
        else if (siblings(dep)) push(siblingDeps, dep);
        else if (bakedNames.has(n)) continue;
        else if (PYODIDE_INDEX_DEPS.has(n)) push(pyodidePackages, dep);
        else push(micropip, dep);
    }
    return {
        plan: {
            id,
            wheel: entry.wheel,
            ...(entry.sha256 ? {wheelSha256: entry.sha256} : {}),
            ...(entry.entrypoint ? {entrypoint: entry.entrypoint} : {}),
            ...(entry.register ? {register: entry.register} : {}),
            runtime,
            pyodidePackages,
            bakedWheels: baked.filter((p) => p.source === "wheel"),
            micropip,
        },
        siblingDeps,
    };
}

/** The plans that make `pluginId` importable: plugins it depends on (other
 * manifest entries named in `deps`) first, itself last. */
export function planPluginInstall(manifest: PluginWheelsManifest, pluginId: string): PluginInstallPlan[] {
    const plugins = manifest.plugins ?? {};
    const out: PluginInstallPlan[] = [];
    const done = new Set<string>();
    const visit = (id: string, stack: string[]) => {
        if (done.has(id)) return;
        if (stack.includes(id)) throw new Error(`Circular plugin wheel dependency: ${[...stack, id].join(" -> ")}`);
        const entry = plugins[id];
        if (!entry) throw new Error(`No browser wheel registered in plugin-wheels-manifest.json for "${id}"`);
        const {plan, siblingDeps} = planOne(id, entry, (dep) => dep !== id && dep in plugins);
        for (const dep of siblingDeps) visit(dep, [...stack, id]);
        done.add(id);
        out.push(plan);
    };
    visit(pluginId, []);
    return out;
}

/** Plans for several plugins, each plugin once, dependencies first. */
export function planPluginsInstall(manifest: PluginWheelsManifest, pluginIds: readonly string[]): PluginInstallPlan[] {
    const seen = new Set<string>();
    const out: PluginInstallPlan[] = [];
    for (const id of pluginIds) {
        for (const plan of planPluginInstall(manifest, id)) {
            if (seen.has(plan.id)) continue;
            seen.add(plan.id);
            out.push(plan);
        }
    }
    return out;
}

/** Every baked Pyodide package across the manifest, once per name. */
export function bakedPyodidePackages(manifest: PluginWheelsManifest): BakedPackage[] {
    const byName = new Map<string, BakedPackage>();
    for (const entry of Object.values(manifest.plugins ?? {})) {
        for (const pkg of entry.packages ?? []) {
            if (pkg.source === "pyodide" && !byName.has(pkg.name)) byName.set(pkg.name, pkg);
        }
    }
    return [...byName.values()];
}

export interface PyodideSource {
    /** Absolute index URL ending in "/". */
    indexURL: string;
    /** "runtime" (window.PYODIDE_INDEX_URL), "staged" (manifest), "default" (jsDelivr). */
    origin: "runtime" | "staged" | "default";
}

function absoluteDir(url: string, base: string): string {
    const abs = new URL(url, base).href;
    return abs.endsWith("/") ? abs : `${abs}/`;
}

/** Where Pyodide loads from: the deployment's runtime setting, else a runtime the
 * build staged next to the wheels (only for this worker's Pyodide version), else
 * jsDelivr. `pageUrl` resolves origin-relative values. */
export function resolvePyodideSource(
    runtimeIndexUrl: string | null | undefined,
    manifest: PluginWheelsManifest,
    pageUrl: string,
    warn: (msg: string) => void = () => {},
): PyodideSource {
    const configured = (runtimeIndexUrl ?? "").trim();
    if (configured) return {indexURL: absoluteDir(configured, pageUrl), origin: "runtime"};
    const staged = manifest.pyodide;
    if (staged?.index_url) {
        if (!staged.version || staged.version === PYODIDE_VERSION) {
            return {indexURL: absoluteDir(staged.index_url, pageUrl), origin: "staged"};
        }
        warn(`staged Pyodide ${staged.version} is not ${PYODIDE_VERSION}; loading Pyodide from ${DEFAULT_PYODIDE_INDEX_URL}`);
    }
    return {indexURL: DEFAULT_PYODIDE_INDEX_URL, origin: "default"};
}

interface LockPackage {
    file_name?: string;
    sha256?: string;
    [k: string]: unknown;
}

export interface LockPatch<L> {
    lock: L;
    redirected: string[];
    skipped: Array<{name: string; reason: string}>;
}

/** Point Pyodide's lock file at the baked copies of its packages. A package is
 * redirected only when the lock names it with the same sha256 as the baked file
 * (so Pyodide's own integrity check still holds); anything else keeps loading
 * from the index. The input lock is not modified. */
export function patchPyodideLock<L extends {packages?: Record<string, LockPackage>}>(
    lock: L,
    baked: readonly BakedPackage[],
    wheelsBaseUrl: string,
): LockPatch<L> {
    const packages = {...(lock.packages ?? {})};
    const redirected: string[] = [];
    const skipped: Array<{name: string; reason: string}> = [];
    const base = wheelsBaseUrl.endsWith("/") ? wheelsBaseUrl : `${wheelsBaseUrl}/`;
    for (const pkg of baked) {
        if (pkg.source !== "pyodide") continue;
        const entry = packages[pkg.name];
        if (!entry) {
            skipped.push({name: pkg.name, reason: "not in Pyodide's lock file"});
            continue;
        }
        if (!pkg.sha256) {
            skipped.push({name: pkg.name, reason: "no sha256 in the manifest"});
            continue;
        }
        if (String(entry.sha256 ?? "").toLowerCase() !== pkg.sha256) {
            skipped.push({name: pkg.name, reason: "sha256 differs from Pyodide's lock file"});
            continue;
        }
        packages[pkg.name] = {...entry, file_name: `${base}${encodeURIComponent(pkg.file)}`};
        redirected.push(pkg.name);
    }
    return {lock: {...lock, packages}, redirected, skipped};
}

/** Which manifest plugins to install ahead of use: those enabled where the user
 * is, whose plugin-job execution order runs Pyodide before (or without) the
 * worker. Plugins they depend on come with them through their plans. */
export function selectPrewarmPlugins(
    manifest: PluginWheelsManifest,
    deps: {isEnabled: (id: string, entry: PluginWheelEntry) => boolean; order: (id: string) => readonly string[]},
): string[] {
    const out: string[] = [];
    for (const [id, entry] of Object.entries(manifest.plugins ?? {})) {
        if (!deps.isEnabled(id, entry)) continue;
        const order = deps.order(id);
        const py = order.indexOf("pyodide");
        if (py < 0) continue;
        const worker = order.indexOf("worker");
        if (worker >= 0 && worker < py) continue;
        out.push(id);
    }
    return out;
}
