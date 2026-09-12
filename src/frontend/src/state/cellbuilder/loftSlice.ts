/**
 * Cellbuilder LOFT slice.
 *
 * Owns: the raw authored loft members — the editable source of truth behind the
 * `loft` band cells. Every action mutates a member, regenerates only that
 * member's bands (ids preserved by name so the selection and gizmo survive),
 * marks the document dirty and pushes one undo step shared with box edits.
 */

import {insertStation, removeStation, retypeStation, seedLoftMember, seedLoftMemberOnPlane, setExcludeFace, setStationParam, translateMember, type LoftMemberDoc} from "@/utils/cellbuilder/loft";
import type {Vec3} from "@/utils/cellbuilder/snap";
import type {BuilderCell, GizmoMode} from "./types";
import type {CellBuilderSlice} from "./state";
import {regenLoftMemberCells, remapLoftSelection, replaceLoftMember} from "./loftCells";
import {makeWithHistory} from "./historySlice";

export interface LoftSlice {
  /** Raw authored loft members — the editable source of truth for the `loft`
   * band cells (Phase 3a). Station/placement edits mutate this array and
   * regenerate the affected member's bands; toDoc re-emits it so a recompile
   * rebuilds the edited geometry. Per-face selection / openings on loft faces
   * are still deferred (design risk #1: no loft-native face id yet — Phase 3b). */
  loftMembers: LoftMemberDoc[];
  /** Edit one station's numeric param (Z/X/Y/WIDTH/HEIGHT/RADIUS). Rebuilds the
   * member's band cells, marks dirty, undoable. WIDTH/HEIGHT/RADIUS clamp >= 0. */
  setLoftStationParam: (
    memberName: string,
    stationIndex: number,
    key: string,
    value: number,
  ) => void;
  /** Insert a station after `afterIndex`, splitting that bay in two (or
   * extending past the last station). Bay count grows by one; undoable. */
  insertLoftStation: (memberName: string, afterIndex: number) => void;
  /** Remove the station at `stationIndex`, merging its adjacent bays. Refused
   * below 2 stations (the backend minimum); undoable. */
  removeLoftStation: (memberName: string, stationIndex: number) => void;
  /** Delete a whole loft member and all its bay cells (Del on a single-bay loft,
   * or an explicit remove). One undo step. */
  removeLoftMember: (memberName: string) => void;
  /** Translate a whole loft member by `delta` (world metres) via its PLACEMENT
   * translation column — moves every bay; undoable. */
  moveLoftMember: (memberName: string, delta: Vec3) => void;
  /** Rename a loft member (updates its bay cell names). Rejects a name already
   * taken by another loft member; undoable. */
  renameLoftMember: (memberName: string, name: string) => void;
  /** Add/remove a MEMBER-RELATIVE loft face id (e.g. `"bay0:edge2"`,
   * `"bay0:cap_lo"` — see `bandFaceIds`) in the member's EXCLUDE_FACES (Phase
   * 3b). `excluded=true` drops the face (its plate is omitted on recompile),
   * `false` restores it. Regenerates the member's band cells so the proxy dims
   * the removed panels; round-trips through toDoc; undoable. */
  setLoftFaceExcluded: (
    memberName: string,
    memberRelativeFaceId: string,
    excluded: boolean,
  ) => void;
  /** Replace a loft member's user-defined METADATA map (empty clears it).
   * Geometry-neutral — round-trips verbatim through the member; undoable. */
  setLoftMemberMetadata: (
    memberName: string,
    metadata: Record<string, unknown>,
  ) => void;
  /** Keyboard "new loft" (L): append a fresh 2-station circle member seeded at
   * the model ground origin and select its first bay. One undo step. */
  /** Start a new loft member (L). With `base` (from a selected cell face), the
   * loft grows out of that face — a rectangle tube on the face plane, sized to
   * the face, extruded along its normal. Without it, a default circle at ground. */
  addLoftMember: (base?: {
    placement: number[][];
    width: number;
    height: number;
  }) => void;
  /** Keyboard extrude for lofts (E): add a station `spacing` metres above the
   * member's top station and select the new top bay. One undo step. */
  extendLoftStack: (memberName: string, spacing: number) => void;
  /** Keyboard station resize (S): set the station's primary section dimension —
   * RADIUS (circle) or WIDTH·HEIGHT together (rectangle). One undo step. */
  resizeLoftStation: (
    memberName: string,
    stationIndex: number,
    primary: number,
  ) => void;
  /** Keyboard station retype (T): flip a station's section rectangle<->circle,
   * seeding sensible dimensions. One undo step. */
  setLoftStationType: (
    memberName: string,
    stationIndex: number,
    type: "rectangle" | "circle",
  ) => void;
}

export const createLoftSlice: CellBuilderSlice<LoftSlice> = (set) => {
  const withHistory = makeWithHistory(set);

  return {
    loftMembers: [],
    // --- Loft editing (Phase 3a) ---------------------------------------
    // Each: mutate the raw loftMembers (the source of truth), regenerate only
    // that member's band cells (ids preserved by name so selection/gizmo
    // survive), mark dirty, push an undo snapshot (shared with box edits). The
    // edited members round-trip verbatim through toDoc -> loft_members, so a
    // recompile rebuilds the edited geometry from the stations.
    setLoftStationParam: (memberName, stationIndex, key, value) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = setStationParam(member, stationIndex, key, value);
        if (next === member) return {};
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),
    insertLoftStation: (memberName, afterIndex) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = insertStation(member, afterIndex);
        if (next === member) return {};
        const cells = regenLoftMemberCells(s.cells, next);
        // The inserted station keeps bay `afterIndex` as its own lo bay; keep
        // the selection there so the panel stays on the same member.
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells,
          dirty: true,
          ...remapLoftSelection(s, cells, memberName, afterIndex),
        };
      }),
    removeLoftStation: (memberName, stationIndex) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = removeStation(member, stationIndex);
        if (next === member) return {}; // refused (< 2 stations) / out of range
        const cells = regenLoftMemberCells(s.cells, next);
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells,
          dirty: true,
          ...remapLoftSelection(s, cells, memberName, stationIndex - 1),
        };
      }),
    removeLoftMember: (memberName) =>
      withHistory((s) => {
        if (!s.loftMembers.some((m) => m.NAME === memberName)) return {};
        const removed = new Set<string>();
        const cells = { ...s.cells };
        for (const [id, c] of Object.entries(s.cells)) {
          if (c.kind === "loft" && c.loft?.member === memberName) {
            delete cells[id];
            removed.add(id);
          }
        }
        const selGone = s.selection ? removed.has(s.selection.cellId) : false;
        return {
          loftMembers: s.loftMembers.filter((m) => m.NAME !== memberName),
          cells,
          dirty: true,
          selection: selGone ? null : s.selection,
          selectedCellIds: s.selectedCellIds.filter((id) => !removed.has(id)),
          gizmoMode: selGone ? ("none" as GizmoMode) : s.gizmoMode,
        };
      }),
    moveLoftMember: (memberName, delta) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = translateMember(member, delta);
        if (next === member) return {};
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),
    renameLoftMember: (memberName, name) =>
      withHistory((s) => {
        const trimmed = name.trim();
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member || !trimmed || trimmed === memberName) return {};
        if (s.loftMembers.some((m) => m.NAME === trimmed)) return {}; // dup
        const next: LoftMemberDoc = { ...member, NAME: trimmed };
        // Rename shifts every bay cell name, so ids remint — clear the member's
        // cells first (so regen doesn't match stale names) then rebuild.
        const cleared: Record<string, BuilderCell> = {};
        for (const [id, c] of Object.entries(s.cells))
          if (!(c.kind === "loft" && c.loft?.member === memberName))
            cleared[id] = c;
        const cells = regenLoftMemberCells(cleared, next);
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells,
          dirty: true,
          ...remapLoftSelection(s, cells, trimmed, 0),
        };
      }),
    setLoftFaceExcluded: (memberName, faceId, excluded) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = setExcludeFace(member, faceId, excluded);
        if (next === member) return {}; // already in the wanted state — no-op
        // No geometry change (exclude only omits compiled plates), but the band
        // cells carry excludeFaces so the proxy can dim the removed panels —
        // regen so that reaches the controller. Ids stay stable (band count
        // unchanged) so the selection + gizmo survive.
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),

    setLoftMemberMetadata: (memberName, metadata) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const next = { ...member };
        // Empty -> drop the key entirely (no METADATA={} in the doc).
        if (metadata && Object.keys(metadata).length) next.METADATA = metadata;
        else delete next.METADATA;
        // Metadata is geometry-neutral, so no cell regen — just the raw member.
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          dirty: true,
        };
      }),
    addLoftMember: (base) =>
      withHistory((s) => {
        let n = s.loftMembers.length + 1;
        let name = `LOFT_${String(n).padStart(2, "0")}`;
        while (s.loftMembers.some((m) => m.NAME === name)) {
          n += 1;
          name = `LOFT_${String(n).padStart(2, "0")}`;
        }
        // On a selected face: grow the loft out of that face (rectangle sized to
        // the face, extruded along its normal). Otherwise seed a default circle
        // at the model's ground level so it lands in view (not a far-off z=0).
        const cells0 = Object.values(s.cells);
        const groundZ = cells0.length
          ? Math.min(...cells0.map((c) => c.origin[2]))
          : 0;
        const member = base
          ? seedLoftMemberOnPlane(name, base.placement, base.width, base.height, 3)
          : seedLoftMember(name, [0, 0, groundZ], 3);
        const cells = regenLoftMemberCells(s.cells, member);
        const bay0 = Object.values(cells).find(
          (c) => c.kind === "loft" && c.loft?.member === name && c.loft?.bay === 0,
        );
        return {
          loftMembers: [...s.loftMembers, member],
          cells,
          dirty: true,
          selection: bay0 ? { kind: "cell", cellId: bay0.id } : s.selection,
          selectedCellIds: bay0 ? [bay0.id] : s.selectedCellIds,
        };
      }),
    extendLoftStack: (memberName, spacing) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member || !spacing) return {};
        const stations = member.STATIONS ?? [];
        if (!stations.length) return {};
        const lastIdx = stations.length - 1;
        const topZ = Number(stations[lastIdx].Z);
        // Add a station duplicating the top, then set its exact spine offset.
        let next = insertStation(member, lastIdx);
        next = setStationParam(next, lastIdx + 1, "Z", topZ + spacing);
        if (next === member) return {};
        const cells = regenLoftMemberCells(s.cells, next);
        // Select the new top bay (bay index = the old top station index).
        const topBay = Object.values(cells).find(
          (c) =>
            c.kind === "loft" &&
            c.loft?.member === memberName &&
            c.loft?.bay === lastIdx,
        );
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells,
          dirty: true,
          selection: topBay
            ? { kind: "cell", cellId: topBay.id }
            : s.selection,
          selectedCellIds: topBay ? [topBay.id] : s.selectedCellIds,
        };
      }),
    resizeLoftStation: (memberName, stationIndex, primary) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const station = member.STATIONS?.[stationIndex];
        if (!station) return {};
        let next =
          station.TYPE === "circle"
            ? setStationParam(member, stationIndex, "RADIUS", primary)
            : setStationParam(member, stationIndex, "WIDTH", primary);
        if (station.TYPE !== "circle") {
          next = setStationParam(next, stationIndex, "HEIGHT", primary);
        }
        if (next === member) return {};
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),
    setLoftStationType: (memberName, stationIndex, type) =>
      withHistory((s) => {
        const member = s.loftMembers.find((m) => m.NAME === memberName);
        if (!member) return {};
        const station = member.STATIONS?.[stationIndex];
        if (!station || station.TYPE === type) return {};
        const nextStations = member.STATIONS.slice();
        nextStations[stationIndex] = retypeStation(station, type);
        const next: LoftMemberDoc = { ...member, STATIONS: nextStations };
        return {
          loftMembers: replaceLoftMember(s.loftMembers, memberName, next),
          cells: regenLoftMemberCells(s.cells, next),
          dirty: true,
        };
      }),

  };
};
