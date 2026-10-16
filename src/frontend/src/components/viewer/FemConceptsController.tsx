import React from "react";
import * as THREE from "three";

import {useViewerRefs} from "@/state/AdaViewerContext";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {requestRender} from "@/state/perfStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useModelState, loadedSourceGroups} from "@/state/modelState";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {refKey, type FemConceptRef} from "@/utils/fem/femSelection";
import {visibleEquipment} from "@/utils/fem/femTakeoff";
import {clipWithModel} from "@/utils/scene/section_clipping";
import type {
    MassGlyph,
    BcGlyph,
    ConstraintGlyph,
    EquipmentGlyph,
    LoadScenario,
    Vec31,
} from "@/extensions/design_and_analysis_extension";

// Headless: reconciles the FEM-concepts store with three.js, drawing a glyph
// overlay (masses, boundary conditions, equipment, the selected constraint's
// master/slave nodes, and the selected load scenario's arrows) into a layer-1
// group, the selected concepts in the selection colour. Renders nothing itself.
// Mirrors SectionPlanesController. Every glyph carries the reference it draws, so
// `pickFemConcept` can tell the pointer handler what was clicked.
const FemConceptsController: React.FC = () => {
    // This viewer instance's handles, for the parts of the file that are inside
    // React. The module-level scene code below has no tree to read a context
    // from and goes through `getViewerRuntime()` instead.
    const {scene: sceneRef, camera: cameraRef, renderer: rendererRef} = useViewerRefs();
    React.useEffect(() => {
        let cleanup: (() => void) | null = null;
        let raf = 0;

        const tryInit = () => {
            const renderer = rendererRef.current;
            const scene = sceneRef.current;
            const camera = cameraRef.current;
            if (!renderer || !scene || !camera) {
                raf = requestAnimationFrame(tryInit); // wait for ThreeCanvas to set refs
                return;
            }
            cleanup = init(scene);
        };
        tryInit();

        return () => {
            cancelAnimationFrame(raf);
            cleanup?.();
        };
    }, []);

    return null;
};

// Merge the fem_concepts blocks across every design + simulation object of
// every LOADED model into flat masses/bcs/scenarios arrays. Each loaded
// scene group keeps its own ADA extension (userData.__adaExt); the single
// The runtime adaExtension is only consulted while something is loaded (the
// streaming/replace path) — it still holds the LAST model's data after an
// unload, which used to leave dead masses/BCs/loads in the overlay.
//
// `withConcepts` names the loaded sources whose own extension carried any concept, so a model
// built for analysis can be noticed as it arrives.
function parseExtension(): {
    masses: MassGlyph[];
    bcs: BcGlyph[];
    constraints: ConstraintGlyph[];
    scenarios: LoadScenario[];
    equipment: EquipmentGlyph[];
    withConcepts: Set<string>;
} {
    const masses: MassGlyph[] = [];
    const bcs: BcGlyph[] = [];
    const constraints: ConstraintGlyph[] = [];
    const scenarios: LoadScenario[] = [];
    const equipment: EquipmentGlyph[] = [];
    const withConcepts = new Set<string>();

    const ingest = (ext: any): boolean => {
        if (!ext) return false;
        let any = false;
        const objs = [...(ext.design_objects ?? []), ...(ext.simulation_objects ?? [])];
        for (const o of objs) {
            const fc = o?.fem_concepts;
            if (!fc) continue;
            any = true;
            if (fc.masses) masses.push(...fc.masses);
            if (fc.bcs) bcs.push(...fc.bcs);
            if (fc.constraints) constraints.push(...fc.constraints);
            if (fc.scenarios) scenarios.push(...fc.scenarios);
            if (fc.equipment) equipment.push(...fc.equipment);
        }
        return any;
    };

    const loadedNames = useModelState.getState().loadedSourceNames;
    let foundPerSource = false;
    for (const name of loadedNames) {
        const group = loadedSourceGroups.get(name);
        if (!group) continue;
        const ext = (group.children?.[0] as any)?.userData?.__adaExt
            ?? (group as any)?.userData?.__adaExt;
        if (ext) {
            if (ingest(ext)) withConcepts.add(name);
            foundPerSource = true;
        }
    }
    if (!foundPerSource && loadedNames.size > 0) {
        if (ingest(getViewerRuntime().adaExtension.current as any)) {
            for (const name of loadedNames) withConcepts.add(name);
        }
    }
    return {masses, bcs, constraints, scenarios, equipment, withConcepts};
}

// A GeniE concept model -- the one source kind that is an analysis model through and through,
// so the panel that shows its masses, supports, loads and equipment is the one to open.
const ANALYSIS_SOURCE = /\.(xml|gnx)$/i;

const MASS_COLOR = 0xffb300; // amber
const BC_COLOR = 0xff3b30; // red — restrained nodes
const LOAD_COLOR = 0x2ecc71; // green — applied loads
const MASTER_COLOR = 0x00b0ff; // blue — independent (master) nodes of a constraint
const SLAVE_COLOR = 0xd500f9; // magenta — dependent (slave) nodes
const EQUIPMENT_COLOR = 0x26c6da; // teal — placed equipment
// The swatches in FemConceptsPanel's constraint info panel mirror these two.
const SELECTED_COLOR = 0xfff176; // pale yellow — what the Selected Object Info panel describes

// The overlay this viewer draws, for `pickFemConcept`: the pointer handler runs outside React and
// asks here, as it asks the clash markers for theirs.
let activeOverlay: {container: THREE.Group; glyph: number} | null = null;

/** A drawn object's concept: one reference, or one per instance of an instanced glyph. */
type Tag = {ref: FemConceptRef} | {refs: FemConceptRef[]};

export interface FemConceptPick {
    ref: FemConceptRef;
    distance: number;
    point: THREE.Vector3;
    /** An equipment box: a volume, which wins a click only when nothing solid is in front of it. */
    solid: boolean;
}

/**
 * The FEM concept under the cursor, or null. Its own raycaster on layer 1, where the glyphs are
 * drawn so the scene's picker never takes them by accident. The point glyphs are drawn over
 * everything (no depth test), so the nearest of those wins outright; an equipment box is
 * returned only when no point glyph was hit, flagged `solid` so the caller can weigh it against
 * the geometry in front of it.
 */
export function pickFemConcept(
    e: {clientX: number; clientY: number},
    camera: THREE.Camera,
    renderer: THREE.WebGLRenderer,
): FemConceptPick | null {
    const overlay = activeOverlay;
    if (!overlay || overlay.container.children.length === 0) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    const ray = new THREE.Raycaster();
    ray.layers.set(1);
    // Lines (load arrows, line loads, surface outlines) are hit within a fraction of a glyph.
    ray.params.Line = {threshold: overlay.glyph * 0.25};
    ray.setFromCamera(
        new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1),
        camera,
    );
    let solid: FemConceptPick | null = null;
    for (const hit of ray.intersectObjects(overlay.container.children, true)) {
        let o: THREE.Object3D | null = hit.object;
        let tag: Tag | undefined;
        while (o && o !== overlay.container && !(tag = o.userData.__femTag)) o = o.parent;
        if (!tag) continue;
        const ref = "ref" in tag ? tag.ref : hit.instanceId != null ? tag.refs[hit.instanceId] : undefined;
        if (!ref) continue;
        const pick = {ref, distance: hit.distance, point: hit.point.clone(), solid: ref.kind === "equipment"};
        if (!pick.solid) return pick;
        solid ??= pick;
    }
    return solid;
}

function tag(o: THREE.Object3D, t: Tag): void {
    o.userData.__femTag = t;
}

function init(scene: THREE.Scene): () => void {
    const container = new THREE.Group();
    container.name = "__fem_concepts__";
    container.userData.__excludeFromFit = true; // keep glyphs out of zoom-to-all
    scene.add(container);
    const overlay = {container, glyph: 1};
    activeOverlay = overlay;
    // Keys of the selected references, for the drawing below to colour what is selected.
    let selected = new Set<string>();
    const isSelected = (r: FemConceptRef) => selected.has(refKey(r));

    const disposeContainer = () => {
        for (let i = container.children.length - 1; i >= 0; i--) {
            const o = container.children[i];
            o.traverse((m: any) => {
                m.geometry?.dispose?.();
                if (m.material) {
                    const mm = m.material;
                    (Array.isArray(mm) ? mm : [mm]).forEach((x: any) => x.dispose?.());
                }
            });
            container.remove(o);
        }
    };

    const glyphScale = (): number => {
        const bb = useModelState.getState().boundingBox;
        const diag = bb ? bb.getSize(new THREE.Vector3()).length() : 50;
        return diag * 0.012;
    };

    const addMasses = (masses: MassGlyph[], glyph: number) => {
        if (!masses.length) return;
        const maxMass = Math.max(...masses.map((m) => m.mass || 0), 1e-9);
        masses.forEach((m, index) => {
            const ref: FemConceptRef = {kind: "mass", index};
            const on = isSelected(ref);
            // Volume-proportional radius so a 10x mass reads as ~2.15x radius, not
            // 10x (which would dwarf the model); floored so tiny masses stay visible.
            const frac = maxMass > 0 ? Math.cbrt((m.mass || 0) / maxMass) : 1;
            const r = glyph * (0.5 + 0.7 * frac) * (on ? 1.25 : 1);
            const mesh = new THREE.Mesh(
                new THREE.SphereGeometry(r, 16, 12),
                new THREE.MeshBasicMaterial({
                    color: on ? SELECTED_COLOR : MASS_COLOR,
                    transparent: true,
                    opacity: on ? 0.95 : 0.85,
                    depthTest: false, // always visible, even inside the structure
                }),
            );
            mesh.renderOrder = on ? 9995 : 9990;
            mesh.position.set(m.position[0], m.position[1], m.position[2]);
            mesh.layers.set(1); // the scene's picker skips layer 1; pickFemConcept asks it
            tag(mesh, {ref});
            container.add(mesh);
        });
    };

    // One instanced diamond (octahedron) per restrained node. Orientation-free
    // so it reads as a "fixed point" regardless of z-up; instanced because a BC
    // set (e.g. a pinned band) can carry many nodes.
    // A selected node is coloured per instance (white material, so the instance colour is the colour).
    const addBcs = (bcs: BcGlyph[], glyph: number) => {
        bcs.forEach((bc, index) => {
            const pts = bc.positions ?? [];
            if (!pts.length) return;
            const geo = new THREE.OctahedronGeometry(glyph * 0.7);
            const mat = new THREE.MeshBasicMaterial({
                color: 0xffffff,
                transparent: true,
                opacity: 0.85,
                depthTest: false,
            });
            const inst = new THREE.InstancedMesh(geo, mat, pts.length);
            inst.renderOrder = 9991;
            inst.layers.set(1);
            const whole = isSelected({kind: "bc", index});
            const m = new THREE.Matrix4();
            const base = new THREE.Color(BC_COLOR);
            const hot = new THREE.Color(SELECTED_COLOR);
            const refs: FemConceptRef[] = [];
            pts.forEach((p, node) => {
                m.makeTranslation(p[0], p[1], p[2]);
                inst.setMatrixAt(node, m);
                const ref: FemConceptRef = {kind: "bc", index, node};
                inst.setColorAt(node, whole || isSelected(ref) ? hot : base);
                refs.push(ref);
            });
            inst.instanceMatrix.needsUpdate = true;
            if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
            tag(inst, {refs});
            container.add(inst);
        });
    };

    // Make an overlay object always-visible (depthTest off, high renderOrder) and
    // out of the scene picker's reach (layer 1; pickFemConcept asks it) — handles
    // ArrowHelper's line+cone children too.
    const styleOverlay = (o: THREE.Object3D, order: number) => {
        o.traverse((c: any) => {
            c.layers?.set(1);
            c.renderOrder = order;
            if (c.material) {
                const mats = Array.isArray(c.material) ? c.material : [c.material];
                mats.forEach((m: any) => {
                    m.depthTest = false;
                    m.transparent = true;
                });
            }
        });
        o.renderOrder = order;
    };

    // Arrows for the selected load scenario: point/accel = a single arrow along
    // the force direction; line = a line + arrow at its midpoint; surface = the
    // loaded polygon outline. Arrow length scales with the load's magnitude
    // relative to the scenario's max (fixed length would hide tiny loads;
    // pure-linear would dwarf the model).
    const addLoads = (scenarioIndex: number, scenario: LoadScenario, showGenerated: boolean, glyph: number) => {
        // Indexed into the scenario's full list, which is what a selection refers to.
        const loads = (scenario.loads ?? [])
            .map((l, index) => ({l, index}))
            .filter(({l}) => showGenerated || !l.generated_by);
        if (!loads.length) return;
        const maxMag = Math.max(...loads.map(({l}) => Math.abs(l.magnitude ?? 0)), 1e-9);
        const lenFor = (l: {magnitude?: number}) =>
            glyph * (1.2 + 1.6 * (maxMag > 0 ? Math.abs(l.magnitude ?? 0) / maxMag : 1));

        for (const {l, index} of loads) {
            const ref: FemConceptRef = {kind: "load", scenario: scenarioIndex, index};
            const on = isSelected(ref);
            const color = on ? SELECTED_COLOR : LOAD_COLOR;
            const order = on ? 9996 : 9992;
            if (l.type === "surface") {
                const pts = (l.points ?? []).map((p) => new THREE.Vector3(p[0], p[1], p[2]));
                if (pts.length >= 2) {
                    const geo = new THREE.BufferGeometry().setFromPoints([...pts, pts[0]]);
                    const loop = new THREE.Line(geo, new THREE.LineBasicMaterial({color}));
                    styleOverlay(loop, order);
                    tag(loop, {ref});
                    container.add(loop);
                }
                continue;
            }
            const dir = l.direction
                ? new THREE.Vector3(l.direction[0], l.direction[1], l.direction[2]).normalize()
                : new THREE.Vector3(0, 0, -1);
            const len = lenFor(l);
            let origin: THREE.Vector3;
            if (l.type === "accel") {
                const bb = useModelState.getState().boundingBox;
                origin = bb ? bb.getCenter(new THREE.Vector3()) : new THREE.Vector3();
            } else {
                const p = l.position ?? [0, 0, 0];
                origin = new THREE.Vector3(p[0], p[1], p[2]);
            }
            if (l.type === "line" && l.end_position) {
                const e = new THREE.Vector3(l.end_position[0], l.end_position[1], l.end_position[2]);
                const lineGeo = new THREE.BufferGeometry().setFromPoints([origin.clone(), e]);
                const line = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({color}));
                styleOverlay(line, order);
                tag(line, {ref});
                container.add(line);
                origin = origin.clone().add(e).multiplyScalar(0.5); // arrow at midpoint
            }
            const arrow = new THREE.ArrowHelper(dir, origin, len, color, len * 0.32, len * 0.2);
            styleOverlay(arrow, order);
            tag(arrow, {ref});
            container.add(arrow);
        }
    };

    // The selected constraint's two sides, drawn as instanced glyphs in different
    // colours: masters as larger spheres (there are usually far fewer of them, and
    // they're the side that drives the relation), slaves as smaller boxes. Instanced
    // because a constraint over an interface can carry thousands of nodes.
    const addConstraintSide = (index: number, pts: Vec31[], color: number, radius: number, master: boolean) => {
        if (!pts.length) return;
        const geo = master
            ? new THREE.SphereGeometry(radius, 10, 8)
            : new THREE.BoxGeometry(radius * 1.5, radius * 1.5, radius * 1.5);
        const mat = new THREE.MeshBasicMaterial({
            color: 0xffffff, // the instance colour is the colour
            transparent: true,
            opacity: 0.9,
            depthTest: false, // always visible, even inside the structure
        });
        const inst = new THREE.InstancedMesh(geo, mat, pts.length);
        inst.renderOrder = 9993;
        inst.layers.set(1); // the scene's picker skips layer 1; pickFemConcept asks it
        const side = master ? "master" : "slave";
        const m = new THREE.Matrix4();
        const base = new THREE.Color(color);
        const hot = new THREE.Color(SELECTED_COLOR);
        const refs: FemConceptRef[] = [];
        pts.forEach((p, node) => {
            m.makeTranslation(p[0], p[1], p[2]);
            inst.setMatrixAt(node, m);
            const ref: FemConceptRef = {kind: "constraint", index, side, node};
            inst.setColorAt(node, isSelected(ref) || isSelected({kind: "constraint", index, side}) ? hot : base);
            refs.push(ref);
        });
        inst.instanceMatrix.needsUpdate = true;
        if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
        tag(inst, {refs});
        container.add(inst);
    };

    // One translucent box per placed equipment, turned by its placed frame, with its edges drawn
    // so a box seen edge-on still reads. Unlike the point glyphs these keep the depth test: they
    // are volumes, and a volume drawn through the structure would hide what it stands on.
    const addEquipment = (items: {e: EquipmentGlyph; index: number}[]) => {
        for (const {e, index} of items) {
            const ref: FemConceptRef = {kind: "equipment", index};
            const on = isSelected(ref);
            const color = on ? SELECTED_COLOR : EQUIPMENT_COLOR;
            const lo = new THREE.Vector3(e.box_min[0], e.box_min[1], e.box_min[2]);
            const hi = new THREE.Vector3(e.box_max[0], e.box_max[1], e.box_max[2]);
            const size = hi.clone().sub(lo);
            if (!(size.x > 0 && size.y > 0 && size.z > 0)) continue;
            const geo = new THREE.BoxGeometry(size.x, size.y, size.z);
            const frame = new THREE.Matrix4().makeBasis(
                new THREE.Vector3(e.axes[0][0], e.axes[0][1], e.axes[0][2]),
                new THREE.Vector3(e.axes[1][0], e.axes[1][1], e.axes[1][2]),
                new THREE.Vector3(e.axes[2][0], e.axes[2][1], e.axes[2][2]),
            );
            frame.setPosition(e.origin[0], e.origin[1], e.origin[2]);
            const centre = lo.clone().add(hi).multiplyScalar(0.5);
            const placement = frame.multiply(new THREE.Matrix4().makeTranslation(centre.x, centre.y, centre.z));

            const box = new THREE.Mesh(
                geo,
                new THREE.MeshBasicMaterial({color, transparent: true, opacity: on ? 0.4 : 0.22, depthWrite: false}),
            );
            box.applyMatrix4(placement);
            box.renderOrder = 9989;
            box.layers.set(1);
            tag(box, {ref});
            container.add(box);

            // The edges are drawn, not picked: a box is clicked by its faces.
            const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({color}));
            edges.applyMatrix4(placement);
            edges.renderOrder = 9989;
            edges.layers.set(1);
            edges.raycast = () => {};
            container.add(edges);
        }
    };

    const addConstraint = (index: number, c: ConstraintGlyph, glyph: number) => {
        addConstraintSide(index, c.master_positions ?? [], MASTER_COLOR, glyph * 0.5, true);
        addConstraintSide(index, c.slave_positions ?? [], SLAVE_COLOR, glyph * 0.28, false);
    };

    const rebuild = () => {
        if (!getViewerRuntime().scene.current) return;
        disposeContainer();
        // Glyph positions are raw model coordinates (m.cog / node.p from
        // the producer). setupModelLoader recenters the loaded model by
        // ``modelStore.translation`` (gltf_scene.position += translation),
        // but this container is a direct scene child at the origin — so
        // without mirroring that offset the masses/BCs/loads float away
        // from the recentered mesh. Copy the model translation onto the
        // container so the overlay tracks the geometry.
        const translation = useModelState.getState().translation;
        if (translation) container.position.copy(translation);
        else container.position.set(0, 0, 0);
        const st = useFemConceptsStore.getState();
        const glyph = glyphScale();
        overlay.glyph = glyph;
        selected = new Set(st.selection.map(refKey));
        if (st.showMasses) addMasses(st.masses, glyph);
        if (st.showBcs) addBcs(st.bcs, glyph);
        const shown = new Set(visibleEquipment(st.equipment, st.showEquipment, st.equipmentGroupOn));
        addEquipment(st.equipment.map((e, index) => ({e, index})).filter(({e}) => shown.has(e)));
        const sc = st.selectedScenario;
        if (sc >= 0 && sc < st.scenarios.length) addLoads(sc, st.scenarios[sc], st.showGeneratedLoads, glyph);
        const ci = st.selectedConstraint;
        if (ci >= 0 && ci < st.constraints.length) addConstraint(ci, st.constraints[ci], glyph);
        // Glyphs go with the part of the model a section plane cuts away. Rebuilt
        // on their own store, so seeded here rather than left to the next plane edit.
        clipWithModel(container);
        requestRender();
    };

    // Re-read the extension when a model loads/changes, push into the store
    // (which triggers a rebuild via the store subscription below).
    // Sources already looked at, so loading a second model doesn't re-open the panel for the first.
    const seen = new Set<string>();
    const reparse = () => {
        const {withConcepts, ...data} = parseExtension();
        useFemConceptsStore.getState().setData(data);
        const loaded = useModelState.getState().loadedSourceNames;
        const arrived = [...loaded].filter((n) => !seen.has(n));
        for (const n of [...seen]) if (!loaded.has(n)) seen.delete(n);
        arrived.forEach((n) => seen.add(n));
        if (arrived.some((n) => withConcepts.has(n) && ANALYSIS_SOURCE.test(n))) {
            useFemConceptsStore.getState().showAll();
            const scene = useSceneInfoStore.getState();
            scene.setMode("fem");
            scene.setShowSceneInfoBox(true);
        }
    };

    reparse();
    rebuild();

    const unsubStore = useFemConceptsStore.subscribe(rebuild);
    const unsubModel = useModelState.subscribe((s, prev) => {
        if (s.loadedSourceNames !== prev.loadedSourceNames) {
            reparse();
            rebuild();
        } else if (
            s.boundingBox !== prev.boundingBox ||
            s.translation !== prev.translation
        ) {
            // bbox → glyph scale; translation → overlay recenter offset.
            rebuild();
        }
    });

    return () => {
        if (activeOverlay === overlay) activeOverlay = null;
        unsubStore();
        unsubModel();
        disposeContainer();
        scene.remove(container);
        requestRender();
    };
}

export default FemConceptsController;
