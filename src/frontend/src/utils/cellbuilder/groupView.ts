/**
 * The cellbuilder GROUPS in the 3D view: hide / show / isolate a group, and
 * colour by group -- on BOTH representations of the model:
 *
 * - the editable cell boxes (before a compile): hiding goes through the store's
 *   per-cell hide (`hideCells` / `unhideCells`, ephemeral view state), colouring
 *   through the box materials' base colour (`refreshFaceStyles` reads
 *   `cellGroupTint`);
 * - the compiled result (after a compile): a grouping engine builds one
 *   structure per group, named after it (`structureNodes.ts`), so the group's
 *   geometry is the draw ranges under that Scene-tree row. Hiding uses the Scene
 *   tree's own row actions (`hideRows` / `showRows`, undoable like the tree's eye);
 *   colouring uses the viewer's existing per-range recolour
 *   (`CustomBatchedMesh.setRangeColors`, reverted with
 *   `disableVertexColorsAndResetMaterial`, as the systems highlight does).
 *
 * Nothing here touches the procedural document: what is shown is a viewer
 * preference (`groupViewStore`). A recompile reloads the result with every
 * range visible and uncoloured, so `reapplyGroupViewToResult` puts the hidden
 * groups and the tint back once the new result is in the scene.
 */

import * as THREE from "three";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {coloursOf, useGroupViewStore} from "@/state/cellbuilder/groupViewStore";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {groupColor, objectGroup} from "@/utils/cellbuilder/groups";
import {findStructureNode, rootsForSource} from "@/utils/cellbuilder/structureNodes";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {recordViewerAction} from "@/utils/scene/viewerHistory";
import {hideRows, rangesUnder, showRows} from "@/utils/tree_view/sceneTreeActions";

// ── where things are ──────────────────────────────────────────────

/** The Scene-tree root rows of the open model's compiled result(s) in the scene. */
function resultRoots(): TreeNodeData[] {
    const cb = useCellBuilderStore.getState();
    const tree = useTreeViewStore.getState().treeData;
    return [cb.resultSourceName, cb.detailSourceName]
        .filter((s): s is string => !!s)
        .flatMap((src) => rootsForSource(tree, src));
}

/** The compiled structure rows of `names`, across every loaded result. */
function structureRows(names: readonly string[]): TreeNodeData[] {
    const out: TreeNodeData[] = [];
    for (const root of resultRoots()) {
        for (const name of names) {
            const n = findStructureNode(root, name);
            if (n) out.push(n);
        }
    }
    return out;
}

/** Does the loaded result contain group `name`'s structure? False before a
 * compile, and for a group the last compile left out. */
export function groupHasCompiledStructure(name: string): boolean {
    return structureRows([name]).length > 0;
}

/** Ids of the builder objects (cells, and the equipment / openings inside them)
 * belonging to group `name` -- or, with `name` undefined, to no group. */
function objectIdsOf(name: string | undefined): string[] {
    const cells = Object.values(useCellBuilderStore.getState().cells);
    return cells.filter((c) => objectGroup(c, cells) === name).map((c) => c.id);
}

// ── hide / show / isolate ─────────────────────────────────────────

export function setGroupHidden(name: string, hidden: boolean): void {
    const view = useGroupViewStore.getState();
    const rest = view.hiddenGroups.filter((g) => g !== name);
    view.setHiddenGroups(hidden ? [...rest, name] : rest);

    const cb = useCellBuilderStore.getState();
    const ids = objectIdsOf(name);
    if (hidden) cb.hideCells(ids);
    else cb.unhideCells(ids);

    const rows = structureRows([name]);
    if (rows.length) {
        if (hidden) hideRows(rows);
        else showRows(rows);
    }
}

/** Show every group again: all cells, and all of the compiled result. */
export function showAllGroups(): void {
    useGroupViewStore.getState().setHiddenGroups([]);
    useCellBuilderStore.getState().unhideAllCells();
    const roots = resultRoots();
    if (roots.length) showRows(roots);
}

/** Show group `name` only: every other group, and anything in no group, hides --
 * cells and compiled result alike. Other models in the scene are left alone. */
export function isolateGroup(name: string): void {
    const cb = useCellBuilderStore.getState();
    useGroupViewStore.getState().setHiddenGroups(cb.groups.map((g) => g.name).filter((g) => g !== name));

    const keepIds = new Set(objectIdsOf(name));
    cb.unhideCells([...keepIds]);
    cb.hideCells(Object.keys(cb.cells).filter((id) => !keepIds.has(id)));

    const roots = resultRoots();
    if (!roots.length) return;
    const keep = structureRows([name]);
    recordViewerAction("isolate", () => {
        for (const [mesh, ids] of rangesUnder(roots)) mesh.hideBatchDrawRange(ids);
        for (const [mesh, ids] of rangesUnder(keep)) mesh.unhideBatchDrawRange(ids);
        // As the tree's Hide: the selection overlay would keep drawing what was just hidden.
        useSelectedObjectStore.getState().clearSelectedObjects();
    });
    requestRender();
}

// ── colour by group ───────────────────────────────────────────────

/** The tint for a builder object under colour-by-group (`0xRRGGBB`), or null when
 * colour-by-group is off or the object is in no group. Read by the cell box
 * styling (`refreshFaceStyles`) in place of the kind colour. */
export function cellGroupTint(
    cell: {kind: string; group?: string; origin: readonly number[]; size: readonly number[]},
    cells: Iterable<{kind: string; group?: string; origin: readonly number[]; size: readonly number[]}>,
): number | null {
    const view = useGroupViewStore.getState();
    if (!view.colourByGroup) return null;
    const cb = useCellBuilderStore.getState();
    const hex = groupColor(cb.groups, objectGroup(cell, cells), coloursOf(view, cb.active?.modelId));
    return hex ? new THREE.Color(hex).getHex() : null;
}

/** Meshes this module tinted, with the material colour they had: vertex colours
 * multiply the material colour, so a tinted mesh draws with a white material and
 * gets its own colour back when the tint goes. The material is the mesh's own
 * clone (`CustomBatchedMesh.originalMaterial`), never a shared one. */
const tinted = new Map<CustomBatchedMesh, THREE.Color | null>();

function baseMaterial(mesh: CustomBatchedMesh): (THREE.Material & {color?: THREE.Color}) | null {
    const m = Array.isArray(mesh.material) ? mesh.material[0] : mesh.originalMaterial;
    return (m as THREE.Material & {color?: THREE.Color}) ?? null;
}

function untint(mesh: CustomBatchedMesh): void {
    const saved = tinted.get(mesh);
    tinted.delete(mesh);
    mesh.disableVertexColorsAndResetMaterial();
    const mat = baseMaterial(mesh);
    if (saved && mat?.color) mat.color.copy(saved);
}

/** Bring the compiled result's colours in line with colour-by-group: each
 * structure in its group's colour (anything in no group in neutral grey) when on,
 * the model's own materials when off. */
export function applyGroupColoursToResult(): void {
    for (const mesh of [...tinted.keys()]) {
        // A mesh of a result that has since been replaced is out of the scene: just forget it.
        if (!mesh.parent) tinted.delete(mesh);
        else untint(mesh);
    }
    const view = useGroupViewStore.getState();
    if (!view.colourByGroup) {
        requestRender();
        return;
    }
    const cb = useCellBuilderStore.getState();
    const overrides = coloursOf(view, cb.active?.modelId);
    for (const root of resultRoots()) {
        const perMesh = new Map<CustomBatchedMesh, Map<string, THREE.Color>>();
        for (const mesh of rangesUnder([root]).keys()) perMesh.set(mesh, new Map());
        for (const g of cb.groups) {
            const node = findStructureNode(root, g.name);
            const hex = groupColor(cb.groups, g.name, overrides);
            if (!node || !hex) continue;
            const col = new THREE.Color(hex);
            for (const [mesh, ids] of rangesUnder([node])) {
                const m = perMesh.get(mesh) ?? new Map<string, THREE.Color>();
                for (const id of ids) m.set(id, col);
                perMesh.set(mesh, m);
            }
        }
        for (const [mesh, colours] of perMesh) {
            // Geometry that carries its own vertex colours (an FEA field, another
            // highlight) is left alone: reverting the tint would delete them.
            if (mesh.geometry.getAttribute("color")) continue;
            const mat = baseMaterial(mesh);
            tinted.set(mesh, mat?.color ? mat.color.clone() : null);
            mat?.color?.set(0xffffff);
            mesh.setRangeColors(colours);
        }
    }
    requestRender();
}

/** After a (re)loaded result arrives: hide the hidden groups' structures again and
 * re-tint. Not recorded for undo -- it restores a state, it is not a user action. */
export function reapplyGroupViewToResult(): void {
    const hidden = useGroupViewStore.getState().hiddenGroups;
    if (hidden.length) {
        for (const [mesh, ids] of rangesUnder(structureRows(hidden))) mesh.hideBatchDrawRange(ids);
    }
    applyGroupColoursToResult();
}

// ── keep it in step ───────────────────────────────────────────────

let installed = false;

/** Re-tint when colour-by-group, a colour or the groups change, and forget the
 * hidden groups when another model is opened (its cells start shown). Idempotent. */
export function installGroupView(): void {
    if (installed) return;
    installed = true;
    useGroupViewStore.subscribe((s, prev) => {
        if (s.colourByGroup !== prev.colourByGroup || s.colours !== prev.colours) applyGroupColoursToResult();
    });
    useCellBuilderStore.subscribe((s, prev) => {
        if (s.active?.modelId !== prev.active?.modelId) useGroupViewStore.getState().setHiddenGroups([]);
        if (s.groups !== prev.groups && useGroupViewStore.getState().colourByGroup) applyGroupColoursToResult();
    });
}
