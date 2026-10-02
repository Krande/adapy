// The saved groups of the current scope, as the Scene panel lists them. Thin: every change goes
// through `changeGroups` (read the latest document, apply, write) and the list shown is whatever
// that wrote -- so other people's groups appear on the next change or reload, and none is lost.

import { create } from "zustand";

import { changeGroups, readGroupsDoc } from "@/services/savedGroups";
import { newGroupId, type GroupMember, type SavedGroup } from "@/utils/groups/savedGroups";

interface SavedGroupsState {
  /** The scope `groups` belong to; null until loaded. */
  scope: string | null;
  groups: readonly SavedGroup[];
  loading: boolean;
  /** Why the document could not be read or written. A change re-reads before it writes, so a
   *  failed read here never lets one overwrite what could not be read. */
  error: string | null;
  busy: boolean;

  load: (scope: string) => Promise<void>;
  create: (scope: string, name: string, members: readonly GroupMember[], createdBy?: string | null) => Promise<SavedGroup | null>;
  rename: (scope: string, id: string, name: string) => Promise<void>;
  remove: (scope: string, id: string) => Promise<void>;
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export const useSavedGroupsStore = create<SavedGroupsState>((set, get) => {
  /** Run one change; the stored result replaces the list. */
  const mutate = async (scope: string, run: () => Promise<readonly SavedGroup[]>): Promise<boolean> => {
    if (get().busy) return false;
    set({ busy: true, error: null });
    try {
      const groups = await run();
      set({ groups, scope, busy: false });
      return true;
    } catch (e) {
      set({ busy: false, error: message(e) });
      return false;
    }
  };

  return {
    scope: null,
    groups: [],
    loading: false,
    error: null,
    busy: false,

    load: async (scope) => {
      set({ loading: true, error: null, ...(get().scope !== scope ? { groups: [], scope } : {}) });
      try {
        const doc = await readGroupsDoc(scope);
        // A scope switch while this was in flight: the newer load owns the list.
        if (get().scope !== scope) return;
        set({ groups: doc.groups, loading: false });
      } catch (e) {
        if (get().scope !== scope) return;
        set({ loading: false, error: `could not read the saved groups: ${message(e)}` });
      }
    },

    create: async (scope, name, members, createdBy) => {
      const trimmed = name.trim();
      if (!trimmed || members.length === 0) return null;
      const now = new Date().toISOString();
      const group: SavedGroup = {
        id: newGroupId(),
        name: trimmed,
        created_at: now,
        ...(createdBy ? { created_by: createdBy } : {}),
        members: [...members],
      };
      const ok = await mutate(scope, async () => (await changeGroups(scope, { kind: "put", group })).groups);
      return ok ? group : null;
    },

    rename: async (scope, id, name) => {
      if (!name.trim()) return;
      await mutate(scope, async () =>
        (await changeGroups(scope, { kind: "rename", id, name, at: new Date().toISOString() })).groups,
      );
    },

    remove: async (scope, id) => {
      await mutate(scope, async () => (await changeGroups(scope, { kind: "delete", id })).groups);
    },
  };
});
