/**
 * Cellbuilder SYSTEMS slice.
 *
 * Owns: the logical service runs (piping / duct / cable / electrical) and their
 * endpoint connections — equipment ports or site terminals — which the compiler
 * renders as routed pipes and cables. Undoable alongside the cells they link.
 */

import type {BuilderSystem, SystemConnection, SystemType} from "./types";
import type {CellBuilderSlice} from "./state";
import {nextId} from "./shared";
import {makeWithHistory} from "./historySlice";

export interface SystemsSlice {
  /** Logical service runs (rendered as routed pipes/cables by the compiler). */
  systems: Record<string, BuilderSystem>;
  addSystem: (
    type: SystemType,
    opts?: { name?: string; medium?: string | null },
  ) => void;
  updateSystem: (id: string, patch: Partial<BuilderSystem>) => void;
  removeSystem: (id: string) => void;
  addSystemConnection: (id: string, conn: SystemConnection) => void;
  removeSystemConnection: (id: string, index: number) => void;
  /** Systems whose connections reference the given equipment name. */
  systemsForEquipment: (equipmentName: string) => BuilderSystem[];
}

export const createSystemsSlice: CellBuilderSlice<SystemsSlice> = (set, get) => {
  const withHistory = makeWithHistory(set);

  return {
    systems: {},
    addSystem: (type, opts) =>
      withHistory((s) => {
        const id = nextId();
        const count = Object.keys(s.systems).length + 1;
        const prefix = {
          piping: "PIPE",
          duct: "DUCT",
          cable: "CABLE",
          electrical: "POWER",
        }[type];
        const name =
          opts?.name ?? `${prefix}_${String(count).padStart(2, "0")}`;
        const system: BuilderSystem = { id, name, type, connections: [] };
        if (opts?.medium) system.medium = opts.medium;
        return { systems: { ...s.systems, [id]: system }, dirty: true };
      }),
    updateSystem: (id, patch) =>
      withHistory((s) => {
        const cur = s.systems[id];
        if (!cur) return {};
        return {
          systems: { ...s.systems, [id]: { ...cur, ...patch } },
          dirty: true,
        };
      }),
    removeSystem: (id) =>
      withHistory((s) => {
        if (!s.systems[id]) return {};
        const systems = { ...s.systems };
        delete systems[id];
        return { systems, dirty: true };
      }),
    addSystemConnection: (id, conn) =>
      withHistory((s) => {
        const cur = s.systems[id];
        if (!cur) return {};
        return {
          systems: {
            ...s.systems,
            [id]: { ...cur, connections: [...cur.connections, conn] },
          },
          dirty: true,
        };
      }),
    removeSystemConnection: (id, index) =>
      withHistory((s) => {
        const cur = s.systems[id];
        if (!cur) return {};
        return {
          systems: {
            ...s.systems,
            [id]: {
              ...cur,
              connections: cur.connections.filter((_, i) => i !== index),
            },
          },
          dirty: true,
        };
      }),
    systemsForEquipment: (equipmentName) =>
      Object.values(get().systems).filter((sys) =>
        sys.connections.some((c) => c.equipment === equipmentName),
      ),

  };
};
