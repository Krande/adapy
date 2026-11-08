// The WebGPU-only ada-viewer embed: what a report figure needs and nothing else -- no three.js, no
// React, no app UI. adacpp's renderer (wasm, inlined) draws; models are read by adacpp's own GLB reader,
// the one the offscreen posters use, so a figure looks like its poster. Without WebGPU (or when
// loading fails) a figure shows its poster (`posterUrl`).
//
// Same entry points and options as the three.js embed (embed/index.d.ts), plus `posterUrl`.
import type {CameraPreset, FeaArtefactFetcher, MountedViewer} from "../embed/index.d";
import {parseBeamSolidsWarp} from "../src/services/feaBeamSolidsWarp";
import {parseFieldBlob} from "../src/services/feaFieldBlob";
import {parseMeshEdges} from "../src/services/feaMeshEdges";
import {parseMeshElements} from "../src/services/feaMeshElements";
import {beamSolidFeatureEdges} from "../src/utils/scene/fea/beamSolidFeatureEdges";
import {beamSolidDisplacement} from "../src/utils/scene/fea/beamSolidWarp";
import {abaqus} from "../src/utils/scene/fea/colormaps";
import {listDisplacementFields} from "../src/utils/scene/fea/feaModePick";
import {translationOffsets, warpValue} from "../src/utils/scene/fea/warpComponents";
import {InstanceFlag, SegmentFlag, Topology} from "../src/viewport/adacpp/types";
import type {RawPick} from "../src/viewport/adacpp/types";
import type {FeaManifest, FeaManifestField} from "../src/services/viewerApi";
import {glbJson, matchGeometries, objectPath, replayBatches} from "./glbObjects";
import {fmtNum, hint, ICONS, infoTable, Toolbar} from "./toolbar";
import {showFallback, Viewer} from "./viewer";

export {FALLBACK_CLASS} from "./viewer";

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
    /** Start with beam elements drawn as their solid sections, as the posters draw them (default
     *  false: lines). Only matters for a bundle with beam solids; a button switches. */
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
        addCadToolbar(viewer, opts.modelBytes, loaded);
    });
}

/** Ada Studio's selection colour (blue), packed RGBA8. */
const SELECTED = (0 | (0 << 8) | (255 << 16) | (255 << 24)) >>> 0;

/** Object info (tap an object: its name, its parents, the point) and scene info for a CAD figure. */
function addCadToolbar(viewer: Viewer, bytes: Uint8Array, loaded: ReturnType<Viewer["loadGlb"]>) {
    const json = glbJson(bytes);
    const records = json ? replayBatches(json) : [];
    const names = matchGeometries(records, loaded.geometries, (g) => viewer.renderer.segmentCount(g));
    const ids = new Set<string>();
    for (const segs of names.values()) for (const s of segs) if (s?.id) ids.add(s.id);
    const triangles = countTriangles(json);
    const [x0, y0, z0, x1, y1, z1] = loaded.bounds;

    let picked: {pick: RawPick; label: string[]; id?: string} | null = null;
    let lit: {instance: number; count: number} | null = null;
    const toolbar = new Toolbar(viewer.host);
    toolbar.add("info", {
        title: "Object info",
        icon: ICONS.info,
        render: (body) => {
            if (!picked) return void body.append(hint("Tap a part of the model."));
            const w = picked.pick.world;
            const rows: [string, string][] = [];
            if (picked.label.length) rows.push(["Name", picked.label[picked.label.length - 1]]);
            if (picked.label.length > 1) rows.push(["In", picked.label.slice(0, -1).join(" / ")]);
            if (picked.id) rows.push(["Id", picked.id]);
            rows.push(["Point", w.map(fmtNum).join(", ")]);
            body.append(infoTable(rows));
        },
    });
    toolbar.add("scene", {
        title: "Scene info",
        icon: ICONS.scene,
        render: (body) => {
            const rows: [string, string][] = [];
            if (ids.size) rows.push(["Objects", String(ids.size)]);
            if (triangles) rows.push(["Triangles", triangles.toLocaleString("en")]);
            rows.push(["Size", [x1 - x0, y1 - y0, z1 - z0].map(fmtNum).join(" × ")]);
            if (loaded.featureEdges) rows.push(["Edges", loaded.featureEdges.toLocaleString("en")]);
            rows.push(["Renderer", "WebGPU"]);
            body.append(infoTable(rows));
        },
    });

    viewer.onTap = async (cx, cy) => {
        const r = await viewer.pick(cx, cy);
        if (lit) {
            viewer.setSegmentStates(lit.instance, new Uint32Array(lit.count * 2));
            lit = null;
        }
        if (!r) {
            picked = null;
        } else {
            const seg = names.get(r.instance)?.[r.segment] ?? null;
            const label = seg?.id ? objectPath(json, seg.id) : seg?.node ? [seg.node] : [];
            picked = {pick: r, label: label.length || !seg?.id ? label : [seg.id], id: seg?.id};
            const segs = names.get(r.instance);
            if (seg && segs) {
                const states = new Uint32Array(segs.length * 2);
                states[r.segment * 2] = SELECTED;
                states[r.segment * 2 + 1] = SegmentFlag.Matte;
                viewer.setSegmentStates(r.instance, states);
                lit = {instance: r.instance, count: segs.length};
            }
            toolbar.toggle("info", true);
        }
        toolbar.refresh("info");
    };
}

/** Triangles in a GLB's triangle primitives, from its accessor counts. */
function countTriangles(json: ReturnType<typeof glbJson>): number {
    let n = 0;
    for (const m of json?.meshes ?? []) {
        for (const p of m.primitives ?? []) {
            if ((p.mode ?? 4) !== 4) continue;
            const acc = p.indices !== undefined ? p.indices : p.attributes?.POSITION;
            n += Math.floor((json.accessors?.[acc]?.count ?? 0) / 3);
        }
    }
    return n;
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

/** One mode of the bundle: a displacement field and a step in it. */
interface ModeEntry {
    field: FeaManifestField;
    stepIndex: number;
    label: string;
}

/** Every mode, in the order `modeIndex` counts them (feaModePick.pickModeEntry's order). */
function listModes(manifest: FeaManifest): ModeEntry[] {
    const out: ModeEntry[] = [];
    for (const field of listDisplacementFields(manifest)) {
        const n = Math.max(1, field.n_steps | 0);
        for (let i = 0; i < n; i++) {
            const st = (field.steps || []).find((x) => x.i === i) ?? (field.steps || [])[i];
            const name = st?.name ? ` (${st.name})` : "";
            out.push({field, stepIndex: i, label: st ? `${out.length + 1}: ${st.label}${name}` : `Mode ${out.length + 1}`});
        }
    }
    return out;
}

/** A mode shape from an FEA artefact bundle (mesh GLB + field blobs + edge sidecars) as a 3D figure. */
export function mountFeaArtefactViewer(element: HTMLElement, opts: MountFeaArtefactViewerOptions): MountedViewer {
    return mount(element, opts, async (viewer) => {
        const manifest = opts.manifest as FeaManifest;
        const modes = listModes(manifest);
        if (!modes.length) throw new Error("the bundle has no displacement field");
        let modeIndex = Math.min(Math.max(0, (opts.modeIndex ?? 0) | 0), modes.length - 1);
        const blobs = new Map<string, Promise<ReturnType<typeof parseFieldBlob>>>();
        const blobOf = (field: FeaManifestField) => {
            const url = field.blob!.url;
            if (!blobs.has(url)) blobs.set(url, opts.fetcher(url).then(parseFieldBlob));
            return blobs.get(url)!;
        };
        const mesh = new Uint8Array(await opts.fetcher(manifest.mesh.url));
        const loaded = viewer.loadGlb(mesh, {keepPositions: true, doubleSided: true, pointSize: 4});
        const main = loaded.geometries.find((g) => !g.featureEdges);
        if (!main?.positions) throw new Error("the FEA mesh has nothing to draw");
        const n = main.vertexCount;

        /** A mode's displacement per vertex, its magnitude, peak and colours. */
        const modeData = async (i: number) => {
            const entry = modes[i];
            if (!entry.field.blob) throw new Error(`mode ${i + 1} has no field data`);
            const blob = await blobOf(entry.field);
            if (n !== blob.header.n_points) {
                throw new Error(`mesh has ${n} vertices, the displacement field ${blob.header.n_points}`);
            }
            const step = blob.steps[Math.min(entry.stepIndex, blob.steps.length - 1)];
            const nc = blob.header.n_components;
            const axes = translationOffsets(entry.field);
            const disp = new Float32Array(n * 3);
            const mag = new Float32Array(n);
            let maxMag = 0;
            for (let v = 0; v < n; v++) {
                const dx = warpValue(step, v * nc, axes[0]), dy = warpValue(step, v * nc, axes[1]), dz = warpValue(step, v * nc, axes[2]);
                disp[v * 3] = dx;
                disp[v * 3 + 1] = dy;
                disp[v * 3 + 2] = dz;
                mag[v] = Math.hypot(dx, dy, dz);
                if (mag[v] > maxMag) maxMag = mag[v];
            }
            return {entry, step, disp, mag, maxMag, colors: packColors(mag, maxMag)};
        };
        let mode = await modeData(modeIndex);
        viewer.heap([mode.disp], ([p]) => viewer.renderer.updateMorph(main.geometry, p, n));
        viewer.heap([mode.colors], ([p]) => viewer.renderer.updateColors(main.geometry, p, n));

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
        const addLines = (pairs: Uint32Array, withColors: boolean): {geometry: number; instance: number} =>
            viewer.heap([main.positions!, withColors ? mode.colors : null, mode.disp, pairs], ([pos, col, mor, idx]) => {
                const geometry = viewer.renderer.addGeometry(Topology.Lines, pos, n, 0, col, mor, idx, pairs.length, 0, 0, 0);
                return {geometry, instance: viewer.renderer.addInstance(geometry)};
            });
        let edges: {geometry: number; instance: number} | null = null;
        if (manifest.mesh.edges_url) {
            let pairs = parseMeshEdges(await opts.fetcher(manifest.mesh.edges_url));
            if (lineEdges && lineEdges.length) pairs = withoutPairs(pairs, lineEdges);
            if (pairs.length) {
                edges = addLines(pairs, false);
                const [r, g, b] = FEA_EDGE_COLOR;
                morphing.push({instance: edges.instance, r, g, b, flags: InstanceFlag.Unlit | InstanceFlag.MorphRelative, lineWidth: 1});
            }
        }
        const memberLines = lineEdges && lineEdges.length ? addLines(lineEdges, true) : null;
        const members: MorphInstance | null = memberLines
            ? {
                instance: memberLines.instance, r: 1, g: 1, b: 1,
                flags: InstanceFlag.VertexColors | InstanceFlag.Unlit | InstanceFlag.MorphRelative, lineWidth: MEMBER_WIDTH,
            }
            : null;
        if (members) morphing.push(members);

        // Beam solids: each beam's section, warped by its nodes' displacement and rotation (a torsion
        // mode twists), coloured by its own displacement, with its element edges -- the posters' look.
        const solids = await loadBeamSolids(viewer, manifest, opts.fetcher, main.positions);
        let solidPeak = solids ? solids.apply(mode.entry.field, mode.step) : 0;
        if (solids) morphing.push(...solids.instances);
        let solidsOn = !!opts.beamSolids;
        const showSolids = (on: boolean) => {
            if (!solids) return;
            for (const mi of solids.instances) mi.flags = on ? mi.flags & ~InstanceFlag.Hidden : mi.flags | InstanceFlag.Hidden;
            if (members) members.flags = on ? members.flags | InstanceFlag.Hidden : members.flags & ~InstanceFlag.Hidden;
        };
        showSolids(solidsOn);

        // The drawn deformation: the animation's phase times the reader's scale.
        let phase = 1, scale = 1;
        const applyInstances = () =>
            viewer.heap([IDENTITY], ([m]) => {
                for (const mi of morphing) {
                    viewer.renderer.setInstance(
                        mi.instance, m, mi.r, mi.g, mi.b, 1, mi.flags, phase * scale,
                        mi.surface ? 1 : 0, 1, !!mi.surface, mi.lineWidth, 0, 0,
                    );
                }
            });
        applyInstances();

        // Frame the deformed model: the undeformed bounds grown by the largest displacement.
        const b = solids ? unionBounds(loaded.bounds, solids.bounds) : loaded.bounds;
        const grow = Math.max(mode.maxMag, solidPeak);
        const framed: [number, number, number, number, number, number] = [
            b[0] - grow, b[1] - grow, b[2] - grow, b[3] + grow, b[4] + grow, b[5] + grow,
        ];
        viewer.onReframe = () => viewer.frame(framed, opts.camera);
        viewer.frame(framed, opts.camera);

        const legend = addLegend(viewer.host);
        legend.update(mode.entry.field, mode.entry.stepIndex, mode.maxMag);
        addPlayButton(viewer, (v) => {
            phase = v;
            applyInstances();
        });
        if (solids) {
            addToggle(viewer, solidsOn, (on) => {
                solidsOn = on;
                showSolids(on);
                applyInstances();
                viewer.invalidate();
            });
        }

        const toolbar = new Toolbar(viewer.host);
        let picked: {node: number; label?: number; solid: boolean; mag: number; d: [number, number, number]; p: [number, number, number]} | null = null;
        let nodeLabels: number[] | null | undefined = manifest.mesh.node_labels ?? undefined;
        const unitOf = (field: FeaManifestField) => {
            const comps = field.components || [];
            return comps.map((c, i) => (["X", "DX", "U1"].includes(c.toUpperCase()) ? field.component_units?.[i] : undefined)).find(Boolean) ?? "";
        };

        let switching: Promise<void> = Promise.resolve();
        const setMode = (i: number) => {
            switching = switching.then(async () => {
                if (i === modeIndex) return;
                const next = await modeData(i);
                modeIndex = i;
                mode = next;
                viewer.heap([mode.disp], ([p]) => {
                    viewer.renderer.updateMorph(main.geometry, p, n);
                    if (edges) viewer.renderer.updateMorph(edges.geometry, p, n);
                    if (memberLines) viewer.renderer.updateMorph(memberLines.geometry, p, n);
                });
                viewer.heap([mode.colors], ([p]) => {
                    viewer.renderer.updateColors(main.geometry, p, n);
                    if (memberLines) viewer.renderer.updateColors(memberLines.geometry, p, n);
                });
                if (solids) solidPeak = solids.apply(mode.entry.field, mode.step);
                applyInstances();
                legend.update(mode.entry.field, mode.entry.stepIndex, mode.maxMag);
                picked = null;
                toolbar.refresh("info");
                toolbar.refresh("scene");
                viewer.invalidate();
            }).catch((err) => console.error("[ada-viewer-webgpu] switching mode:", err));
        };

        toolbar.add("info", {
            title: "Object info",
            icon: ICONS.info,
            render: (body) => {
                if (!picked) return void body.append(hint("Tap the model for a node's displacement."));
                const unit = unitOf(mode.entry.field);
                const u = unit ? ` ${unit}` : "";
                const rows: [string, string][] = [];
                if (picked.solid) rows.push(["Beam solid", "point on the section"]);
                else rows.push(["Node", picked.label !== undefined ? String(picked.label) : `#${picked.node}`]);
                rows.push(["Position", picked.p.map(fmtNum).join(", ")]);
                rows.push(["Displacement", picked.d.map(fmtNum).join(", ") + u]);
                rows.push(["Magnitude", fmtNum(picked.mag) + u]);
                rows.push(["Mode", modes[modeIndex].label]);
                body.append(infoTable(rows));
            },
        });
        toolbar.add("scene", {
            title: "Scene info",
            icon: ICONS.scene,
            render: (body) => {
                const rows: [string, string][] = [
                    ["Nodes", (manifest.mesh.n_points ?? n).toLocaleString("en")],
                    ["Elements", (manifest.mesh.n_elements ?? manifest.mesh.n_cells ?? 0).toLocaleString("en")],
                    ["Modes", String(modes.length)],
                    ["Showing", modes[modeIndex].label],
                    ["Peak", fmtNum(mode.maxMag) + (unitOf(mode.entry.field) ? ` ${unitOf(mode.entry.field)}` : "")],
                ];
                if (solids) rows.push(["Beams", solidsOn ? "solid sections" : "lines"]);
                rows.push(["Renderer", "WebGPU"]);
                body.append(infoTable(rows));
            },
        });
        toolbar.add("sim", {
            title: "Simulation controls",
            icon: ICONS.sim,
            render: (body) => {
                const row = (label: string, el: HTMLElement) => {
                    const d = document.createElement("label");
                    d.style.cssText = "display:flex;align-items:center;gap:6px;margin:3px 0;";
                    d.append(label, el);
                    body.append(d);
                };
                if (modes.length > 1) {
                    const sel = document.createElement("select");
                    sel.style.cssText = "font:11px system-ui;max-width:200px;";
                    modes.forEach((m, i) => sel.add(new Option(m.label, String(i), false, i === modeIndex)));
                    sel.addEventListener("change", () => setMode(Number(sel.value)));
                    row("Mode", sel);
                }
                const range = document.createElement("input");
                range.type = "range";
                range.min = "0";
                range.max = "5";
                range.step = "0.1";
                range.value = String(scale);
                range.style.cssText = "width:120px;";
                const out = document.createElement("span");
                out.textContent = `${scale.toFixed(1)}×`;
                range.addEventListener("input", () => {
                    scale = Number(range.value);
                    out.textContent = `${scale.toFixed(1)}×`;
                    applyInstances();
                    viewer.invalidate();
                });
                row("Scale", range);
                range.after(out);
            },
        });

        viewer.onTap = async (cx, cy) => {
            const r = await viewer.pick(cx, cy);
            picked = null as typeof picked; // (widened: `describe` below sets it)
            if (r) {
                const k = phase * scale;
                const nearest = (pos: Float32Array, d: Float32Array, cands: number[]) => {
                    let best = -1, bd = Infinity;
                    for (const v of cands) {
                        if (v < 0 || v * 3 + 2 >= pos.length) continue;
                        const dd = Math.hypot(
                            pos[v * 3] + d[v * 3] * k - r.world[0],
                            pos[v * 3 + 1] + d[v * 3 + 1] * k - r.world[1],
                            pos[v * 3 + 2] + d[v * 3 + 2] * k - r.world[2],
                        );
                        if (dd < bd) [best, bd] = [v, dd];
                    }
                    return best;
                };
                const describe = (pos: Float32Array, d: Float32Array, v: number, solid: boolean) => {
                    const dv: [number, number, number] = [d[v * 3], d[v * 3 + 1], d[v * 3 + 2]];
                    picked = {
                        node: v, solid, d: dv, mag: Math.hypot(...dv),
                        p: [pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]],
                    };
                };
                const t = r.triangle;
                if (r.instance === main.instance && main.indices && tris) {
                    const v = nearest(main.positions!, mode.disp, [main.indices[t * 3], main.indices[t * 3 + 1], main.indices[t * 3 + 2]]);
                    if (v >= 0) describe(main.positions!, mode.disp, v, false);
                } else if (memberLines && r.instance === memberLines.instance && lineEdges) {
                    const v = nearest(main.positions!, mode.disp, [lineEdges[t * 2], lineEdges[t * 2 + 1]]);
                    if (v >= 0) describe(main.positions!, mode.disp, v, false);
                } else if (solids && r.instance === solids.instances[0].instance && solids.indices) {
                    const ix = solids.indices;
                    const v = nearest(solids.base, solids.disp, [ix[t * 3], ix[t * 3 + 1], ix[t * 3 + 2]]);
                    if (v >= 0) describe(solids.base, solids.disp, v, true);
                }
                if (picked && !picked.solid) {
                    if (nodeLabels === undefined && manifest.mesh.node_labels_url) {
                        try {
                            const buf = await opts.fetcher(manifest.mesh.node_labels_url);
                            nodeLabels = JSON.parse(new TextDecoder().decode(buf)) as number[];
                        } catch {
                            nodeLabels = null;
                        }
                    }
                    const lab = nodeLabels?.[picked.node];
                    if (typeof lab === "number") picked.label = lab;
                }
                if (picked) toolbar.toggle("info", true);
            }
            toolbar.refresh("info");
        };
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

/** The colour legend, as the posters (and Studio's ColorLegend) draw it; `update` redraws it. */
function addLegend(host: HTMLElement) {
    const box = document.createElement("div");
    box.style.cssText =
        "position:absolute;top:8px;right:8px;background:rgba(30,41,59,0.92);color:#e2e8f0;border-radius:6px;" +
        "padding:6px 8px;font:10px/1.3 system-ui;pointer-events:none;max-width:45%;";
    host.appendChild(box);
    return {
        update(field: FeaManifestField, stepIndex: number, maxMag: number) {
            const path = (field.group_path || []).join(" / ") || field.name_canonical || "Displacement";
            const comps = field.components || [];
            const unit = comps.map((c, i) => (["X", "DX", "U1"].includes(c.toUpperCase()) ? field.component_units?.[i] : undefined)).find(Boolean);
            const step = (field.steps || []).find((s) => s.i === stepIndex) ?? (field.steps || [])[stepIndex];
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
        },
    };
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

interface BeamSolids {
    instances: MorphInstance[];
    bounds: [number, number, number, number, number, number];
    /** The solid's undeformed vertices, its index buffer and the current displacement (for picking). */
    base: Float32Array;
    indices: Uint32Array | undefined;
    disp: Float32Array;
    /** Warp the solids to a mode (displacement, rotation, colours); returns the largest displacement. */
    apply: (field: FeaManifestField, step: Float32Array) => number;
}

/** The bundle's beam solids added to the scene, or null when it has none or they do not fit their
 *  warp map (the model then draws its beams as lines). */
async function loadBeamSolids(
    viewer: Viewer,
    manifest: FeaManifest,
    fetcher: FeaArtefactFetcher,
    nodePositions: Float32Array,
): Promise<BeamSolids | null> {
    const glbUrl = manifest.mesh.beam_solids_url, warpUrl = manifest.mesh.beam_solids_warp_url;
    if (!glbUrl || !warpUrl) return null;
    const [glb, warpBuf] = await Promise.all([fetcher(glbUrl), fetcher(warpUrl)]);
    const warp = parseBeamSolidsWarp(warpBuf);
    const loaded = viewer.loadGlb(new Uint8Array(glb), {keepPositions: true, doubleSided: true});
    const parts = loaded.geometries.filter((g) => !g.featureEdges && g.topology === Topology.Triangles && g.positions);
    const total = parts.reduce((n, g) => n + g.vertexCount, 0);
    if (parts.length !== 1 || total !== warp.n_verts) {
        console.warn(`[ada-viewer-webgpu] beam solids: ${total} vertices in ${parts.length} parts, a warp map for ${warp.n_verts}; beams as lines`);
        for (const g of loaded.geometries) viewer.renderer.removeInstance(g.instance);
        return null;
    }
    const part = parts[0];
    const base = part.positions!;
    const n = warp.n_verts;
    const instances: MorphInstance[] = [{
        instance: part.instance, r: 1, g: 1, b: 1,
        flags: InstanceFlag.VertexColors | InstanceFlag.MorphRelative | InstanceFlag.DoubleSided | InstanceFlag.DepthBack,
        lineWidth: 1, surface: true,
    }];
    const out: BeamSolids = {instances, bounds: loaded.bounds, base, indices: part.indices, disp: new Float32Array(n * 3), apply: () => 0};

    // Element edges, per element as the viewer finds them, on the solid's vertices and morph.
    let edgeGeometry = 0;
    const index = part.indices;
    if (index && index.length) {
        let ranges: [number, number][] = [[0, index.length]];
        const elementsUrl = manifest.mesh.beam_solids_elements_url;
        if (elementsUrl) {
            try {
                ranges = parseMeshElements(await fetcher(elementsUrl)).map((e) => [e.triStart * 3, e.triCount * 3]);
            } catch (err) {
                console.warn("[ada-viewer-webgpu] beam-solid element ranges:", err);
            }
        }
        const pairs = beamSolidFeatureEdges(base, index, ranges);
        if (pairs.length) {
            const inst = viewer.heap([base, out.disp, pairs], ([pos, mor, idx]) => {
                edgeGeometry = viewer.renderer.addGeometry(Topology.Lines, pos, n, 0, 0, mor, idx, pairs.length, 0, 0, 0);
                return viewer.renderer.addInstance(edgeGeometry);
            });
            const [r, g, b] = FEA_EDGE_COLOR;
            instances.push({instance: inst, r, g, b, flags: InstanceFlag.Unlit | InstanceFlag.MorphRelative, lineWidth: 1});
        }
    }
    out.apply = (field, step) => {
        const disp = beamSolidDisplacement(warp, base, field, step, nodePositions);
        const mag = new Float32Array(n);
        let peak = 0;
        for (let v = 0; v < n; v++) {
            mag[v] = Math.hypot(disp[v * 3], disp[v * 3 + 1], disp[v * 3 + 2]);
            if (mag[v] > peak) peak = mag[v];
        }
        // Coloured by the solid's own displacement (translation and twist), as the viewer does: the
        // nodes' colours would be round-off noise in a torsion mode.
        const colors = packColors(mag, peak);
        viewer.heap([disp], ([p]) => {
            viewer.renderer.updateMorph(part.geometry, p, n);
            if (edgeGeometry) viewer.renderer.updateMorph(edgeGeometry, p, n);
        });
        viewer.heap([colors], ([p]) => viewer.renderer.updateColors(part.geometry, p, n));
        out.disp = disp;
        return peak;
    };
    return out;
}

function unionBounds(
    a: [number, number, number, number, number, number],
    b: [number, number, number, number, number, number],
): [number, number, number, number, number, number] {
    return [
        Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.min(a[2], b[2]),
        Math.max(a[3], b[3]), Math.max(a[4], b[4]), Math.max(a[5], b[5]),
    ];
}

/** Beams as solids / as lines. */
function addToggle(viewer: Viewer, initial: boolean, onChange: (solids: boolean) => void) {
    const btn = document.createElement("button");
    btn.type = "button";
    let on = initial;
    const label = () => {
        btn.textContent = on ? "Solids" : "Lines";
        btn.title = on ? "Beams drawn as their sections: click for lines" : "Beams drawn as lines: click for their sections";
    };
    label();
    btn.style.cssText =
        "position:absolute;left:44px;bottom:8px;height:28px;padding:0 10px;border-radius:14px;border:1px solid #cbd5e1;" +
        "background:#fff;color:#334155;font:12px system-ui;cursor:pointer;";
    btn.addEventListener("pointerdown", (e) => e.stopPropagation());
    btn.addEventListener("click", (e) => {
        e.stopPropagation();
        on = !on;
        label();
        onChange(on);
    });
    viewer.host.appendChild(btn);
}
