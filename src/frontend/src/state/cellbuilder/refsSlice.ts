/**
 * Cellbuilder BUILDER REFS slice.
 *
 * Owns: the boxes plugins show among the builder's cells for rows of their own
 * (a docked UI's supports or modifications, say), as `refs`, and the actions
 * that hand an edit of one back to the plugin that showed it. A ref is a
 * PROJECTION, not stored state: each registered provider projects its refs from
 * the doc's extras and the cells (`project`), and `refs` is recomputed whenever
 * either changes (store.ts). An edit goes to the provider, which writes its own
 * doc key (`setDocExtra`); the projection then follows. So undo and redo need
 * nothing here: the extras ride in the model snapshot.
 *
 * Refs are drawn, picked, moved and resized like cells, but they are no cells:
 * not snap targets, and never in the compile, the doc, containment or bounds.
 */

import type {Vec3} from "@/utils/cellbuilder/snap";
import type {BuilderCell} from "./types";
import type {CellBuilderSlice} from "./state";

/** What a user may do to a ref, per ref. */
export interface BuilderRefEdit {
  move: boolean;
  resize: boolean;
  rename: boolean;
  remove: boolean;
  /** The provider draws the ref's whole form in the Properties panel (a section of its own, on
   *  `PropertyItemLayout`): core then draws nothing for the ref item. Default false: core's
   *  minimal form (its kind, name, box where it may move or resize, where it is). */
  form?: boolean;
}

/** One box a plugin shows in the builder for a row of its own. */
export interface BuilderRef {
  /** `owner:key`, unique in the builder. */
  id: string;
  /** The provider that projected it. */
  owner: string;
  /** The provider's own key for the row. */
  key: string;
  name: string;
  /** What kind of row it is, in words ("Support", "Modification"). */
  kindLabel: string;
  /** The box, in the model frame the cells use. */
  origin: Vec3;
  size: Vec3;
  /** The structure (group) it belongs to, or "*" for every structure. */
  structure?: string;
  /** The space cell it belongs to, by name. */
  space?: string;
  /** Whether the build takes it (drawn faint when not). */
  include: boolean;
  /** How it is drawn: a box, a face (the provider gives a thin box) or a space-like volume. */
  look: "box" | "face" | "space";
  edit: BuilderRefEdit;
  /** A CSS colour; the builder's ref colour when absent. */
  color?: string;
  /** What the builder's info label (I) shows under its kind and name, a line each (its
   *  size when absent). The provider words it: core knows nothing of the row. */
  info?: string[];
}

/** What `project` returns for one ref: the ref without its id and owner, which the builder
 *  gives it; `include`, `look` and `edit` default to true, "box" and nothing editable. */
export type BuilderRefSpec = Omit<BuilderRef, "id" | "owner" | "include" | "look" | "edit"> & {
  include?: boolean;
  look?: BuilderRef["look"];
  edit?: Partial<BuilderRefEdit>;
};

/** A plugin's source of refs (`registerBuilderRefs`). The provider does the writes: each
 *  updates its own doc key (`setDocExtra`), and the refs are projected again from it. */
export interface BuilderRefProvider {
  /** Names the provider; a ref's id is `owner:key`. Registering an owner again replaces it. */
  owner: string;
  /** The refs for this doc. Called when the doc's extras or the cells change, not otherwise. */
  project(docExtras: Readonly<Record<string, unknown>>, cells: Readonly<Record<string, BuilderCell>>): BuilderRefSpec[];
  setIncluded?(ref: BuilderRef, on: boolean): void;
  rename?(ref: BuilderRef, name: string): void;
  remove?(ref: BuilderRef): void;
  /** The ref's new box, from a move or a resize; false when the provider refuses it. */
  write?(ref: BuilderRef, box: {origin: Vec3; size: Vec3}): boolean;
  /** A space cell is being renamed from `from` to `to`: the doc keys of the provider's own rows
   *  that name it, rewritten (`{key: value}`), or null when none does. Applied in the rename's own
   *  undo step (cellsSlice `renameCell`), so one undo takes back both. Keys the builder owns are
   *  ignored. */
  renameSpace?(from: string, to: string, docExtras: Readonly<Record<string, unknown>>): Record<string, unknown> | null;
}

interface ProviderEntry {
  provider: BuilderRefProvider;
  /** The last projection and what it was made from: the same inputs project the same refs. */
  memo: {docExtras: unknown; cells: unknown; refs: BuilderRef[]} | null;
}

const providers = new Map<string, ProviderEntry>();

/** The refs' id: the owner and the provider's key. */
export const builderRefId = (owner: string, key: string): string => `${owner}:${key}`;

const NO_EDIT: BuilderRefEdit = {move: false, resize: false, rename: false, remove: false};

/** A provider's spec made a ref. A provider that offers no write can not move or resize one,
 *  whatever its spec says, and likewise for rename and remove. */
function toRef(provider: BuilderRefProvider, spec: BuilderRefSpec): BuilderRef {
  const edit = {...NO_EDIT, ...spec.edit};
  return {
    ...spec,
    id: builderRefId(provider.owner, spec.key),
    owner: provider.owner,
    origin: [...spec.origin] as Vec3,
    size: [...spec.size] as Vec3,
    include: spec.include ?? true,
    look: spec.look ?? "box",
    edit: {
      move: edit.move && !!provider.write,
      resize: edit.resize && !!provider.write,
      rename: edit.rename && !!provider.rename,
      remove: edit.remove && !!provider.remove,
      ...(edit.form ? {form: true} : {}),
    },
  };
}

function projectOne(entry: ProviderEntry, docExtras: Record<string, unknown>, cells: Record<string, BuilderCell>): BuilderRef[] {
  const m = entry.memo;
  if (m && m.docExtras === docExtras && m.cells === cells) return m.refs;
  let refs: BuilderRef[] = [];
  try {
    refs = entry.provider.project(docExtras, cells).map((spec) => toRef(entry.provider, spec));
  } catch (err) {
    // One provider's failure must not fail the store's set, nor hide another's refs.
    console.warn(`[builder refs] "${entry.provider.owner}" project() threw -- no refs from it`, err);
  }
  entry.memo = {docExtras, cells, refs};
  return refs;
}

let lastParts: BuilderRef[][] = [];
let lastRefs: Record<string, BuilderRef> = {};

/** Every provider's refs for this doc and these cells, by id. The same object while no
 *  provider's projection changed, so a store listener sees no change. */
export function projectBuilderRefs(
  docExtras: Record<string, unknown>,
  cells: Record<string, BuilderCell>,
): Record<string, BuilderRef> {
  const parts = [...providers.values()].map((e) => projectOne(e, docExtras, cells));
  if (parts.length === lastParts.length && parts.every((p, i) => p === lastParts[i])) return lastRefs;
  const out: Record<string, BuilderRef> = {};
  for (const part of parts) for (const ref of part) out[ref.id] = ref;
  lastParts = parts;
  lastRefs = out;
  return out;
}

/** Add (or replace) a provider; returns the removal. The store is not told here: the caller
 *  (plugins/builderRefs.ts) refreshes it. */
export function addBuilderRefProvider(provider: BuilderRefProvider): () => boolean {
  const entry: ProviderEntry = {provider, memo: null};
  providers.set(provider.owner, entry);
  return () => {
    if (providers.get(provider.owner) !== entry) return false;
    providers.delete(provider.owner);
    return true;
  };
}

/** The doc's extras with every provider's rows that name the space `from` rewritten to `to`
 *  (`renameSpace`); the same object when none names it. `owned`: the keys the builder owns, which a
 *  provider may not write. */
export function extrasForSpaceRename(
  docExtras: Record<string, unknown>,
  from: string,
  to: string,
  owned: ReadonlySet<string>,
): Record<string, unknown> {
  let out = docExtras;
  for (const {provider} of providers.values()) {
    if (!provider.renameSpace) continue;
    let patch: Record<string, unknown> | null = null;
    try {
      patch = provider.renameSpace(from, to, out);
    } catch (err) {
      console.warn(`[builder refs] "${provider.owner}" renameSpace() threw -- its rows keep the old name`, err);
    }
    for (const [key, value] of Object.entries(patch ?? {})) {
      if (owned.has(key) || out[key] === value) continue;
      if (out === docExtras) out = {...docExtras};
      out[key] = value;
    }
  }
  return out;
}

/** The provider that projected `ref`. */
export function builderRefProvider(ref: Pick<BuilderRef, "owner">): BuilderRefProvider | undefined {
  return providers.get(ref.owner)?.provider;
}

export interface RefsSlice {
  /** The plugins' refs, by id: projected from `docExtras` and `cells`, never set directly. */
  refs: Record<string, BuilderRef>;
  /** Project the refs again (a provider came or went). */
  refreshRefs: () => void;
  /** Hand an edit of a ref to its provider. Each does nothing when the ref is gone or the
   *  provider offers no such edit; `writeRef` returns whether the provider took the box. */
  setRefIncluded: (refId: string, on: boolean) => void;
  renameRef: (refId: string, name: string) => void;
  removeRef: (refId: string) => void;
  writeRef: (refId: string, box: {origin: Vec3; size: Vec3}) => boolean;
}

export const createRefsSlice: CellBuilderSlice<RefsSlice> = (set, get) => {
  const withProvider = <T>(refId: string, run: (ref: BuilderRef, p: BuilderRefProvider) => T, none: T): T => {
    const ref = get().refs[refId];
    const p = ref ? builderRefProvider(ref) : undefined;
    return ref && p ? run(ref, p) : none;
  };
  return {
    refs: {},
    refreshRefs: () => {
      const s = get();
      const refs = projectBuilderRefs(s.docExtras, s.cells);
      const sel = s.selection;
      const selGone = sel?.kind === "ref" && !refs[sel.refId];
      if (refs === s.refs && !selGone) return;
      set({refs, ...(selGone ? {selection: null, gizmoMode: "none" as const, gizmoAxisLock: null} : {})});
    },
    setRefIncluded: (refId, on) =>
      withProvider(refId, (ref, p) => {
        if (ref.include !== on) p.setIncluded?.(ref, on);
      }, undefined),
    renameRef: (refId, name) =>
      withProvider(refId, (ref, p) => {
        const trimmed = name.trim();
        if (ref.edit.rename && trimmed && trimmed !== ref.name) p.rename?.(ref, trimmed);
      }, undefined),
    removeRef: (refId) =>
      withProvider(refId, (ref, p) => {
        // The pick of it goes with the projection (store.ts).
        if (ref.edit.remove) p.remove?.(ref);
      }, undefined),
    writeRef: (refId, box) =>
      withProvider(refId, (ref, p) => {
        if (!(ref.edit.move || ref.edit.resize) || !p.write) return false;
        return p.write(ref, {origin: [...box.origin] as Vec3, size: [...box.size] as Vec3});
      }, false),
  };
};

/** Remove the provider of `owner`; whether there was one. */
export function removeBuilderRefProvider(owner: string): boolean {
  return providers.delete(owner);
}
