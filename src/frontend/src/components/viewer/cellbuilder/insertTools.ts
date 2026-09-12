/**
 * Keyboard INSERT tools: equipment, openings, extrude and loft starts.
 *
 * Owns: the equipment-insert entry (pick a type and host cell, then type
 * cell-local X/Y), the opening-on-face entry (X/Y, W/H, DEPTH in the face
 * plane), and the starters that arm a numeric entry for a box extrude, a loft
 * stack extension or a loft station resize.
 */

import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {BOX_FACE_SIDES, openingBoxOnFace, type Vec3} from "@/utils/cellbuilder/snap";
import {DEFAULT_EQUIPMENT_SIZE, OPENING_COLOR} from "./sceneConstants";
import type {OpenEntry} from "./sceneTypes";
import type {CellBuilderScene} from "./sceneContext";
import {fmt, hideReadout, loftMemberByName, parseTyped, refreshNumEntry, setGhostBox, showReadout} from "./numericEntry";

export const spaceCells = (ctx: CellBuilderScene): BuilderCell[] =>
    Object.values(useCellBuilderStore.getState().cells)
        .filter((c) => c.kind === "cell")
        .sort((a, b) => a.name.localeCompare(b.name));


export const equipTypeName = (ctx: CellBuilderScene): string => {
    const st = useCellBuilderStore.getState();
    const t = st.equipmentTypes.find((x) => x.slug === st.selectedEquipmentType);
    return t?.name ?? st.selectedEquipmentType ?? "EQ";
};

export const refreshEquipEntry = (ctx: CellBuilderScene) => {
    if (!ctx.equipEntry) return;
    const st = useCellBuilderStore.getState();
    const host = st.cells[ctx.equipEntry.hostId];
    if (!host) return endEquipEntry(ctx);
    const size = DEFAULT_EQUIPMENT_SIZE;
    const local: [number, number] =
        ctx.equipEntry.phase === "xy"
            ? [
                  ctx.equipEntry.vals[0] ??
                      (ctx.equipEntry.axis === 0 ? parseTyped(ctx, ctx.equipEntry.typed, host.size[0] / 2) : host.size[0] / 2),
                  ctx.equipEntry.vals[1] ??
                      (ctx.equipEntry.axis === 1 ? parseTyped(ctx, ctx.equipEntry.typed, host.size[1] / 2) : host.size[1] / 2),
              ]
            : [host.size[0] / 2, host.size[1] / 2];
    // Cell-local (X,Y) centre -> world origin (min corner), seated on floor.
    const origin: Vec3 = [
        host.origin[0] + local[0] - size[0] / 2,
        host.origin[1] + local[1] - size[1] / 2,
        host.origin[2],
    ];
    setGhostBox(ctx, {origin, size});
    const centre: Vec3 = [origin[0] + size[0] / 2, origin[1] + size[1] / 2, origin[2] + size[2] / 2];
    if (ctx.equipEntry.phase === "pick") {
        showReadout(ctx, `${equipTypeName(ctx)} → ${host.name}`, centre);
        st.setToolHint(
            `Insert ${equipTypeName(ctx)} @ cell ${host.name} — T type, N/P cell, ↵ pick, Esc cancel`,
        );
    } else {
        showReadout(ctx, `${equipTypeName(ctx)} (${fmt(ctx, local[0])}, ${fmt(ctx, local[1])})`, centre);
        st.setToolHint(
            `Equip ${equipTypeName(ctx)} @ cell ${host.name} local (${fmt(ctx, local[0])}, ${fmt(ctx, local[1])}) — type, "," X→Y, ↵ place, Esc cancel`,
        );
    }
    requestRender();
};

export const endEquipEntry = (ctx: CellBuilderScene) => {
    ctx.equipEntry = null;
    ctx.ghost.visible = false;
    ctx.ghostBox = null;
    hideReadout(ctx);
    useCellBuilderStore.getState().setToolHint(null);
    requestRender();
};

export const startEquipInsert = (ctx: CellBuilderScene): boolean => {
    const st = useCellBuilderStore.getState();
    const cells = spaceCells(ctx);
    if (!cells.length) {
        st.setToolHint("Add a cell first — equipment needs a host cell");
        return false;
    }
    // Default the type if none is chosen yet, so the ghost/readout have one.
    if (!st.selectedEquipmentType && st.equipmentTypes.length) {
        st.setSelectedEquipmentType(st.equipmentTypes[0].slug);
    }
    const selId = st.selection?.cellId;
    const host = (selId && st.cells[selId]?.kind === "cell" ? st.cells[selId] : null) ?? cells[0];
    ctx.equipEntry = {phase: "pick", hostId: host.id, axis: 0, vals: [null, null], typed: ""};
    if (st.selection?.cellId !== host.id) st.setSelection({kind: "cell", cellId: host.id});
    refreshEquipEntry(ctx);
    return true;
};

export const cycleEquipHost = (ctx: CellBuilderScene, dir: 1 | -1) => {
    if (!ctx.equipEntry) return;
    const cells = spaceCells(ctx);
    if (!cells.length) return;
    const i = cells.findIndex((c) => c.id === ctx.equipEntry!.hostId);
    const host = cells[((i < 0 ? 0 : i) + dir + cells.length) % cells.length];
    ctx.equipEntry.hostId = host.id;
    const st = useCellBuilderStore.getState();
    if (st.selection?.cellId !== host.id) st.setSelection({kind: "cell", cellId: host.id});
    refreshEquipEntry(ctx);
};

// --- Keyboard opening-on-face insert (O) ---------------------------------
// A cell FACE must be selected. Numeric fields in the face's 2D plane:
// [X, Y] lower corner, then [W, H], then DEPTH (half through-thickness). ","
// steps to the next field; Enter finishes the current stage (X/Y -> W/H ->
// DEPTH) and commits on the last. The negative box straddles the face plane.
const OPEN_FIELDS = ["x", "y", "w", "h", "depth"] as const;

export const openVals = (ctx: CellBuilderScene, oe: OpenEntry): [number, number, number, number, number] => {
    const out = [...oe.vals] as [number, number, number, number, number];
    if (oe.typed !== "") out[oe.field] = parseTyped(ctx, oe.typed, oe.vals[oe.field]);
    return out;
};

export const refreshOpenEntry = (ctx: CellBuilderScene) => {
    if (!ctx.openEntry) return;
    const st = useCellBuilderStore.getState();
    const cell = st.cells[ctx.openEntry.cellId];
    if (!cell || cell.kind !== "cell") return endOpenEntry(ctx);
    const [x, y, w, h, d] = openVals(ctx, ctx.openEntry);
    const box = openingBoxOnFace(cell, ctx.openEntry.faceIndex, x, y, w, h, d);
    setGhostBox(ctx, box, OPENING_COLOR); // red — a negative-volume cut
    const centre: Vec3 = [
        box.origin[0] + box.size[0] / 2,
        box.origin[1] + box.size[1] / 2,
        box.origin[2] + box.size[2] / 2,
    ];
    const fieldVal = [x, y, w, h, d][ctx.openEntry.field];
    showReadout(ctx, `${OPEN_FIELDS[ctx.openEntry.field]} ${fmt(ctx, fieldVal)}`, centre);
    st.setToolHint(
        `Opening ${OPEN_FIELDS[ctx.openEntry.field]}=${fmt(ctx, fieldVal)} (x${fmt(ctx, x)} y${fmt(ctx, y)} w${fmt(ctx, w)} h${fmt(ctx, h)} d${fmt(ctx, d)}) — type, "," next, ↵ next/commit, Esc cancel`,
    );
    requestRender();
};

export const endOpenEntry = (ctx: CellBuilderScene) => {
    ctx.openEntry = null;
    ctx.ghost.visible = false;
    ctx.ghostBox = null;
    hideReadout(ctx);
    useCellBuilderStore.getState().setToolHint(null);
    requestRender();
};

export const startOpeningOnFace = (ctx: CellBuilderScene, cell: BuilderCell, faceIndex: number): boolean => {
    if (cell.kind !== "cell" || !BOX_FACE_SIDES[faceIndex]) return false;
    ctx.openEntry = {cellId: cell.id, faceIndex, field: 0, vals: [0, 0, 1, 1, 1], typed: ""};
    refreshOpenEntry(ctx);
    return true;
};

export const commitOpenEntry = (ctx: CellBuilderScene) => {
    if (!ctx.openEntry) return;
    const st = useCellBuilderStore.getState();
    const cell = st.cells[ctx.openEntry.cellId];
    if (cell && cell.kind === "cell") {
        const [x, y, w, h, d] = openVals(ctx, ctx.openEntry);
        const box = openingBoxOnFace(cell, ctx.openEntry.faceIndex, x, y, w, h, d);
        if (box.size[0] > 0 && box.size[1] > 0 && box.size[2] > 0) {
            // addCell uses the current selectedOpeningType's subtype and
            // round-trips as a USE_GLOBAL_COORDS negative box (one undo step).
            st.addCell("opening", box.origin, box.size);
        }
    }
    endOpenEntry(ctx);
};

export const startCellExtrude = (ctx: CellBuilderScene, cell: BuilderCell, faceIndex: number): boolean => {
    const side = BOX_FACE_SIDES[faceIndex];
    if (!side || cell.kind !== "cell") return false;
    ctx.numEntry = {
        kind: "cellExtrude",
        cellId: cell.id,
        faceIndex,
        axis: side.axis,
        defaultDepth: cell.size[side.axis],
        typed: "",
    };
    refreshNumEntry(ctx);
    return true;
};

export const startLoftExtend = (ctx: CellBuilderScene, cell: BuilderCell): boolean => {
    if (cell.kind !== "loft" || !cell.loft) return false;
    const member = loftMemberByName(ctx, cell.loft.member);
    if (!member || !member.STATIONS.length) return false;
    const n = member.STATIONS.length;
    const top = member.STATIONS[n - 1];
    const prev = n >= 2 ? member.STATIONS[n - 2] : null;
    const spacing = prev ? Math.abs(Number(top.Z) - Number(prev.Z)) || 3 : 3;
    ctx.numEntry = {
        kind: "loftExtend",
        memberName: member.NAME,
        defaultSpacing: spacing,
        anchor: [Number(top.X), Number(top.Y), Number(top.Z)],
        typed: "",
    };
    refreshNumEntry(ctx);
    return true;
};

export const startLoftResize = (ctx: CellBuilderScene, cell: BuilderCell): boolean => {
    if (cell.kind !== "loft" || !cell.loft) return false;
    const member = loftMemberByName(ctx, cell.loft.member);
    if (!member) return false;
    const idx =
        ctx.loftActive && ctx.loftActive.member === member.NAME ? ctx.loftActive.index : cell.loft.bay;
    const station = member.STATIONS[idx];
    if (!station) return false;
    const section = station.TYPE;
    ctx.numEntry = {
        kind: "loftResize",
        memberName: member.NAME,
        stationIndex: idx,
        section,
        defaultVal: section === "circle" ? (station.RADIUS ?? 1) : (station.WIDTH ?? 1),
        anchor: [Number(station.X), Number(station.Y), Number(station.Z)],
        typed: "",
    };
    refreshNumEntry(ctx);
    return true;
};

// Point the active loft station at `index` (clamped) and select the bay that
// contains it so the panel/highlight follow. Sets loftActive.member first so
// the selection subscription doesn't reset the index we just chose.
export const setLoftActive = (ctx: CellBuilderScene, member: string, index: number) => {
    const st = useCellBuilderStore.getState();
    const m = loftMemberByName(ctx, member);
    if (!m) return;
    const nStations = m.STATIONS.length;
    const idx = Math.min(Math.max(index, 0), nStations - 1);
    ctx.loftActive = {member, index: idx};
    const bay = Math.min(idx, nStations - 2);
    const band = Object.values(st.cells).find(
        (c) => c.kind === "loft" && c.loft?.member === member && c.loft?.bay === bay,
    );
    if (band && st.selection?.cellId !== band.id) {
        st.setSelection({kind: "cell", cellId: band.id});
    }
    requestRender();
};
