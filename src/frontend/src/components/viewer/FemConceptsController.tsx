import React from "react";

import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useModelState, loadedSourceGroups} from "@/state/modelState";
import {useSceneFrameStore} from "@/state/sceneFrameStore";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {openSimulationPanel} from "@/state/simPanelStore";
import {onSourceExtensions, sourceExtension} from "@/state/sourceExtensions";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {MODEL_TAB} from "@/components/simulation/simPanelTabs";
import {buildConceptGlyphs, glyphSize} from "@/utils/fem/conceptGlyphs";
import {setShownConcepts} from "@/utils/fem/conceptPicking";
import {refKey} from "@/utils/fem/femSelection";
import {visibleEquipment} from "@/utils/fem/femTakeoff";
import {followActiveViewport, onModelLoaded, type ViewportApi} from "@/viewport/api/viewportApi";
import type {
    MassGlyph,
    BcGlyph,
    ConstraintGlyph,
    EquipmentGlyph,
    LoadScenario,
} from "@/extensions/design_and_analysis_extension";

// Headless: reconciles the FEM-concepts store with whichever viewport is on screen, drawing the
// glyphs (masses, boundary conditions, equipment, the selected constraint's master/slave nodes,
// and the selected load scenario's arrows) as an overlay of that viewport, the selected concepts in
// the selection colour. Renders nothing itself. What is drawn is plain data
// (utils/fem/conceptGlyphs.ts), and a click is resolved against the same data
// (utils/fem/conceptPicking.ts), so both viewports show and pick the same thing.
const FemConceptsController: React.FC = () => {
    React.useEffect(() => followActiveViewport(init), []);
    return null;
};

// Merge the fem_concepts blocks across every design + simulation object of
// every LOADED model into flat masses/bcs/scenarios arrays. Each loaded model
// keeps its own ADA extension: on its scene group (userData.__adaExt) where the
// three.js loader read it, or by its source name (state/sourceExtensions) where
// a viewport without scene objects did. The runtime adaExtension is only
// consulted while something is loaded (the streaming/replace path) — it still
// holds the LAST model's data after an unload, which used to leave dead
// masses/BCs/loads in the overlay.
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
        const ext = (group?.children?.[0] as any)?.userData?.__adaExt
            ?? (group as any)?.userData?.__adaExt
            ?? sourceExtension(name);
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

// Sources already looked at, so loading a second model doesn't re-open the panel for the first.
// The page's, not a viewport's: the same models are on screen when another viewport takes over.
const seen = new Set<string>();

function init(viewport: ViewportApi): () => void {
    const layer = viewport.overlays("__fem_concepts__");

    const rebuild = () => {
        layer.clear();
        const st = useFemConceptsStore.getState();
        // Glyph positions are raw model coordinates (m.cog / node.p from the producer). A viewport
        // that recentres what it loads says by how much (state/sceneFrameStore), and the glyphs are
        // placed by the same offset, or they would float away from the recentred mesh.
        const offset = useSceneFrameStore.getState().offset;
        const moved = !!offset && (offset[0] !== 0 || offset[1] !== 0 || offset[2] !== 0);
        const matrix = moved ? [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, offset[0], offset[1], offset[2], 1] : null;
        const box = viewport.modelBounds();
        const glyph = glyphSize(box ? Math.hypot(box[3] - box[0], box[4] - box[1], box[5] - box[2]) : null);
        const shift = moved ? offset : [0, 0, 0];
        const shown = new Set(visibleEquipment(st.equipment, st.showEquipment, st.equipmentGroupOn));
        const sc = st.selectedScenario;
        const ci = st.selectedConstraint;
        const glyphs = buildConceptGlyphs({
            masses: st.showMasses ? st.masses : [],
            bcs: st.showBcs ? st.bcs : [],
            equipment: st.equipment.map((e, index) => ({e, index})).filter(({e}) => shown.has(e)),
            scenario: sc >= 0 && sc < st.scenarios.length ? {index: sc, scenario: st.scenarios[sc], showGenerated: st.showGeneratedLoads} : null,
            constraint: ci >= 0 && ci < st.constraints.length ? {index: ci, constraint: st.constraints[ci]} : null,
            selected: new Set(st.selection.map(refKey)),
            glyph,
            centre: box ? [(box[0] + box[3]) / 2 - shift[0], (box[1] + box[4]) / 2 - shift[1], (box[2] + box[5]) / 2 - shift[2]] : [0, 0, 0],
        });
        // Glyphs go with the part of the model a section plane cuts away.
        for (const m of glyphs.meshes) {
            layer.addMesh({positions: m.positions, indices: m.indices, colors: m.colors, opacity: m.opacity, onTop: m.onTop, clipped: true, matrix, fitted: false});
        }
        for (const l of glyphs.lines) {
            layer.addLines({segments: l.segments, colors: l.colors, widthPx: 1, onTop: l.onTop, clipped: true, matrix, fitted: false});
        }
        const any = glyphs.meshes.length > 0 || glyphs.lines.length > 0;
        setShownConcepts(viewport, any ? glyphs.picks : null, matrix);
    };

    // Re-read the extension when a model loads/changes, push into the store
    // (which triggers a rebuild via the store subscription below).
    const reparse = () => {
        const {withConcepts, ...data} = parseExtension();
        const loaded = useModelState.getState().loadedSourceNames;
        useFemConceptsStore.getState().setData(data);
        const arrived = [...loaded].filter((n) => !seen.has(n));
        for (const n of [...seen]) if (!loaded.has(n)) seen.delete(n);
        arrived.forEach((n) => seen.add(n));
        if (arrived.some((n) => withConcepts.has(n) && ANALYSIS_SOURCE.test(n))) {
            useFemConceptsStore.getState().showAll();
            // Other UI shells show the concepts under the Scene panel's `fem` mode.
            useSceneInfoStore.getState().setMode("fem");
            openSimulationPanel(MODEL_TAB);
        }
    };

    reparse();
    rebuild();

    const offs = [
        useFemConceptsStore.subscribe(rebuild),
        useModelState.subscribe((s, prev) => {
            if (s.loadedSourceNames !== prev.loadedSourceNames) {
                reparse();
                rebuild();
            } else if (s.boundingBox !== prev.boundingBox) {
                rebuild(); // the model's size is the glyphs' size
            }
        }),
        // Where the models stand in the world.
        useSceneFrameStore.subscribe(rebuild),
        // A viewport without scene objects: the model's extension arrives with its announcement,
        // and its box is known from then on.
        onSourceExtensions(() => {
            reparse();
            rebuild();
        }),
        onModelLoaded(rebuild),
    ];

    return () => {
        for (const off of offs) off();
        setShownConcepts(viewport, null);
        layer.dispose();
    };
}

export default FemConceptsController;
