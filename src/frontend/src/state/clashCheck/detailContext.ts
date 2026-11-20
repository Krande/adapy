// What a kept detail model was made from, rebuilt for its "Load" (Connections > Review, "Detail
// models"): the clash check or convex-contact run behind it -- its target and its result, so the
// Clashes tab and the markers come back as they were after the run -- and the elements that were
// checked, as members to load into the scene.
//
// A detail model's `source` names the result (`result_key`) or the run (`run_key`). The rest is read
// from those: a result's `provenance` says what was checked (a node's provider and revision, a
// group's members, as the check resolved them), a run carries its target and, for a group, the
// group it measured. Pure: what it reads comes through `DetailContextDeps`.

import type { ClashCheckTarget, WireDetailModel } from "@/services/api/clashCheck";
import { contactRunToResult } from "./realDeps";
import { parseClashResult, type ClashResult } from "./result";
import type { ContactRunDoc } from "@/services/clash/contactRuns";
import type { GroupMember, SavedGroup } from "@/utils/groups/savedGroups";

export interface DetailContextDeps {
  /** A stored clash result document (`ada.clash/result`). */
  readResult(scope: string, key: string): Promise<unknown>;
  /** A stored convex-contact run. */
  readRun(scope: string, key: string): Promise<ContactRunDoc>;
  /** A saved group of the scope by id, else by name; null when there is none. */
  findGroup(scope: string, by: { id?: string | null; name?: string | null }): Promise<SavedGroup | null>;
}

/** The check a detail model came from, as the Clashes tab holds one, and what to load for it. */
export interface DetailContext {
  /** What was checked, for `setGroupTarget` / `setAssetTarget` / `setSource`. */
  target: ClashCheckTarget;
  /** The panel's name for it. */
  label: string;
  result: ClashResult;
  /** The result's key (a clash check), or the run's (a convex-contact run). */
  derivedKey: string | null;
  runKey: string | null;
  /** The checked elements, loadable as a group's members are; empty when none could be named. */
  members: readonly GroupMember[];
  /** What could not be brought back, in a sentence each. */
  notes: readonly string[];
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

function asMembers(raw: unknown): GroupMember[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (m): m is GroupMember =>
      !!m && typeof m === "object" && !!(m as GroupMember).target && typeof (m as GroupMember).target.kind === "string",
  );
}

/** A whole stored file as a member: its root row, which loads the model. */
export function fileMember(sourceKey: string): GroupMember {
  return { target: { kind: "file", source_key: sourceKey }, element: null, path: [] };
}

/** A whole published node as a member. */
export function nodeMember(t: { provider: string; collection: string; subject: string; revision?: string | null; node?: string | null }): GroupMember {
  return {
    target: {
      kind: "node",
      provider: t.provider,
      collection: t.collection,
      subject: t.subject,
      revision: t.revision ?? null,
      node: t.node ?? null,
    },
    element: null,
    path: [],
  };
}

async function fromResult(
  deps: DetailContextDeps,
  scope: string,
  source: WireDetailModel["source"],
  resultKey: string,
): Promise<DetailContext> {
  const result = parseClashResult(await deps.readResult(scope, resultKey));
  const prov = result.provenance;
  const target = (source.target ?? {}) as Record<string, unknown>;
  const notes: string[] = [];
  const base = { result, derivedKey: resultKey, runKey: null };

  // A group: the members as the check resolved them, else the saved group of that name now.
  const group = prov.group && typeof prov.group === "object" ? (prov.group as Record<string, unknown>) : null;
  if (target.kind === "group" || group) {
    const name = str(group?.name) ?? str(target.name) ?? str(source.label) ?? "group";
    let members = asMembers(group?.members);
    let id: string | null = null;
    if (members.length === 0) {
      const saved = await deps.findGroup(scope, { name });
      if (saved) {
        members = [...saved.members];
        id = saved.id;
      } else {
        notes.push(`The group "${name}" is no longer saved in this scope, so its members were not loaded.`);
      }
    }
    return { ...base, target: { kind: "group", name, members, ...(id ? { id } : {}) }, label: `group ${name}`, members, notes };
  }

  // A node: the provider is in the provenance (the route resolved it from the node's manifest).
  const collection = str(prov.collection) ?? str(target.collection);
  const subject = str(prov.subject) ?? str(target.subject);
  if (target.kind === "node" || (collection && subject)) {
    if (!collection || !subject) throw new Error("the checked node is not named in the result");
    const provider = str(prov.provider);
    const revision = str(prov.revision);
    const node = str(prov.node) ?? str(target.node);
    if (!provider) notes.push(`Who publishes ${subject} is not recorded, so it was not loaded: load it from Sources.`);
    return {
      ...base,
      target: { kind: "node", collection, subject, revision, node, provider },
      label: node ?? subject,
      members: provider ? [nodeMember({ provider, collection, subject, revision, node })] : [],
      notes,
    };
  }

  const sourceKey = str(source.source_key) ?? result.sourceKey;
  if (!sourceKey) throw new Error("the checked file is not named in the result");
  return { ...base, target: { kind: "file", sourceKey }, label: sourceKey, members: [fileMember(sourceKey)], notes };
}

async function fromRun(deps: DetailContextDeps, scope: string, runKey: string): Promise<DetailContext> {
  const run = await deps.readRun(scope, runKey);
  const result = contactRunToResult(run, runKey);
  const base = { result, derivedKey: null, runKey };
  const t = run.target;
  if (t.kind === "group") {
    let group: { id: string; name: string; members: readonly GroupMember[] } | null = run.group ?? null;
    const notes: string[] = [];
    if (!group?.members?.length) {
      const saved = await deps.findGroup(scope, { id: t.group_id });
      group = saved ? { id: saved.id, name: saved.name, members: saved.members } : null;
      if (!group) notes.push("The group this run measured is no longer saved in this scope, so its members were not loaded.");
    }
    const name = group?.name ?? t.group_id;
    const members = [...(group?.members ?? [])];
    return { ...base, target: { kind: "group", name, members, id: t.group_id }, label: `group ${name}`, members, notes };
  }
  if (t.kind === "node") {
    // A run records the node, not who publishes it: what it measured is its stored GLB, which is
    // loaded instead when the node is not in the scene already.
    return {
      ...base,
      target: { kind: "node", collection: t.collection, subject: t.subject, revision: t.revision ?? null, node: t.node ?? null },
      label: t.node ?? t.subject,
      members: run.geometry?.key ? [fileMember(run.geometry.key)] : [],
      notes: run.geometry?.key ? [] : [`Who publishes ${t.subject} is not recorded, so it was not loaded: load it from Sources.`],
    };
  }
  return { ...base, target: { kind: "file", sourceKey: t.source_key }, label: t.source_key, members: [fileMember(t.source_key)], notes: [] };
}

/** The check `model` was detailed from. Throws when its result or run cannot be read. */
export async function detailContext(deps: DetailContextDeps, scope: string, model: WireDetailModel): Promise<DetailContext> {
  const source = model.source;
  if (source.kind === "run" && source.run_key) return fromRun(deps, scope, source.run_key);
  if (source.result_key) return fromResult(deps, scope, source, source.result_key);
  throw new Error("it does not say which clash check it was made from");
}
