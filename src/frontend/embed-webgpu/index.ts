// The WebGPU-only ada-viewer embed: what a report figure needs and nothing else -- no three.js, no
// React, no app UI. adacpp's renderer (wasm, inlined) draws; models are read by adacpp's own GLB reader,
// the one the offscreen posters use, so a figure looks like its poster. Without WebGPU (or when
// loading fails) a figure shows its poster (`posterUrl`).
//
// Same entry points and options as the three.js embed (embed/index.d.ts), plus `posterUrl`.
import type {CameraPreset, FeaArtefactFetcher, MountedViewer} from "../embed/index.d";
import {parseFieldBlob} from "../src/services/feaFieldBlob";
import {parseMeshEdges} from "../src/services/feaMeshEdges";
import {abaqus} from "../src/utils/scene/fea/colormaps";
import {pickModeEntry} from "../src/utils/scene/fea/feaModePick";
import {translationOffsets, warpValue} from "../src/utils/scene/fea/warpComponents";
import {InstanceFlag, Topology} from "../src/viewport/adacpp/types";
import type {FeaManifest, FeaManifestField} from "../src/services/viewerApi";
import {showFallback, Viewer} from "./viewer";

export {webGpuUnavailableReason} from "./renderer";

export interface MountViewerOptions {
    modelBytes: Uint8Array;
    camera: CameraPreset;
    caption?: string;
    /** The figure's poster: shown when the 3D view cannot run. */
    posterUrl?: string;
    /** Accepted for the three.js embed's API; this embed has no panels. */
    showControls?: boolean;
    onReady?: () => void;
    onError?: (err: Error) => void;
}

export interface MountFeaArtefactViewerOptions {
    manifest: unknown;
    fetcher: FeaArtefactFetcher;
    camera: CameraPreset;
    caption?: string;
    posterUrl?: string;
    showControls?: boolean;
    /** 0-based mode index to render (default 0). */
    modeIndex?: number;
    /** Accepted for the three.js embed's API; beams draw as lines here. */
    beamSolids?: boolean;
    onReady?: () => void;
    onError?: (err: Error) => void;
}

/** CAD edges as the viewer draws them: feature edges at 30 degrees, black. */
const FEATURE_EDGE_ANGLE_DEG = 30;
const CAD_EDGE_COLOR: [number, number, number, number] = [0, 0, 0, 1];
/** FEA element edges: white over the result (frontend utils/scene/fea/edgeColors.ts). */
const FEA_EDGE_COLOR: [number, number, number, number] = [1, 1, 1, 1];
const MEMBER_WIDTH = 3;
const IDENTITY = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

function mount(
    element: HTMLElement,
    opts: {posterUrl?: string; caption?: string; onReady?: () => void; onError?: (err: Error) => void},
    build: (viewer: Viewer) => Promise<void>,
): MountedViewer {
    const viewer = new Viewer(element);
    let disposed = false;
    (async () => {
        try {
            await viewer.start();
            await build(viewer);
            if (!disposed) opts.onReady?.();
        } catch (err) {
            if (disposed) return;
            const e = err instanceof Error ? err : new Error(String(err));
            viewer.dispose();
            showFallback(element, opts, e.message);
            opts.onError?.(e);
        }
    })();
    return {
        dispose: () => {
            disposed = true;
            viewer.dispose();
        },
    };
}

/** A CAD model (GLB bytes) as a 3D figure. */
export function mountViewer(element: HTMLElement, opts: MountViewerOptions): MountedViewer {
    return mount(element, opts, async (viewer) => {
        const loaded = viewer.loadGlb(opts.modelBytes, {
            featureEdgeAngleDeg: FEATURE_EDGE_ANGLE_DEG,
            lineColor: CAD_EDGE_COLOR,
            doubleSided: true,
        });
        if (!loaded.geometries.length) throw new Error("the model has nothing to draw");
        viewer.onReframe = () => viewer.frame(loaded.bounds, opts.camera);
        viewer.frame(loaded.bounds, opts.camera);
    });
}

interface MorphInstance {
    instance: number;
    r: number;
    g: number;
    b: number;
    flags: number;
    lineWidth: number;
    /** Lit surfaces: glTF's default material (metallic 1, roughness 1), which three.js' loader and the
     *  posters give an FEA mesh without one; lines are unlit. */
    surface?: boolean;
}

/** A mode shape from an FEA artefact bundle (mesh GLB + field blobs + edge sidecars) as a 3D figure. */
export function mountFeaArtefactViewer(element: HTMLElement, opts: MountFeaArtefactViewerOptions): MountedViewer {
    return mount(element, opts, async (viewer) => {
        const manifest = opts.manifest as FeaManifest;
        const mesh = new Uint8Array(await opts.fetcher(manifest.mesh.url));
        const entry = pickModeEntry(manifest, Math.max(0, (opts.modeIndex ?? 0) | 0));
        if (!entry?.field.blob) throw new Error("the bundle has no displacement field");
        const field = entry.field;
        const blob = parseFieldBlob(await opts.fetcher(field.blob!.url));
        const step = blob.steps[Math.min(entry.stepIndex, blob.steps.length - 1)];

        const loaded = viewer.loadGlb(mesh, {keepPositions: true, doubleSided: true, pointSize: 4});
        const main = loaded.geometries.find((g) => !g.featureEdges);
        if (!main?.positions) throw new Error("the FEA mesh has nothing to draw");
        const n = main.vertexCount;
        if (n !== blob.header.n_points) {
            throw new Error(`mesh has ${n} vertices, the displacement field ${blob.header.n_points}`);
        }

        // The mode: displacement per vertex (a relative morph), coloured by its magnitude.
        const nc = blob.header.n_components;
        const axes = translationOffsets(field);
        const disp = new Float32Array(n * 3);
        const mag = new Float32Array(n);
        let maxMag = 0;
        for (let v = 0; v < n; v++) {
            const dx = warpValue(step, v * nc, axes[0]), dy = warpValue(step, v * nc, axes[1]), dz = warpValue(step, v * nc, axes[2]);
            disp.set([dx, dy, dz], v * 3);
            mag[v] = Math.hypot(dx, dy, dz);
            if (mag[v] > maxMag) maxMag = mag[v];
        }
        const colors = packColors(mag, maxMag);
        viewer.heap([disp], ([p]) => viewer.renderer.updateMorph(main.geometry, p, n));
        viewer.heap([colors], ([p]) => viewer.renderer.updateColors(main.geometry, p, n));

        const morphing: MorphInstance[] = [];
        const tris = main.topology === Topology.Triangles;
        morphing.push({
            instance: main.instance, r: 1, g: 1, b: 1,
            flags: InstanceFlag.VertexColors | InstanceFlag.MorphRelative | InstanceFlag.DoubleSided |
                (tris ? InstanceFlag.DepthBack : InstanceFlag.Hidden), // a beam model's mesh is its node points
            lineWidth: 4,
            surface: tris,
        });

        // Element edges and beam members, on the mesh's vertices and its morph.
        const lineEdges = manifest.mesh.line_edges_url ? parseMeshEdges(await opts.fetcher(manifest.mesh.line_edges_url)) : null;
        const addLines = (pairs: Uint32Array, withColors: boolean): number =>
            viewer.heap([main.positions!, withColors ? colors : null, disp, pairs], ([pos, col, mor, idx]) => {
                const g = viewer.renderer.addGeometry(Topology.Lines, pos, n, 0, col, mor, idx, pairs.length, 0, 0, 0);
                return viewer.renderer.addInstance(g);
            });
        if (manifest.mesh.edges_url) {
            let pairs = parseMeshEdges(await opts.fetcher(manifest.mesh.edges_url));
            if (lineEdges && lineEdges.length) pairs = withoutPairs(pairs, lineEdges);
            if (pairs.length) {
                const [r, g, b] = FEA_EDGE_COLOR;
                morphing.push({instance: addLines(pairs, false), r, g, b, flags: InstanceFlag.Unlit | InstanceFlag.MorphRelative, lineWidth: 1});
            }
        }
        if (lineEdges && lineEdges.length) {
            morphing.push({
                instance: addLines(lineEdges, true), r: 1, g: 1, b: 1,
                flags: InstanceFlag.VertexColors | InstanceFlag.Unlit | InstanceFlag.MorphRelative, lineWidth: MEMBER_WIDTH,
            });
        }

        const setInfluence = (influence: number) =>
            viewer.heap([IDENTITY], ([m]) => {
                for (const mi of morphing) {
                    viewer.renderer.setInstance(
                        mi.instance, m, mi.r, mi.g, mi.b, 1, mi.flags, influence,
                        mi.surface ? 1 : 0, 1, !!mi.surface, mi.lineWidth, 0, 0,
                    );
                }
            });
        setInfluence(1);

        // Frame the deformed model: the undeformed bounds grown by the largest displacement.
        const b = loaded.bounds;
        const framed: [number, number, number, number, number, number] = [
            b[0] - maxMag, b[1] - maxMag, b[2] - maxMag, b[3] + maxMag, b[4] + maxMag, b[5] + maxMag,
        ];
        viewer.onReframe = () => viewer.frame(framed, opts.camera);
        viewer.frame(framed, opts.camera);

        addLegend(viewer.host, field, entry.stepIndex, maxMag);
        addPlayButton(viewer, setInfluence);
    });
}

/** Per-vertex RGBA8 (packed) of the viewer's result palette, normalised to the mode's peak. */
function packColors(mag: Float32Array, maxMag: number): Uint32Array {
    const out = new Uint32Array(mag.length);
    const rgb = new Float32Array(3);
    const inv = maxMag > 0 ? 1 / maxMag : 0;
    const q = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255)));
    for (let i = 0; i < mag.length; i++) {
        // As the viewer and the posters pass them: the palette's values straight through.
        abaqus(mag[i] * inv, rgb, 0);
        out[i] = (q(rgb[0]) | (q(rgb[1]) << 8) | (q(rgb[2]) << 16) | (255 << 24)) >>> 0;
    }
    return out;
}

/** `pairs` without the (unordered) pairs in `drop`. */
function withoutPairs(pairs: Uint32Array, drop: Uint32Array): Uint32Array {
    const key = (a: number, b: number) => (a < b ? a * 4294967296 + b : b * 4294967296 + a);
    const gone = new Set<number>();
    for (let i = 0; i + 1 < drop.length; i += 2) gone.add(key(drop[i], drop[i + 1]));
    const out: number[] = [];
    for (let i = 0; i + 1 < pairs.length; i += 2) if (!gone.has(key(pairs[i], pairs[i + 1]))) out.push(pairs[i], pairs[i + 1]);
    return Uint32Array.from(out);
}

/** The colour legend, as the posters (and Studio's ColorLegend) draw it. */
function addLegend(host: HTMLElement, field: FeaManifestField, stepIndex: number, maxMag: number) {
    const path = (field.group_path || []).join(" / ") || field.name_canonical || "Displacement";
    const comps = field.components || [];
    const unit = comps.map((c, i) => (["X", "DX", "U1"].includes(c.toUpperCase()) ? field.component_units?.[i] : undefined)).find(Boolean);
    const step = (field.steps || []).find((s) => s.i === stepIndex) ?? (field.steps || [])[stepIndex];
    const box = document.createElement("div");
    box.style.cssText =
        "position:absolute;top:8px;right:8px;background:rgba(30,41,59,0.92);color:#e2e8f0;border-radius:6px;" +
        "padding:6px 8px;font:10px/1.3 system-ui;pointer-events:none;max-width:45%;";
    const lines = [`<b>${esc(path)}</b>`, unit ? `Magnitude [${esc(unit)}]` : "Magnitude"];
    if (step) lines.push(`Case: ${esc(String(step.label ?? stepIndex))}`);
    const ticks = Array.from({length: 7}, (_, i) => maxMag - (i * maxMag) / 6);
    box.innerHTML =
        lines.join("<br>") +
        `<div style="display:flex;gap:6px;margin-top:4px;height:96px">` +
        `<div style="width:10px;border-radius:2px;background:linear-gradient(to bottom,#f00,#ff0,#0f0,#0ff,#00f)"></div>` +
        `<div style="display:flex;flex-direction:column;justify-content:space-between">` +
        ticks.map((t) => `<span>${fmt(t)}</span>`).join("") +
        `</div></div>`;
    host.appendChild(box);
}

function addPlayButton(viewer: Viewer, setInfluence: (v: number) => void) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "▶";
    btn.title = "Animate the mode";
    btn.style.cssText =
        "position:absolute;left:8px;bottom:8px;width:28px;height:28px;border-radius:14px;border:1px solid #cbd5e1;" +
        "background:#fff;color:#334155;font:12px system-ui;cursor:pointer;";
    let playing = false;
    let t0 = 0;
    btn.addEventListener("pointerdown", (e) => e.stopPropagation());
    btn.addEventListener("click", (e) => {
        e.stopPropagation();
        playing = !playing;
        btn.textContent = playing ? "❚❚" : "▶";
        if (!playing) {
            viewer.onFrame = null;
            setInfluence(1);
            viewer.invalidate();
            return;
        }
        t0 = performance.now();
        viewer.onFrame = (t) => {
            setInfluence(Math.sin(((t - t0) / 1600) * 2 * Math.PI));
            return true;
        };
        viewer.invalidate();
    });
    viewer.host.appendChild(btn);
}

function fmt(v: number): string {
    if (v === 0) return "0";
    const a = Math.abs(v);
    return a >= 1e4 || a < 1e-3 ? v.toExponential(2) : String(Number(v.toPrecision(4)));
}

function esc(s: string): string {
    return s.replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]!);
}
