/**
 * The builder KEY HANDLER.
 *
 * Owns: one keydown handler that routes to whichever tool is live — an active
 * numeric entry swallows digits and Enter/Escape; otherwise the key starts a
 * tool, drives the gizmos and axis locks, or walks the selection. Registered in
 * the capture phase so it can preempt the global viewer key handler.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {bandFaceIds} from "@/utils/cellbuilder/loft";
import {BOX_FACE_SIDES, faceCenter, neighbourFaceInDirection, type Vec3} from "@/utils/cellbuilder/snap";
import type {CellBuilderScene} from "./sceneContext";
import {endModalMove} from "./cellGizmo";
import {commitNumEntry, commitPlaceEntry, endNumEntry, endPlaceEntry, loftMemberByName, parseTyped, refreshNumEntry, refreshPlaceEntry} from "./numericEntry";
import {commitOpenEntry, cycleEquipHost, endEquipEntry, endOpenEntry, refreshEquipEntry, refreshOpenEntry, setLoftActive, startCellExtrude, startEquipInsert, startLoftExtend, startLoftResize, startOpeningOnFace} from "./insertTools";

export const onKeyDown = (ctx: CellBuilderScene, ev: KeyboardEvent) => {
    const st = useCellBuilderStore.getState();
    if (!st.active) return;

    // Undo / redo — but not while typing in a form field (let the field's
    // own text undo win there).
    const target = ev.target as HTMLElement | null;
    const inField = !!target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);

    // An interactive extrude/station preview is live: capture numeric entry
    // so digits mean depth (not cell-type), Enter commits, Esc cancels,
    // Backspace edits. Any other key cancels the preview and falls through.
    if (!inField && ctx.numEntry && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        if (ev.key === "Enter") {
            commitNumEntry(ctx);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        if (ev.key === "Escape") {
            endNumEntry(ctx, true);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        if (ev.key === "Backspace") {
            ctx.numEntry.typed = ctx.numEntry.typed.slice(0, -1);
            refreshNumEntry(ctx);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        // Accept both the main row and the NUMPAD. ev.code (Numpad0..9 /
        // NumpadDecimal / NumpadSubtract) catches the numpad even with NumLock
        // off (where ev.key would be a nav key); the decimal also accepts ","
        // (numpad separator on Nordic layouts).
        const numpadDigit = /^Numpad([0-9])$/.exec(ev.code);
        if (numpadDigit || /^[0-9]$/.test(ev.key)) {
            ctx.numEntry.typed += numpadDigit ? numpadDigit[1] : ev.key;
            refreshNumEntry(ctx);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        if (
            (ev.key === "." || ev.key === "," || ev.code === "NumpadDecimal") &&
            !ctx.numEntry.typed.includes(".")
        ) {
            ctx.numEntry.typed += ".";
            refreshNumEntry(ctx);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        if ((ev.key === "-" || ev.code === "NumpadSubtract") && ctx.numEntry.typed === "") {
            ctx.numEntry.typed = "-";
            refreshNumEntry(ctx);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        // Not a numeric-entry key — abandon the preview, then let the key
        // fall through to its normal handling below.
        endNumEntry(ctx, true);
    }

    // Numeric placement while in an add mode: type X, "," next axis, Y, ",",
    // Z, Enter to drop the box at exactly (x,y,z). Activates on the first
    // digit (pointer placement still works until then); "." decimal, ","
    // steps axes, Enter places, Esc cancels.
    const inAddMode =
        st.mode === "add-cell" || st.mode === "add-opening" || st.mode === "add-equipment";
    if (!inField && inAddMode && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        const npd = /^Numpad([0-9])$/.exec(ev.code);
        const isDigit = !!npd || /^[0-9]$/.test(ev.key);
        if (ctx.placeEntry) {
            if (ev.key === "Enter") {
                commitPlaceEntry(ctx);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (ev.key === "Escape") {
                endPlaceEntry(ctx);
                st.setMode("idle");
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (ev.key === "Backspace") {
                ctx.placeEntry.typed = ctx.placeEntry.typed.slice(0, -1);
                refreshPlaceEntry(ctx);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (
                (ev.key === "." || ev.code === "NumpadDecimal") &&
                !ctx.placeEntry.typed.includes(".")
            ) {
                ctx.placeEntry.typed += ".";
                refreshPlaceEntry(ctx);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (ev.key === ",") {
                // Advance to the next axis (commit the typed value first).
                if (ctx.placeEntry.typed !== "")
                    ctx.placeEntry.vals[ctx.placeEntry.axis] = parseTyped(ctx, ctx.placeEntry.typed, 0);
                ctx.placeEntry.axis = Math.min(2, ctx.placeEntry.axis + 1) as 0 | 1 | 2;
                ctx.placeEntry.typed = "";
                refreshPlaceEntry(ctx);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if ((ev.key === "-" || ev.code === "NumpadSubtract") && ctx.placeEntry.typed === "") {
                ctx.placeEntry.typed = "-";
                refreshPlaceEntry(ctx);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (isDigit) {
                ctx.placeEntry.typed += npd ? npd[1] : ev.key;
                refreshPlaceEntry(ctx);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
        } else if (isDigit) {
            ctx.placeEntry = {axis: 0, vals: [null, null, null], typed: npd ? npd[1] : ev.key};
            refreshPlaceEntry(ctx);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
    }

    // Equipment-insert flow (keyboard): pick phase (T type / N,P cell / ↵
    // lock) then xy phase (numeric local X,Y / ↵ place). Captures its keys.
    if (!inField && ctx.equipEntry && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        const npd = /^Numpad([0-9])$/.exec(ev.code);
        const isDigit = !!npd || /^[0-9]$/.test(ev.key);
        const consume = () => {
            ev.preventDefault();
            ev.stopPropagation();
        };
        if (ev.key === "Escape") {
            endEquipEntry(ctx);
            consume();
            return;
        }
        if (ctx.equipEntry.phase === "pick") {
            if (ev.key === "Enter") {
                ctx.equipEntry.phase = "xy";
                ctx.equipEntry.axis = 0;
                ctx.equipEntry.vals = [null, null];
                ctx.equipEntry.typed = "";
                refreshEquipEntry(ctx);
                consume();
                return;
            }
            const k = ev.key.toLowerCase();
            if (k === "t") {
                st.cycleEquipmentType(1);
                refreshEquipEntry(ctx);
                consume();
                return;
            }
            if (k === "n" || k === "p") {
                cycleEquipHost(ctx, k === "n" ? 1 : -1);
                consume();
                return;
            }
            return; // swallow nothing else in pick phase
        }
        // xy phase — numeric local X,Y.
        if (ev.key === "Enter") {
            if (ctx.equipEntry.typed !== "")
                ctx.equipEntry.vals[ctx.equipEntry.axis] = parseTyped(ctx, ctx.equipEntry.typed, 0);
            const host = st.cells[ctx.equipEntry.hostId];
            const local: [number, number] = [
                ctx.equipEntry.vals[0] ?? (host ? host.size[0] / 2 : 0),
                ctx.equipEntry.vals[1] ?? (host ? host.size[1] / 2 : 0),
            ];
            const hostId = ctx.equipEntry.hostId;
            endEquipEntry(ctx);
            st.insertEquipmentAtLocal(hostId, local);
            consume();
            return;
        }
        if (ev.key === "Backspace") {
            ctx.equipEntry.typed = ctx.equipEntry.typed.slice(0, -1);
            refreshEquipEntry(ctx);
            consume();
            return;
        }
        if (ev.key === "," && ctx.equipEntry.axis === 0) {
            if (ctx.equipEntry.typed !== "")
                ctx.equipEntry.vals[0] = parseTyped(ctx, ctx.equipEntry.typed, 0);
            ctx.equipEntry.axis = 1;
            ctx.equipEntry.typed = "";
            refreshEquipEntry(ctx);
            consume();
            return;
        }
        if ((ev.key === "." || ev.code === "NumpadDecimal") && !ctx.equipEntry.typed.includes(".")) {
            ctx.equipEntry.typed += ".";
            refreshEquipEntry(ctx);
            consume();
            return;
        }
        if ((ev.key === "-" || ev.code === "NumpadSubtract") && ctx.equipEntry.typed === "") {
            ctx.equipEntry.typed = "-";
            refreshEquipEntry(ctx);
            consume();
            return;
        }
        if (isDigit) {
            ctx.equipEntry.typed += npd ? npd[1] : ev.key;
            refreshEquipEntry(ctx);
            consume();
            return;
        }
        return;
    }

    // Opening-on-face flow (keyboard): numeric X,Y,W,H,DEPTH fields. Captures
    // its keys; "," steps a field, Enter finishes the stage / commits.
    if (!inField && ctx.openEntry && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        const npd = /^Numpad([0-9])$/.exec(ev.code);
        const isDigit = !!npd || /^[0-9]$/.test(ev.key);
        const consume = () => {
            ev.preventDefault();
            ev.stopPropagation();
        };
        if (ev.key === "Escape") {
            endOpenEntry(ctx);
            consume();
            return;
        }
        if (ev.key === "Enter") {
            if (ctx.openEntry.typed !== "")
                ctx.openEntry.vals[ctx.openEntry.field] = parseTyped(ctx, ctx.openEntry.typed, ctx.openEntry.vals[ctx.openEntry.field]);
            if (ctx.openEntry.field >= 4) {
                commitOpenEntry(ctx);
            } else {
                // X/Y -> W (field 2); W/H -> DEPTH (field 4).
                ctx.openEntry.field = (ctx.openEntry.field < 2 ? 2 : 4) as 0 | 1 | 2 | 3 | 4;
                ctx.openEntry.typed = "";
                refreshOpenEntry(ctx);
            }
            consume();
            return;
        }
        if (ev.key === "Backspace") {
            ctx.openEntry.typed = ctx.openEntry.typed.slice(0, -1);
            refreshOpenEntry(ctx);
            consume();
            return;
        }
        if (ev.key === ",") {
            if (ctx.openEntry.typed !== "")
                ctx.openEntry.vals[ctx.openEntry.field] = parseTyped(ctx, ctx.openEntry.typed, ctx.openEntry.vals[ctx.openEntry.field]);
            ctx.openEntry.field = Math.min(4, ctx.openEntry.field + 1) as 0 | 1 | 2 | 3 | 4;
            ctx.openEntry.typed = "";
            refreshOpenEntry(ctx);
            consume();
            return;
        }
        if ((ev.key === "." || ev.code === "NumpadDecimal") && !ctx.openEntry.typed.includes(".")) {
            ctx.openEntry.typed += ".";
            refreshOpenEntry(ctx);
            consume();
            return;
        }
        if ((ev.key === "-" || ev.code === "NumpadSubtract") && ctx.openEntry.typed === "") {
            ctx.openEntry.typed = "-";
            refreshOpenEntry(ctx);
            consume();
            return;
        }
        if (isDigit) {
            ctx.openEntry.typed += npd ? npd[1] : ev.key;
            refreshOpenEntry(ctx);
            consume();
            return;
        }
        return;
    }

    if ((ev.ctrlKey || ev.metaKey) && !inField) {
        const k = ev.key.toLowerCase();
        if (k === "z" && !ev.shiftKey) {
            st.undo();
            ev.preventDefault();
            return;
        }
        if ((k === "z" && ev.shiftKey) || k === "y") {
            st.redo();
            ev.preventDefault();
            return;
        }
    }

    // Bump the selected equipment up/down a cell floor (desktop shortcut).
    // PageUp = up a floor, PageDown = down; no-op without an equipment pick.
    if (!inField && (ev.key === "PageUp" || ev.key === "PageDown")) {
        if (st.selection) {
            st.bumpSelectedFloor(ev.key === "PageUp" ? 1 : -1);
            requestRender();
        }
        ev.preventDefault();
        return;
    }

    // Delete / Backspace removes the selected cell(s)/equipment — the keyboard
    // equivalent of the context-menu Delete. Multi-selection deletes all in one
    // undo step. A loft bay: shrink the member by one station, or remove the
    // whole member when it's down to its last bay (2 stations) — so Del peels
    // bays and finally deletes the loft.
    if (!inField && (ev.key === "Delete" || ev.key === "Backspace")) {
        const ids = st.selectedCellIds.length
            ? st.selectedCellIds
            : st.selection
              ? [st.selection.cellId]
              : [];
        if (!ids.length) return;
        ev.preventDefault();
        ev.stopPropagation();
        st.beginTransaction();
        for (const id of ids) {
            const cur = useCellBuilderStore.getState();
            const cell = cur.cells[id];
            if (!cell) continue;
            if (cell.kind === "loft") {
                const member = cur.loftMembers.find((m) => m.NAME === cell.loft?.member);
                if (!member) continue;
                if ((member.STATIONS?.length ?? 0) <= 2) {
                    cur.removeLoftMember(member.NAME);
                } else {
                    const idx =
                        ctx.loftActive && ctx.loftActive.member === member.NAME
                            ? ctx.loftActive.index
                            : (cell.loft?.bay ?? 0);
                    cur.removeLoftStation(member.NAME, Math.min(idx, member.STATIONS.length - 1));
                }
            } else {
                cur.removeCell(id);
            }
        }
        st.endTransaction();
        requestRender();
        return;
    }

    // --- Blender-style gizmo shortcuts (not while typing in a field) ------
    // These consume the key (stopPropagation) so the global viewer handler
    // — same phase, but this listener runs in capture — doesn't also fire.
    if (!inField && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        const k = ev.key.toLowerCase();
        const cell = st.selection ? st.cells[st.selection.cellId] : null;
        // Representation-mode shortcuts (work regardless of selection so you
        // can flip views mid-edit), mirroring the Representation button row:
        // backtick cycles topology→simulation→detail, Shift+backtick reverses;
        // Shift+1/2/3 jump straight to a mode. Plain digits are left to the
        // builder's "1–9 = cell type" picker below (Shift+digit is `!@#`, which
        // that picker's /^[1-9]$/ test ignores — so no clash).
        const REP_MODES = ["topology", "simulation", "detail"] as const;
        if (ev.code === "Backquote") {
            const cur = Math.max(0, REP_MODES.indexOf(st.repMode));
            const dir = ev.shiftKey ? -1 : 1;
            const next = REP_MODES[(cur + dir + REP_MODES.length) % REP_MODES.length];
            void st.setRepMode(next);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        if (
            ev.shiftKey &&
            (ev.code === "Digit1" || ev.code === "Digit2" || ev.code === "Digit3")
        ) {
            void st.setRepMode(REP_MODES[Number(ev.code.slice(-1)) - 1]);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        // Shift+H hides the selected builder cells (mirrors the Hide buttons).
        // With a builder selection this wins over the global mesh-range hide;
        // with nothing selected it falls through so result meshes still hide.
        if (ev.shiftKey && k === "h") {
            const ids = st.selectedCellIds.length
                ? st.selectedCellIds
                : cell
                  ? [cell.id]
                  : [];
            if (ids.length) {
                st.hideCells(ids);
                ev.preventDefault();
                ev.stopPropagation();
            }
            return;
        }
        // Shift+U unhides all builder cells. Unlike hide, this does NOT stop
        // propagation — "unhide all" should reveal everything, so the global
        // handler still runs to unhide any hidden result meshes too.
        if (ev.shiftKey && k === "u") {
            st.unhideAllCells();
            return;
        }
        // Shift+X toggles exclusion on the selected loft face panel (drops
        // its plate on recompile). Maps the picked material index to the
        // member-relative face id via bandFaceIds.
        if (ev.shiftKey && k === "x") {
            const sel = st.selection;
            if (
                sel?.kind === "face" &&
                sel.faceIndex != null &&
                cell?.kind === "loft" &&
                cell.loft
            ) {
                const {edges, caps} = bandFaceIds(cell.loft);
                const faceIds = [...edges, caps[0], caps[1]];
                const fid = faceIds[sel.faceIndex];
                if (fid) {
                    const excluded = cell.loft.excludeFaces.includes(fid);
                    st.setLoftFaceExcluded(cell.loft.member, fid, !excluded);
                    ev.preventDefault();
                    ev.stopPropagation();
                }
            }
            return;
        }
        // Loft members: G moves the whole member (existing translate gizmo),
        // S starts a numeric section resize of the active station.
        if (!ev.shiftKey && cell && cell.kind === "loft") {
            if (k === "g") {
                st.setGizmoMode("translate");
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (k === "s") {
                if (startLoftResize(ctx, cell)) {
                    ev.preventDefault();
                    ev.stopPropagation();
                }
                return;
            }
        }
        // G/R/S activate the translate / rotate / resize gizmo (Blender keys):
        // rotate is equipment-only, resize is cell-only (matches the menus).
        if (!ev.shiftKey && cell && cell.kind !== "loft") {
            if (k === "g") {
                st.setGizmoMode("translate");
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (k === "r" && cell.kind === "equipment") {
                st.setGizmoMode("rotate");
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            if (k === "s" && cell.kind === "cell") {
                st.setGizmoMode("resize");
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
        }
        // X/Y/Z lock the active translate/rotate gizmo to that axis (press the
        // same axis again to release). The HUD's numeric field then applies
        // along it.
        if (
            (st.gizmoMode === "translate" || st.gizmoMode === "rotate") &&
            (k === "x" || k === "y" || k === "z")
        ) {
            const axis = k === "x" ? 0 : k === "y" ? 1 : 2;
            st.setGizmoAxisLock(st.gizmoAxisLock === axis ? null : axis);
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }

        // --- Keyboard topology scheme (single keys) ----------------------
        if (!ev.shiftKey) {
            // L: start a new loft member. With a cell FACE selected, base the
            // loft on that face — a rectangle tube sized to the face, growing
            // out along its normal. Otherwise a default circle at the ground.
            if (k === "l") {
                const selCell = st.selection ? st.cells[st.selection.cellId] : null;
                let base:
                    | {placement: number[][]; width: number; height: number}
                    | undefined;
                if (
                    selCell &&
                    selCell.kind === "cell" &&
                    st.selection?.kind === "face" &&
                    st.selection.faceIndex != null &&
                    BOX_FACE_SIDES[st.selection.faceIndex]
                ) {
                    const fi = st.selection.faceIndex;
                    const side = BOX_FACE_SIDES[fi];
                    const inPlane = ([0, 1, 2] as const).filter((a) => a !== side.axis) as [
                        0 | 1 | 2,
                        0 | 1 | 2,
                    ];
                    const [a1, a2] = inPlane;
                    const U: Vec3 = [0, 0, 0];
                    U[a1] = 1;
                    const V: Vec3 = [0, 0, 0];
                    V[a2] = 1;
                    const N: Vec3 = [0, 0, 0];
                    N[side.axis] = side.positive ? 1 : -1;
                    const c = faceCenter(selCell, fi); // model space
                    base = {
                        placement: [
                            [U[0], V[0], N[0], c[0]],
                            [U[1], V[1], N[1], c[1]],
                            [U[2], V[2], N[2], c[2]],
                            [0, 0, 0, 1],
                        ],
                        width: selCell.size[a1],
                        height: selCell.size[a2],
                    };
                }
                st.addLoftMember(base);
                const members = useCellBuilderStore.getState().loftMembers;
                const last = members[members.length - 1];
                if (last) setLoftActive(ctx, last.NAME, 0);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // A: enter add-cell placement (pointer ghost).
            if (k === "a") {
                st.setMode("add-cell");
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // I: keyboard equipment insert (pick type + host cell, then type
            // the local X,Y). No cell needed to start.
            if (k === "i") {
                st.setMode("idle");
                if (startEquipInsert(ctx)) {
                    ev.preventDefault();
                    ev.stopPropagation();
                }
                return;
            }
            // O: keyboard opening on the selected cell FACE (numeric X,Y,W,H,
            // depth). No-op unless a space-cell face is selected.
            if (k === "o") {
                if (
                    cell?.kind === "cell" &&
                    st.selection?.kind === "face" &&
                    st.selection.faceIndex != null &&
                    startOpeningOnFace(ctx, cell, st.selection.faceIndex)
                ) {
                    ev.preventDefault();
                    ev.stopPropagation();
                }
                return;
            }
            // Tab: cycle selection granularity cell -> face -> edge.
            if (k === "tab" && st.selection) {
                st.cycleSelectMode(1);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // N / P: next / previous cell.
            if (k === "n" || k === "p") {
                st.selectAdjacentCell(k === "n" ? 1 : -1);
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // Arrow keys: SPATIAL face navigation. With a box-cell face
            // selected, walk to the edge-adjacent face that lies toward the
            // arrow ON SCREEN (Right = the neighbour whose outward normal
            // projects most to screen-right, etc), using the live camera
            // basis. Consumes the key so the camera doesn't also pan; only
            // active in face mode on a box cell (bare arrows are otherwise
            // free — globals use Shift+arrows for tree traversal).
            if (
                (ev.key === "ArrowUp" ||
                    ev.key === "ArrowDown" ||
                    ev.key === "ArrowLeft" ||
                    ev.key === "ArrowRight") &&
                cell?.kind === "cell" &&
                st.selection?.kind === "face" &&
                st.selection.faceIndex != null
            ) {
                const camObj = getViewerRuntime().camera.current;
                if (camObj) {
                    const camRight = new THREE.Vector3()
                        .setFromMatrixColumn(camObj.matrixWorld, 0)
                        .normalize();
                    const camUp = new THREE.Vector3()
                        .setFromMatrixColumn(camObj.matrixWorld, 1)
                        .normalize();
                    const dir =
                        ev.key === "ArrowUp"
                            ? "up"
                            : ev.key === "ArrowDown"
                              ? "down"
                              : ev.key === "ArrowLeft"
                                ? "left"
                                : "right";
                    const nb = neighbourFaceInDirection(
                        st.selection.faceIndex,
                        dir,
                        [camRight.x, camRight.y, camRight.z],
                        [camUp.x, camUp.y, camUp.z],
                    );
                    if (nb != null && nb !== st.selection.faceIndex) {
                        st.setSelection({kind: "face", cellId: cell.id, faceIndex: nb});
                    }
                }
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // F / D: next / previous element — faces / edges, or loft stations.
            if ((k === "f" || k === "d") && cell) {
                const dir = k === "f" ? 1 : -1;
                if (cell.kind === "loft" && cell.loft) {
                    const member = loftMemberByName(ctx, cell.loft.member);
                    if (member) {
                        const cur =
                            ctx.loftActive && ctx.loftActive.member === member.NAME
                                ? ctx.loftActive.index
                                : cell.loft.bay;
                        const n = member.STATIONS.length;
                        setLoftActive(ctx, member.NAME, ((cur + dir) % n + n) % n);
                    }
                } else {
                    st.cycleSelectionElement(dir);
                }
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // T: loft station retype (rectangle<->circle) or cell-type cycle.
            if (k === "t") {
                if (cell?.kind === "loft" && cell.loft && ctx.loftActive) {
                    const member = loftMemberByName(ctx, cell.loft.member);
                    const station = member?.STATIONS[ctx.loftActive.index];
                    if (member && station) {
                        st.setLoftStationType(
                            member.NAME,
                            ctx.loftActive.index,
                            station.TYPE === "circle" ? "rectangle" : "circle",
                        );
                    }
                } else {
                    st.cycleCellType(1);
                }
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            // E: interactive extrude — box cell from its selected face, or
            // extend a loft stack up its spine. No face selected = no-op (Q1).
            if (k === "e") {
                let started = false;
                if (cell?.kind === "loft") {
                    started = startLoftExtend(ctx, cell);
                } else if (
                    cell?.kind === "cell" &&
                    st.selection?.kind === "face" &&
                    st.selection.faceIndex != null
                ) {
                    started = startCellExtrude(ctx, cell, st.selection.faceIndex);
                }
                if (started) {
                    ev.preventDefault();
                    ev.stopPropagation();
                }
                return;
            }
            // 1-9: pick a cell type directly from the advertised catalog.
            if (/^[1-9]$/.test(ev.key)) {
                const types = st.cellTypes;
                const idx = Number(ev.key) - 1;
                if (idx < types.length) {
                    st.setSelectedCellType(types[idx].slug);
                    ev.preventDefault();
                    ev.stopPropagation();
                }
                return;
            }
        }
    }

    // Escape while typing in a field (the HUD's numeric inputs) blurs the
    // field — don't also unwind the selection/gizmo underneath.
    if (ev.key !== "Escape" || inField) return;
    // The insert popover owns its own Escape (it closes itself); don't also
    // unwind the selection underneath it.
    if (st.insertMenu) return;
    // An active axis-locked modal move: Escape cancels it (restore the cell)
    // and drops the lock, without also tearing down the gizmo/selection.
    if (ctx.modalMove) {
        endModalMove(ctx, true);
        st.setGizmoAxisLock(null);
        ev.preventDefault();
        ev.stopPropagation();
        return;
    }
    // Escape unwinds one layer at a time: menu → gizmo → add-mode → selection.
    if (st.portMenu) {
        st.closePortMenu();
    } else if (st.portGizmo) {
        st.stopPortGizmo();
    } else if (st.contextMenu) {
        st.closeContextMenu();
    } else if (st.gizmoMode !== "none") {
        st.setGizmoMode("none");
    } else if (st.mode !== "idle") {
        st.setMode("idle");
        ctx.ghost.visible = false;
        requestRender();
    } else if (st.selection) {
        st.setSelection(null);
    }
};
