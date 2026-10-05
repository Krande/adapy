// The tree sets of the collection the Sources tab shows, and which one narrows the tree.
//
// The sets are the scope's (`@/services/treeSets`); WHICH ONE IS ON is this viewer's own --
// someone looking at "Alt design" does not switch everyone else's tree -- and is remembered per
// scope and collection in this browser, best effort.

import { create } from "zustand";

import { newSetId, type ProviderChoice, type TreeSet, type TreeSetMember, type TreeSetsChange } from "@/assets/treeSets";
import { changeSets, readSetsDoc } from "@/services/treeSets";

const ACTIVE_KEY_PREFIX = "ada.assets.treeSet:";

function activeKey(scope: string, collection: string): string {
  return `${ACTIVE_KEY_PREFIX}${scope}:${collection}`;
}

function readActive(scope: string, collection: string): string | null {
  try {
    return globalThis.localStorage?.getItem(activeKey(scope, collection)) || null;
  } catch {
    return null;
  }
}

function writeActive(scope: string, collection: string, id: string | null): void {
  try {
    if (id) globalThis.localStorage?.setItem(activeKey(scope, collection), id);
    else globalThis.localStorage?.removeItem(activeKey(scope, collection));
  } catch {
    // A storage that throws (a private window) just forgets the choice.
  }
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

interface TreeSetsState {
  /** What `sets` belong to; null until loaded. */
  scope: string | null;
  collection: string | null;
  sets: readonly TreeSet[];
  /** The set narrowing the tree; null draws everything. */
  activeId: string | null;
  loading: boolean;
  busy: boolean;
  error: string | null;

  load: (scope: string, collection: string) => Promise<void>;
  setActive: (id: string | null) => void;
  create: (name: string, members: readonly TreeSetMember[], createdBy?: string | null) => Promise<TreeSet | null>;
  rename: (id: string, name: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  addMembers: (id: string, members: readonly TreeSetMember[]) => Promise<void>;
  removeMembers: (id: string, memberIds: readonly string[]) => Promise<void>;
  /** Whose geometry members load (`null` is every provider), all in one write. */
  setProviders: (id: string, choices: readonly ProviderChoice[]) => Promise<void>;
}

export const useTreeSetsStore = create<TreeSetsState>((set, get) => {
  /** Run one change against the latest stored document; what was written replaces the list. */
  const mutate = async (change: TreeSetsChange): Promise<boolean> => {
    const { scope, collection, busy } = get();
    if (!scope || !collection || busy) return false;
    set({ busy: true, error: null });
    try {
      const doc = await changeSets(scope, collection, change);
      // The collection changed while this was in flight: the newer load owns the list.
      if (get().scope !== scope || get().collection !== collection) return true;
      const activeId = doc.sets.some((s) => s.id === get().activeId) ? get().activeId : null;
      set({ sets: doc.sets, activeId, busy: false });
      return true;
    } catch (e) {
      set({ busy: false, error: `could not save the sets: ${message(e)}` });
      return false;
    }
  };
  const now = () => new Date().toISOString();

  return {
    scope: null,
    collection: null,
    sets: [],
    activeId: null,
    loading: false,
    busy: false,
    error: null,

    load: async (scope, collection) => {
      const same = get().scope === scope && get().collection === collection;
      set({
        loading: true,
        error: null,
        ...(same ? {} : { scope, collection, sets: [], activeId: readActive(scope, collection) }),
      });
      try {
        const doc = await readSetsDoc(scope, collection);
        if (get().scope !== scope || get().collection !== collection) return;
        // A remembered set that is gone now draws everything, rather than nothing.
        const activeId = doc.sets.some((s) => s.id === get().activeId) ? get().activeId : null;
        set({ sets: doc.sets, activeId, loading: false });
      } catch (e) {
        if (get().scope !== scope || get().collection !== collection) return;
        set({ loading: false, error: `could not read the sets: ${message(e)}` });
      }
    },

    setActive: (id) => {
      const { scope, collection } = get();
      set({ activeId: id });
      if (scope && collection) writeActive(scope, collection, id);
    },

    create: async (name, members, createdBy) => {
      const trimmed = name.trim();
      if (!trimmed) return null;
      const at = now();
      const created: TreeSet = {
        id: newSetId(),
        name: trimmed,
        created_at: at,
        ...(createdBy ? { created_by: createdBy } : {}),
        members: [...members],
      };
      return (await mutate({ kind: "put", set: created })) ? created : null;
    },
    rename: async (id, name) => {
      if (name.trim()) await mutate({ kind: "rename", id, name, at: now() });
    },
    remove: async (id) => {
      await mutate({ kind: "delete", id });
    },
    addMembers: async (id, members) => {
      if (members.length) await mutate({ kind: "add", id, members, at: now() });
    },
    removeMembers: async (id, memberIds) => {
      if (memberIds.length) await mutate({ kind: "remove", id, memberIds, at: now() });
    },
    setProviders: async (id, choices) => {
      if (choices.length) await mutate({ kind: "providers", id, choices, at: now() });
    },
  };
});
