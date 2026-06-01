import React from "react";
import * as THREE from "three";

import {sceneRef, cameraRef, rendererRef, adaExtensionRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useModelState} from "@/state/modelState";
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

// Merge the fem_concepts blocks across every design + simulation object in the
// loaded model's ADA_EXT extension into flat masses/bcs/scenarios arrays.
function parseExtension(): {masses: MassGlyph[]; bcs: BcGlyph[]; scenarios: LoadScenario[]} {
    const ext = adaExtensionRef.current as any;
    const masses: MassGlyph[] = [];
    const bcs: BcGlyph[] = [];
    const scenarios: LoadScenario[] = [];
    if (ext) {
        const objs = [...(ext.design_objects ?? []), ...(ext.simulation_objects ?? [])];
        for (const o of objs) {
            const fc = o?.fem_concepts;
            if (!fc) continue;
            if (fc.masses) masses.push(...fc.masses);
            if (fc.bcs) bcs.push(...fc.bcs);
            if (fc.scenarios) scenarios.push(...fc.scenarios);
        }
    }
    return {masses, bcs, scenarios};
}

const MASS_COLOR = 0xffb300; // amber

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

    const rebuild = () => {
        if (!sceneRef.current) return;
        disposeContainer();
        const st = useFemConceptsStore.getState();
        const glyph = glyphScale();
        if (st.showMasses) addMasses(st.masses, glyph);
        // Phase 2 (BCs) and Phase 3 (load scenarios) add their glyphs here.
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
        } else if (s.boundingBox !== prev.boundingBox) {
            rebuild(); // glyph scale follows the model bbox
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
