import React from "react";
import * as THREE from "three";

import {sceneRef, cameraRef, rendererRef, adaExtensionRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useModelState, loadedSourceGroups} from "@/state/modelState";
import type {MassGlyph, BcGlyph, LoadScenario} from "@/extensions/design_and_analysis_extension";

// Headless: reconciles the FEM-concepts store with three.js, drawing a glyph
// overlay (masses, and — later phases — boundary conditions + the selected load
// scenario's arrows) into a layer-1 group. Renders nothing itself. Mirrors
// SectionPlanesController.
const FemConceptsController: React.FC = () => {
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
// adaExtensionRef is only consulted while something is loaded (the
// streaming/replace path) — it still holds the LAST model's data after an
// unload, which used to leave dead masses/BCs/loads in the overlay.
function parseExtension(): {masses: MassGlyph[]; bcs: BcGlyph[]; scenarios: LoadScenario[]} {
    const masses: MassGlyph[] = [];
    const bcs: BcGlyph[] = [];
    const scenarios: LoadScenario[] = [];

    const ingest = (ext: any) => {
        if (!ext) return;
        const objs = [...(ext.design_objects ?? []), ...(ext.simulation_objects ?? [])];
        for (const o of objs) {
            const fc = o?.fem_concepts;
            if (!fc) continue;
            if (fc.masses) masses.push(...fc.masses);
            if (fc.bcs) bcs.push(...fc.bcs);
            if (fc.scenarios) scenarios.push(...fc.scenarios);
        }
    };

    const loadedNames = useModelState.getState().loadedSourceNames;
    let foundPerSource = false;
    for (const name of loadedNames) {
        const group = loadedSourceGroups.get(name);
        if (!group) continue;
        const ext = (group.children?.[0] as any)?.userData?.__adaExt
            ?? (group as any)?.userData?.__adaExt;
        if (ext) {
            ingest(ext);
            foundPerSource = true;
        }
    }
    if (!foundPerSource && loadedNames.size > 0) {
        ingest(adaExtensionRef.current as any);
    }
    return {masses, bcs, scenarios};
}

const MASS_COLOR = 0xffb300; // amber
const BC_COLOR = 0xff3b30; // red — restrained nodes
const LOAD_COLOR = 0x2ecc71; // green — applied loads

function init(scene: THREE.Scene): () => void {
    const container = new THREE.Group();
    container.name = "__fem_concepts__";
    container.userData.__excludeFromFit = true; // keep glyphs out of zoom-to-all
    scene.add(container);

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
        for (const m of masses) {
            // Volume-proportional radius so a 10x mass reads as ~2.15x radius, not
            // 10x (which would dwarf the model); floored so tiny masses stay visible.
            const frac = maxMass > 0 ? Math.cbrt((m.mass || 0) / maxMass) : 1;
            const r = glyph * (0.5 + 0.7 * frac);
            const mesh = new THREE.Mesh(
                new THREE.SphereGeometry(r, 16, 12),
                new THREE.MeshBasicMaterial({
                    color: MASS_COLOR,
                    transparent: true,
                    opacity: 0.85,
                    depthTest: false, // always visible, even inside the structure
                }),
            );
            mesh.renderOrder = 9990;
            mesh.position.set(m.position[0], m.position[1], m.position[2]);
            mesh.layers.set(1); // non-pickable overlay
            mesh.userData.__femConcept = {kind: "mass", name: m.name, mass: m.mass};
            container.add(mesh);
        }
    };

    // One instanced diamond (octahedron) per restrained node. Orientation-free
    // so it reads as a "fixed point" regardless of z-up; instanced because a BC
    // set (e.g. a pinned band) can carry many nodes.
    const addBcs = (bcs: BcGlyph[], glyph: number) => {
        for (const bc of bcs) {
            const pts = bc.positions ?? [];
            if (!pts.length) continue;
            const geo = new THREE.OctahedronGeometry(glyph * 0.7);
            const mat = new THREE.MeshBasicMaterial({
                color: BC_COLOR,
                transparent: true,
                opacity: 0.85,
                depthTest: false,
            });
            const inst = new THREE.InstancedMesh(geo, mat, pts.length);
            inst.renderOrder = 9991;
            inst.layers.set(1);
            const m = new THREE.Matrix4();
            pts.forEach((p, i) => {
                m.makeTranslation(p[0], p[1], p[2]);
                inst.setMatrixAt(i, m);
            });
            inst.instanceMatrix.needsUpdate = true;
            inst.userData.__femConcept = {kind: "bc", name: bc.name, dofs: bc.dofs};
            container.add(inst);
        }
    };

    // Make an overlay object always-visible (depthTest off, high renderOrder) and
    // non-pickable (layer 1) — handles ArrowHelper's line+cone children too.
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
    const addLoads = (scenario: LoadScenario, glyph: number) => {
        const loads = scenario.loads ?? [];
        if (!loads.length) return;
        const maxMag = Math.max(...loads.map((l) => Math.abs(l.magnitude ?? 0)), 1e-9);
        const lenFor = (l: {magnitude?: number}) =>
            glyph * (1.2 + 1.6 * (maxMag > 0 ? Math.abs(l.magnitude ?? 0) / maxMag : 1));

        for (const l of loads) {
            if (l.type === "surface") {
                const pts = (l.points ?? []).map((p) => new THREE.Vector3(p[0], p[1], p[2]));
                if (pts.length >= 2) {
                    const geo = new THREE.BufferGeometry().setFromPoints([...pts, pts[0]]);
                    const loop = new THREE.Line(geo, new THREE.LineBasicMaterial({color: LOAD_COLOR}));
                    styleOverlay(loop, 9992);
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
                const line = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({color: LOAD_COLOR}));
                styleOverlay(line, 9992);
                container.add(line);
                origin = origin.clone().add(e).multiplyScalar(0.5); // arrow at midpoint
            }
            const arrow = new THREE.ArrowHelper(dir, origin, len, LOAD_COLOR, len * 0.32, len * 0.2);
            styleOverlay(arrow, 9992);
            container.add(arrow);
        }
    };

    const rebuild = () => {
        if (!sceneRef.current) return;
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
        if (st.showMasses) addMasses(st.masses, glyph);
        if (st.showBcs) addBcs(st.bcs, glyph);
        const sc = st.selectedScenario;
        if (sc >= 0 && sc < st.scenarios.length) addLoads(st.scenarios[sc], glyph);
        requestRender();
    };

    // Re-read the extension when a model loads/changes, push into the store
    // (which triggers a rebuild via the store subscription below).
    const reparse = () => {
        useFemConceptsStore.getState().setData(parseExtension());
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
        unsubStore();
        unsubModel();
        disposeContainer();
        scene.remove(container);
        requestRender();
    };
}

export default FemConceptsController;
