// The saved groups of the current scope, as the Scene panel lists them. Thin: every change goes
// through `changeGroups` (read the latest document, apply, write) and the list shown is whatever
// that wrote -- so other people's groups appear on the next change or reload, and none is lost.

import { create } from "zustand";

import { realSourceRefDeps } from "@/services/groupSourceDeps";
import { changeGroups, readGroupsDoc } from "@/services/savedGroups";
import { attachMemberSources } from "@/utils/groups/groupSourceRefs";
import { addMembersToGroup, memberKey, newGroupId, type GroupMember, type SavedGroup } from "@/utils/groups/savedGroups";

/** `members` with their source references recorded (`attachMemberSources`) -- before they are
 *  written, so a group refers to the source geometry its members were picked from, not to the GLB the
 *  viewer drew. Without a server there is nothing to refer to, and they are written as they are. */
async function withSources(scope: string, members: readonly GroupMember[]): Promise<GroupMember[]> {
  const deps = realSourceRefDeps(scope);
  return deps ? attachMemberSources(members, deps) : [...members];
}

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
  /** Append `members` to the group `groupId`, skipping those it holds already. The result, or
   *  null when the write failed (`error` says why). `added: 0` wrote nothing. */
  addMembers: (scope: string, groupId: string, members: readonly GroupMember[]) => Promise<{ group: SavedGroup; added: number } | null>;
  rename: (scope: string, id: string, name: string) => Promise<void>;
  remove: (scope: string, id: string) => Promise<void>;
  /** Record the source of every member of `groupId` that has none yet -- a group saved before
   *  sources were kept. Resolves how many members gained one (null: the write failed). */
  recordSources: (scope: string, groupId: string) => Promise<number | null>;
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
      let group: SavedGroup | null = null;
      const ok = await mutate(scope, async () => {
        group = {
          id: newGroupId(),
          name: trimmed,
          created_at: now,
          ...(createdBy ? { created_by: createdBy } : {}),
          members: await withSources(scope, members),
        };
        return (await changeGroups(scope, { kind: "put", group })).groups;
      });
      return ok ? group : null;
    },

    addMembers: async (scope, groupId, members) => {
      let out: { group: SavedGroup; added: number } | null = null;
      const ok = await mutate(scope, async () => {
        const sourced = await withSources(scope, members);
        // Merged into the group as stored NOW, not as this list last showed it: a `put` replaces
        // the whole group, and members someone else added meanwhile must survive it.
        const latest = await readGroupsDoc(scope);
        const current = latest.groups.find((g) => g.id === groupId);
        if (!current) throw new Error("that group no longer exists -- someone may have deleted it");
        const merged = addMembersToGroup(current, sourced, new Date().toISOString());
        out = merged;
        if (merged.added === 0) return latest.groups;
        return (await changeGroups(scope, { kind: "put", group: merged.group })).groups;
      });
      return ok ? out : null;
    },

    recordSources: async (scope, groupId) => {
      let gained = 0;
      const ok = await mutate(scope, async () => {
        const latest = await readGroupsDoc(scope);
        const current = latest.groups.find((g) => g.id === groupId);
        if (!current) throw new Error("that group no longer exists -- someone may have deleted it");
        const missing = current.members.filter((m) => !m.source).length;
        if (missing === 0) return latest.groups;
        const sourced = new Map((await withSources(scope, current.members)).map((m) => [memberKey(m), m.source]));
        // Merged into the group as stored AFTER the reads, which take a while: members someone
        // added or removed meanwhile stay as they are now.
        const now = (await readGroupsDoc(scope)).groups.find((g) => g.id === groupId);
        if (!now) throw new Error("that group no longer exists -- someone may have deleted it");
        const members = now.members.map((m) => {
          const source = m.source ?? sourced.get(memberKey(m));
          if (source && !m.source) gained++;
          return source ? { ...m, source } : m;
        });
        if (gained === 0) return latest.groups;
        const group: SavedGroup = { ...now, members, updated_at: new Date().toISOString() };
        return (await changeGroups(scope, { kind: "put", group })).groups;
      });
      return ok ? gained : null;
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
