/**
 * Keyboard NUMERIC ENTRY: extrude, loft edits and numeric placement.
 *
 * Owns: the on-canvas readout and live ring preview, the typed-buffer parsing,
 * and the two entry state machines — the extrude/loft-edit entry (E/S) and the
 * numeric add-mode placement (type X, Y, Z and drop the box). The store is
 * mutated only on commit.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {stationRingPoints} from "@/utils/cellbuilder/loft";
import {extrudeBox, faceCenter, quantize, type CellBox, type Vec3} from "@/utils/cellbuilder/snap";
import {GHOST_COLOR, OPENING_COLOR, addModeSize} from "./sceneConstants";
import type {PlaceEntry} from "./sceneTypes";
import {offsetVec, type CellBuilderScene} from "./sceneContext";
import {setLoftActive} from "./insertTools";

// --- Keyboard interactive extrude + numeric entry -------------------------
// A live preview (the reused green `ghost` mesh + an on-canvas readout)
// driven purely from the keyboard: E starts it, digits/`.`/`-` type the
// depth, Enter commits (store mutates only here), Esc cancels. Three shapes:
// extruding a box cell from its selected face, extending a loft stack up its
// spine, and resizing a loft station's section — all share the typing UX.
export const drawReadout = (ctx: CellBuilderScene, text: string) => {
    const g = ctx.readoutCanvas.getContext("2d")!;
    g.clearRect(0, 0, 256, 64);
    g.fillStyle = "rgba(17,24,39,0.86)";
    const r = 12;
    g.beginPath();
    g.moveTo(r, 2);
    g.arcTo(254, 2, 254, 62, r);
    g.arcTo(254, 62, 2, 62, r);
    g.arcTo(2, 62, 2, 2, r);
    g.arcTo(2, 2, 254, 2, r);
    g.fill();
    g.fillStyle = "#22c55e";
    g.font = "bold 34px ui-monospace, monospace";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, 128, 34);
    ctx.readoutTex.needsUpdate = true;
};
export const showReadout = (ctx: CellBuilderScene, text: string, modelPos: Vec3) => {
    drawReadout(ctx, text);
    const off = offsetVec(ctx);
    ctx.readout.position.set(modelPos[0] + off.x, modelPos[1] + off.y, modelPos[2] + off.z);
    ctx.readout.visible = true;
};
export const hideReadout = (ctx: CellBuilderScene) => {
    if (ctx.readout.visible) ctx.readout.visible = false;
};
export const fmt = (ctx: CellBuilderScene, v: number): string => `${Math.round(v * 1000) / 1000}`;

export const showRingPreview = (ctx: CellBuilderScene, pts: Vec3[]) => {
    if (pts.length < 2) {
        ctx.ringPreview.visible = false;
        return;
    }
    const arr = new Float32Array(pts.length * 3);
    for (let i = 0; i < pts.length; i++) {
        arr[i * 3] = pts[i][0];
        arr[i * 3 + 1] = pts[i][1];
        arr[i * 3 + 2] = pts[i][2];
    }
    ctx.ringPreview.geometry.setAttribute("position", new THREE.BufferAttribute(arr, 3));
    ctx.ringPreview.geometry.computeBoundingSphere();
    ctx.ringPreview.visible = true;
};
export const hideRingPreview = (ctx: CellBuilderScene) => {
    if (ctx.ringPreview.visible) ctx.ringPreview.visible = false;
};

// Parse the typed buffer to a number, falling back to `def` for the empty /
// partial ("", "-", ".") states; a lone "-" flips the default's sign.
export const parseTyped = (ctx: CellBuilderScene, typed: string, def: number): number => {
    if (typed === "" || typed === "." || typed === "-.") return def;
    if (typed === "-") return -def;
    const v = Number(typed);
    return Number.isFinite(v) ? v : def;
};

export const loftMemberByName = (ctx: CellBuilderScene, name: string) =>
    useCellBuilderStore.getState().loftMembers.find((m) => m.NAME === name) ?? null;

// Bounds of two rings (for the loft-extend ghost box).
export const ringsBounds = (ctx: CellBuilderScene, lo: Vec3[], hi: Vec3[]): CellBox => {
    const min: Vec3 = [Infinity, Infinity, Infinity];
    const max: Vec3 = [-Infinity, -Infinity, -Infinity];
    for (const p of [...lo, ...hi]) {
        for (let a = 0; a < 3; a++) {
            if (p[a] < min[a]) min[a] = p[a];
            if (p[a] > max[a]) max[a] = p[a];
        }
    }
    return {origin: min, size: [max[0] - min[0], max[1] - min[1], max[2] - min[2]]};
};

export const setGhostBox = (ctx: CellBuilderScene, box: CellBox, color: number = GHOST_COLOR) => {
    // Reuses the single GHOST box mesh (also used by add-mode placement; the
    // modes never overlap). `color` tints it per use — green for additive
    // (cell / extrude / loft), red for an opening (a negative-volume cut).
    (ctx.ghost.material as THREE.MeshBasicMaterial).color.setHex(color);
    ctx.ghost.scale.set(Math.max(box.size[0], 1e-3), Math.max(box.size[1], 1e-3), Math.max(box.size[2], 1e-3));
    ctx.ghost.position.set(
        box.origin[0] + box.size[0] / 2,
        box.origin[1] + box.size[1] / 2,
        box.origin[2] + box.size[2] / 2,
    );
    ctx.ghost.visible = true;
};

export const refreshNumEntry = (ctx: CellBuilderScene) => {
    if (!ctx.numEntry) return;
    const st = useCellBuilderStore.getState();
    if (ctx.numEntry.kind === "cellExtrude") {
        const cell = st.cells[ctx.numEntry.cellId];
        if (!cell) return endNumEntry(ctx, true);
        const v = parseTyped(ctx, ctx.numEntry.typed, ctx.numEntry.defaultDepth);
        const box = extrudeBox(cell, ctx.numEntry.faceIndex, v);
        if (Math.abs(box.size[ctx.numEntry.axis]) < 1e-6) ctx.ghost.visible = false;
        else setGhostBox(ctx, box);
        showReadout(ctx, `${fmt(ctx, v)} m`, faceCenter(cell, ctx.numEntry.faceIndex));
        st.setToolHint(`Extrude ${fmt(ctx, v)} m — type depth, ↵ commit, Esc cancel`);
    } else if (ctx.numEntry.kind === "loftExtend") {
        const member = loftMemberByName(ctx, ctx.numEntry.memberName);
        if (!member || !member.STATIONS.length) return endNumEntry(ctx, true);
        const v = parseTyped(ctx, ctx.numEntry.typed, ctx.numEntry.defaultSpacing);
        const top = member.STATIONS[member.STATIONS.length - 1];
        const lo = stationRingPoints(top, member.PLACEMENT);
        const hi = stationRingPoints({...top, Z: Number(top.Z) + v}, member.PLACEMENT);
        const box = ringsBounds(ctx, lo, hi);
        if (Math.abs(box.size[2]) < 1e-6) ctx.ghost.visible = false;
        else setGhostBox(ctx, box);
        showReadout(ctx, `↑ ${fmt(ctx, v)} m`, ctx.numEntry.anchor);
        st.setToolHint(`Loft +${fmt(ctx, v)} m — type spacing, ↵ commit, Esc cancel`);
    } else {
        const v = parseTyped(ctx, ctx.numEntry.typed, ctx.numEntry.defaultVal);
        ctx.ghost.visible = false; // the ring outline is the preview here
        // Redraw the station's ring at the new section size (circle → RADIUS,
        // rectangle → WIDTH=HEIGHT, matching resizeLoftStation) so it scales live.
        const member = loftMemberByName(ctx, ctx.numEntry.memberName);
        const station = member?.STATIONS?.[ctx.numEntry.stationIndex];
        if (member && station) {
            const resized =
                ctx.numEntry.section === "circle"
                    ? {...station, RADIUS: v}
                    : {...station, WIDTH: v, HEIGHT: v};
            showRingPreview(ctx, stationRingPoints(resized, member.PLACEMENT));
        } else {
            hideRingPreview(ctx);
        }
        showReadout(ctx, `${ctx.numEntry.section === "circle" ? "r" : "□"} ${fmt(ctx, v)} m`, ctx.numEntry.anchor);
        st.setToolHint(
            `Section ${ctx.numEntry.section === "circle" ? "r" : "□"} ${fmt(ctx, v)} m — type size, ↵ commit, Esc cancel`,
        );
    }
    requestRender();
};

export const commitNumEntry = (ctx: CellBuilderScene) => {
    if (!ctx.numEntry) return;
    const st = useCellBuilderStore.getState();
    const entry = ctx.numEntry;
    if (entry.kind === "cellExtrude") {
        const v = parseTyped(ctx, entry.typed, entry.defaultDepth);
        if (Math.abs(v) > 1e-6) st.extendCellFromFace(entry.cellId, entry.faceIndex, v);
    } else if (entry.kind === "loftExtend") {
        const v = parseTyped(ctx, entry.typed, entry.defaultSpacing);
        if (Math.abs(v) > 1e-6) {
            st.extendLoftStack(entry.memberName, v);
            const m = loftMemberByName(ctx, entry.memberName);
            if (m) setLoftActive(ctx, entry.memberName, m.STATIONS.length - 1);
        }
    } else {
        const v = parseTyped(ctx, entry.typed, entry.defaultVal);
        st.resizeLoftStation(entry.memberName, entry.stationIndex, Math.max(0, v));
    }
    endNumEntry(ctx, false);
};

export const endNumEntry = (ctx: CellBuilderScene, _cancel: boolean) => {
    ctx.numEntry = null;
    ctx.ghost.visible = false;
    ctx.ghostBox = null;
    hideReadout(ctx);
    hideRingPreview(ctx);
    useCellBuilderStore.getState().setToolHint(null);
    requestRender();
};

// Numeric placement for add-cell / add-opening / add-equipment: type X, `,`
// to the next axis, Y, `,`, Z, Enter to drop the box at exactly (x,y,z) —
// no pointer needed. `.` is the decimal, `,` steps axes. Unentered axes
// default to 0 / the model ground (z). Lazily created on the first digit
// while in an add mode; the pointer ghost is suspended while it's live.
export const placeOrigin = (ctx: CellBuilderScene, pe: PlaceEntry): Vec3 => {
    const cells = Object.values(useCellBuilderStore.getState().cells);
    const groundZ = cells.length ? Math.min(...cells.map((c) => c.origin[2])) : 0;
    const def: Vec3 = [0, 0, groundZ];
    const val = (a: 0 | 1 | 2): number => {
        if (pe.vals[a] != null) return pe.vals[a] as number;
        if (a === pe.axis && pe.typed !== "") return parseTyped(ctx, pe.typed, def[a]);
        return def[a];
    };
    return [val(0), val(1), val(2)];
};
export const refreshPlaceEntry = (ctx: CellBuilderScene) => {
    if (!ctx.placeEntry) return;
    const st = useCellBuilderStore.getState();
    const size = addModeSize(st);
    const o = placeOrigin(ctx, ctx.placeEntry);
    const origin: Vec3 = [
        quantize(o[0], st.gridStep),
        quantize(o[1], st.gridStep),
        quantize(o[2], st.gridStep),
    ];
    setGhostBox(ctx, {origin, size}, st.mode === "add-opening" ? OPENING_COLOR : GHOST_COLOR);
    const AX = ["x", "y", "z"];
    showReadout(ctx,
        `${AX[ctx.placeEntry.axis]} ${fmt(ctx, o[ctx.placeEntry.axis])}`,
        [origin[0] + size[0] / 2, origin[1] + size[1] / 2, origin[2] + size[2] / 2],
    );
    st.setToolHint(
        `Place @ (${fmt(ctx, o[0])}, ${fmt(ctx, o[1])}, ${fmt(ctx, o[2])}) — type, "," next axis, ↵ place, Esc cancel`,
    );
    requestRender();
};
export const endPlaceEntry = (ctx: CellBuilderScene) => {
    ctx.placeEntry = null;
    ctx.ghost.visible = false;
    ctx.ghostBox = null;
    hideReadout(ctx);
    useCellBuilderStore.getState().setToolHint(null);
    requestRender();
};
export const commitPlaceEntry = (ctx: CellBuilderScene) => {
    if (!ctx.placeEntry) return;
    const st = useCellBuilderStore.getState();
    if (ctx.placeEntry.typed !== "") ctx.placeEntry.vals[ctx.placeEntry.axis] = parseTyped(ctx, ctx.placeEntry.typed, 0);
    const size = addModeSize(st);
    const o = placeOrigin(ctx, ctx.placeEntry);
    const origin: Vec3 = [
        quantize(o[0], st.gridStep),
        quantize(o[1], st.gridStep),
        quantize(o[2], st.gridStep),
    ];
    const kind =
        st.mode === "add-opening" ? "opening" : st.mode === "add-equipment" ? "equipment" : "cell";
    st.addCell(kind, origin, size); // sets mode idle + selects the new cell
    endPlaceEntry(ctx);
};

// --- Keyboard equipment insert (I) ---------------------------------------
// Two phases: "pick" chooses the equipment TYPE (T cycles) and the host CELL
// (N/P cycle, highlighted via selection); Enter locks the host and moves to
// "xy", where local (X,Y) in the cell frame are typed ("," steps X->Y),
// Enter places on the cell floor. The green ghost previews the unit at the
// current host + local position throughout.
