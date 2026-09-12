/**
 * Cellbuilder BLUEPRINTS and GROUPS slice.
 *
 * Owns: the structural blueprint the compiler dispatches on, the whitelisted
 * blueprint options round-tripped as `doc.blueprint`, the equipment-CAD toggle,
 * and the cell groups — each a structure compiled with its own blueprint, which
 * cells reference by name. All of it round-trips through the document, so every
 * change is undoable and marks the model dirty.
 */

import {capabilities} from "@/services/capabilities";
import type {ProceduralBlueprintOption} from "@/services/viewerApi";
import {resolveSelectedBlueprint} from "@/utils/cellbuilder/blueprints";
import {groupAfterRemoval, type CellGroup} from "@/utils/cellbuilder/groups";
import type {BuilderCell} from "./types";
import type {CellBuilderSlice} from "./state";
import {currentScopePart} from "./shared";
import {makeWithHistory} from "./historySlice";

export interface BlueprintsSlice {
  /** Blueprint compile options round-tripped as doc.blueprint (whitelisted
   * server-side), e.g. {reinforce_internal_walls: true}. */
  blueprintOptions: Record<string, unknown>;
  /** When true, catalog equipment with a linked CAD asset render as the real
   * CAD geometry (spliced at compile) instead of a box. Round-trips as
   * doc.equipment_cad. */
  equipmentCad: boolean;
  /** Selected structural blueprint slug the compiler dispatches on; round-trips
   * as doc.blueprint_name. Null until the (engine-scoped) list is fetched. */
  selectedBlueprint: string | null;
  /** Available structural blueprints for the Blueprint dropdown, scoped to the
   * selected engine (built-in ∪ engine-advertised). Refetched on engine change. */
  blueprints: ProceduralBlueprintOption[];
  /** Cell groups (name + per-group blueprint). A group is one structure the
   * (grouping-capable) engine compiles with its own blueprint; cells reference a
   * group by name via BuilderCell.group. Empty = single model-level blueprint
   * (backward compatible). Only meaningful for engines advertising
   * `supports_grouping`. */
  groups: CellGroup[];
  setEquipmentCad: (v: boolean) => void;
  /** Select the structural blueprint (doc.blueprint_name); marks the model dirty. */
  setSelectedBlueprint: (slug: string) => void;
  /** Set one advertised blueprint parameter into doc.blueprint (e.g. a section
   * profile like `girder_sec`); marks the model dirty so a recompile picks it up. */
  setBlueprintOption: (name: string, value: unknown) => void;
  /** Fetch the blueprints the SELECTED engine offers and reconcile the current
   * selection (keep it if still offered, else the engine's default). */
  fetchBlueprints: () => Promise<void>;
  /** Add a new cell group (auto-named; blueprint defaults to the engine's
   * default / current selection). Undoable; marks the model dirty. */
  addGroup: () => void;
  /** Rename a group (from -> to); reassigns every cell pointing at it. A blank or
   * duplicate target is ignored. Undoable; marks the model dirty. */
  renameGroup: (from: string, to: string) => void;
  /** Delete a group; unassigns its cells (back to ungrouped). Undoable; dirty. */
  removeGroup: (name: string) => void;
  /** Set a group's structural blueprint. Undoable; marks the model dirty. */
  setGroupBlueprint: (name: string, blueprint: string) => void;
  /** Assign a cell to a group (or null to clear). Undoable; marks the model dirty. */
  setCellGroup: (cellId: string, groupName: string | null) => void;
}

export const createBlueprintsSlice: CellBuilderSlice<BlueprintsSlice> = (set, get) => {
  const withHistory = makeWithHistory(set);

  return {
    blueprintOptions: {},
    equipmentCad: false,
    selectedBlueprint: null,
    blueprints: [],
    groups: [],
    setEquipmentCad: (equipmentCad) =>
      withHistory(() => ({ equipmentCad, dirty: true })),

    // Picking a blueprint changes the document (doc.blueprint_name), so it marks
    // the model dirty — unlike the compile-time engine/ruleset toggles.
    setSelectedBlueprint: (slug) =>
      set((s) =>
        slug === s.selectedBlueprint
          ? {}
          : { selectedBlueprint: slug, dirty: true },
      ),

    setBlueprintOption: (name, value) =>
      set((s) => ({
        blueprintOptions: { ...s.blueprintOptions, [name]: value },
        dirty: true,
      })),

    fetchBlueprints: async () => {
      try {
        const blueprints = await capabilities.procedural.listBlueprints(
          currentScopePart(),
          get().selectedEngine,
        );
        set((s) => ({
          blueprints,
          selectedBlueprint: resolveSelectedBlueprint(
            blueprints,
            s.selectedBlueprint,
          ),
        }));
      } catch (e) {
        console.warn("cellbuilder: blueprints fetch failed", e);
        set({ blueprints: [] });
      }
    },

    addGroup: () =>
      withHistory((s) => {
        // Auto-name "Group N" avoiding collisions; default the new group's
        // blueprint to the current selection (else the engine's first offered).
        const existing = new Set(s.groups.map((g) => g.name));
        let n = s.groups.length + 1;
        let name = `Group ${n}`;
        while (existing.has(name)) name = `Group ${++n}`;
        const blueprint = s.selectedBlueprint ?? s.blueprints[0]?.slug ?? "";
        return { groups: [...s.groups, { name, blueprint }], dirty: true };
      }),

    renameGroup: (from, to) =>
      withHistory((s) => {
        const name = to.trim();
        // Ignore blank / unchanged / colliding names, or a missing source.
        if (
          !name ||
          name === from ||
          s.groups.some((g) => g.name === name) ||
          !s.groups.some((g) => g.name === from)
        )
          return {};
        const groups = s.groups.map((g) => (g.name === from ? { ...g, name } : g));
        // Repoint every cell that referenced the old name.
        const cells: Record<string, BuilderCell> = {};
        for (const [id, c] of Object.entries(s.cells))
          cells[id] = c.group === from ? { ...c, group: name } : c;
        return { groups, cells, dirty: true };
      }),

    removeGroup: (name) =>
      withHistory((s) => {
        if (!s.groups.some((g) => g.name === name)) return {};
        const groups = s.groups.filter((g) => g.name !== name);
        const removed = new Set([name]);
        // Unassign every cell in the deleted group (back to ungrouped).
        const cells: Record<string, BuilderCell> = {};
        for (const [id, c] of Object.entries(s.cells))
          cells[id] = { ...c, group: groupAfterRemoval(c.group, removed) };
        return { groups, cells, dirty: true };
      }),

    setGroupBlueprint: (name, blueprint) =>
      withHistory((s) => {
        if (!s.groups.some((g) => g.name === name && g.blueprint !== blueprint))
          return {};
        return {
          groups: s.groups.map((g) => (g.name === name ? { ...g, blueprint } : g)),
          dirty: true,
        };
      }),

    setCellGroup: (cellId, groupName) =>
      withHistory((s) => {
        const cell = s.cells[cellId];
        if (!cell) return {};
        const group = groupName && groupName.trim() ? groupName : undefined;
        // Accept only an existing group (or clearing to ungrouped).
        if (group && !s.groups.some((g) => g.name === group)) return {};
        if (cell.group === group) return {};
        return {
          cells: { ...s.cells, [cellId]: { ...cell, group } },
          dirty: true,
        };
      }),
  };
};
